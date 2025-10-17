"""Smoke check for the hunting store and the classical-ML stack (ADR 0004, ADR 0005).

1. DuckDB: create the hunting table, bulk insert from Python rows, a time-bucketed analytic query with a
   window function, a JSON field extraction, a parameterised query, a read-only second connection, and
   Parquet export/import (used for training snapshots)
2. scikit-learn IsolationForest + robust z-score on synthetic per-user login features (precision@k)
3. LightGBM on a synthetic triage set vs a logistic baseline (PR-AUC), with isotonic calibration and
   SHAP contributions (LightGBM needs libomp on macOS)
4. LiteLLM imports (offline fake is the default; no network call is made)

  python scripts/smoke/store_ml_smoke.py
"""

from __future__ import annotations

import os
import resource
import tempfile
import time
import warnings
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # never fetch the price map at import
warnings.filterwarnings("ignore", message="LightGBM binary classifier with TreeExplainer")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(1)


def duckdb_smoke() -> None:
    import duckdb

    print(f"duckdb {duckdb.__version__}")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hunt.duckdb"
        con = duckdb.connect(str(path))
        con.execute(
            """
            CREATE TABLE events (
              org_id UUID, event_id VARCHAR, ts TIMESTAMPTZ, dataset VARCHAR, category VARCHAR, action VARCHAR,
              outcome VARCHAR, host_name VARCHAR, user_name VARCHAR, source_ip VARCHAR, destination_ip VARCHAR,
              process_name VARCHAR, command_line VARCHAR, raw JSON
            )
            """
        )
        t0 = datetime(2025, 11, 20, 0, 0, tzinfo=UTC)
        org = "6f1c2c1e-0000-4000-8000-000000000001"
        rows = [
            (
                org,
                f"e{i}",
                t0 + timedelta(seconds=7 * i),
                "windows.security",
                "authentication",
                "logon-failed" if i % 9 == 0 else "logon",
                "failure" if i % 9 == 0 else "success",
                f"ant-ws-{i % 12:02d}",
                f"user{i % 30}",
                f"10.20.{i % 4}.{i % 250}",
                None,
                None,
                None,
                f'{{"LogonType": {3 if i % 2 else 10}, "geo": {{"country": "{"BE" if i % 5 else "NG"}"}}}}',
            )
            for i in range(50_000)
        ]
        start = time.perf_counter()
        con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows[:2000])
        slow = time.perf_counter() - start
        import pandas as pd

        df = pd.DataFrame(  # noqa: F841 - read by DuckDB (replacement scan)
            rows[2000:], columns=[d[0] for d in con.execute("SELECT * FROM events LIMIT 0").description]
        )
        start = time.perf_counter()
        con.execute("INSERT INTO events SELECT * FROM df")
        fast = time.perf_counter() - start
        total = con.execute("SELECT count(*) FROM events").fetchone()[0]
        check("bulk insert", total == 50_000, f"executemany 2k rows {slow:.2f}s · DataFrame 48k rows {fast:.2f}s")

        q = """
            WITH hourly AS (
              SELECT time_bucket(INTERVAL '1 hour', ts) AS hour, host_name,
                     count(*) FILTER (WHERE outcome = 'failure') AS failures, count(*) AS total
              FROM events WHERE org_id = ? AND ts >= ?
              GROUP BY ALL
            )
            SELECT *, avg(failures) OVER (PARTITION BY host_name ORDER BY hour
                                          ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS trailing_avg
            FROM hourly ORDER BY failures DESC LIMIT 5
        """
        start = time.perf_counter()
        top = con.execute(q, [org, t0]).fetchall()
        check("analytic query (time buckets + window)", len(top) == 5, f"{(time.perf_counter() - start) * 1000:.1f} ms")
        geo = con.execute(
            "SELECT raw->>'$.geo.country' AS c, count(*) FROM events GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        check("JSON field extraction", {g[0] for g in geo} == {"BE", "NG"}, str(geo))
        pq = Path(tmp) / "snap.parquet"
        con.execute(f"COPY (SELECT * FROM events WHERE category = 'authentication') TO '{pq}' (FORMAT parquet)")
        n_pq = con.execute(f"SELECT count(*) FROM read_parquet('{pq}')").fetchone()[0]
        check("Parquet export/import", n_pq == 50_000, f"{pq.stat().st_size / 1e6:.1f} MB")
        con.close()
        ro = duckdb.connect(str(path), read_only=True)
        check("read-only reopen", ro.execute("SELECT count(*) FROM events").fetchone()[0] == 50_000)
        ro.close()
        check("file size", path.stat().st_size < 64e6, f"{path.stat().st_size / 1e6:.1f} MB for 50k events")


def ml_smoke() -> None:
    import lightgbm as lgb
    import numpy as np
    import shap
    import sklearn
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import IsolationForest
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    print(f"scikit-learn {sklearn.__version__} · lightgbm {lgb.__version__} · shap {shap.__version__}")
    rng = np.random.default_rng(7)
    # UEBA: per user-day features (logins, distinct hosts, off-hours share, new-country flag, MB out)
    normal = np.column_stack(
        [
            rng.poisson(12, 2000),
            rng.poisson(2, 2000),
            rng.beta(1, 12, 2000),
            rng.binomial(1, 0.01, 2000),
            rng.lognormal(3, 0.5, 2000),
        ]
    )
    anomalies = np.column_stack(
        [
            rng.poisson(40, 20),
            rng.poisson(9, 20),
            rng.beta(6, 2, 20),
            rng.binomial(1, 0.7, 20),
            rng.lognormal(6, 0.4, 20),
        ]
    )
    X = np.vstack([normal, anomalies])
    y = np.r_[np.zeros(len(normal)), np.ones(len(anomalies))]
    iso = IsolationForest(n_estimators=200, contamination="auto", random_state=7).fit(normal[:1500])
    score = -iso.score_samples(X)
    med = np.median(normal[:1500], axis=0)
    mad = np.median(np.abs(normal[:1500] - med), axis=0) * 1.4826
    mad = np.maximum(mad, 0.5 * normal[:1500].std(axis=0) + 1e-6)  # floor: MAD is 0 on sparse/binary features
    robust_z = np.abs((X - med) / mad).max(axis=1)
    k = 20
    p_at_k = y[np.argsort(-score)[:k]].mean()
    z_at_k = y[np.argsort(-robust_z)[:k]].mean()
    check(
        "IsolationForest precision@20 on injected anomalies", p_at_k >= 0.8, f"IF {p_at_k:.2f} · robust-z {z_at_k:.2f}"
    )

    # Triage: alert features → real (1) vs noise (0)
    n = 6000
    F = np.column_stack(
        [
            rng.integers(1, 5, n),
            rng.poisson(3, n),
            rng.binomial(1, 0.2, n),
            rng.random(n),
            rng.poisson(1, n),
            rng.binomial(1, 0.1, n),
            rng.random(n),
        ]
    )
    logit = -3 + 0.9 * F[:, 0] + 1.6 * F[:, 2] + 2.2 * (F[:, 3] > 0.8) * F[:, 5] - 0.25 * F[:, 1] + 0.8 * F[:, 4]
    Y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    tr, te = slice(0, 4500), slice(4500, n)  # stand-in for a time split
    base = LogisticRegression(max_iter=1000).fit(F[tr], Y[tr])
    gbm = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15, verbose=-1).fit(F[tr], Y[tr])
    p_base, p_gbm = base.predict_proba(F[te])[:, 1], gbm.predict_proba(F[te])[:, 1]
    cal = CalibratedClassifierCV(
        lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15, verbose=-1), method="isotonic", cv=3
    ).fit(F[tr], Y[tr])
    p_cal = cal.predict_proba(F[te])[:, 1]
    check(
        "LightGBM vs logistic baseline",
        roc_auc_score(Y[te], p_gbm) > 0.7,
        f"PR-AUC {average_precision_score(Y[te], p_base):.3f} → {average_precision_score(Y[te], p_gbm):.3f}",
    )
    check(
        "isotonic calibration",
        brier_score_loss(Y[te], p_cal) <= brier_score_loss(Y[te], p_gbm) + 0.01,
        f"Brier {brier_score_loss(Y[te], p_gbm):.4f} → {brier_score_loss(Y[te], p_cal):.4f}",
    )
    sv = shap.TreeExplainer(gbm).shap_values(F[te][:50])
    sv = sv[1] if isinstance(sv, list) else sv  # older shap returned [neg, pos]
    check("SHAP contributions", sv.shape == (50, F.shape[1]), f"shape {sv.shape}")


def litellm_smoke() -> None:
    start = time.perf_counter()
    import litellm

    check(
        "litellm imports",
        hasattr(litellm, "acompletion"),
        f"{version('litellm')} in {time.perf_counter() - start:.1f}s",
    )


def main() -> None:
    duckdb_smoke()
    ml_smoke()
    litellm_smoke()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # bytes on macOS
    print(f"  [INFO] peak RSS {peak:.0f} MB")
    print("Store + ML smoke: PASS")


if __name__ == "__main__":
    main()
