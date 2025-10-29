"""`make train` → `python -m nightjar.ml.train all`: reproducible (fixed seeds), CPU-minutes small.

UEBA
  history: 14 days of Halden background activity → per-entity profiles (days 0–6) and baseline windows
  (days 7–13) → one Isolation Forest per entity type, robust per-entity baselines (median, MAD with floor),
  the baseline score distribution for rank normalisation.
  evaluation: 2 held-out days of normal activity with injected attack activity → precision@k (k = injected
  anomalous windows), recall at the operating threshold, false-positive rate by site and staff type (fairness).

Triage classifier
  data: 60 synthetic days (randomised scenarios, benign look-alikes, background) through the real normaliser,
  detector and UEBA; label = the detection contains an attacker's event.
  split: by day in time order (train 0–39, validation 40–49, test 50–59); every scenario instance lives in one
  day, so no scenario leaks across splits.
  models: logistic regression baseline, random forest and LightGBM challengers (LightGBM tuned with Optuna on
  validation PR-AUC); isotonic calibration on validation; thresholds from validation (recall ≥ 0.98 for the
  noise threshold); test metrics: ROC-AUC, PR-AUC, Brier, ECE, false-positive reduction vs rules alone.
  explanations: LightGBM TreeSHAP contributions (global mean |SHAP| in the card, top-3 reasons per alert).
  drift: PSI per feature between training and test (monitoring baseline).
Tracking: MLflow (local SQLite + `mlruns/`, git-ignored) with a model registry entry per trained version.
Artefacts (small, committed): `ml/artifacts/` model files, metadata, model cards and the registry.
"""
# ruff: noqa: S311

from __future__ import annotations

import contextlib
import itertools
import json
import os
import random
import sys
import time
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from nightjar.detect.engine import Detector
from nightjar.ml.dataset import Row, detect_all, halden_inventory, normalise_all, rows_for
from nightjar.ml.features import (
    FEATURE_LABELS,
    HOST_FEATURES,
    TRIAGE_FEATURES,
    USER_FEATURES,
    EntityProfile,
    TriageFeaturizer,
    learn,
    window_features,
    windows_of,
)
from nightjar.ml.ueba import UebaModel, features_for, gate_open, robust_baseline
from nightjar.workers.replay import generator as gen

SEED = 20251015
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
REPO = Path(__file__).resolve().parents[3]
TRIAGE_VERSION = "1.0.0"
UEBA_VERSION = "1.0.0"
warnings.filterwarnings("ignore", category=UserWarning)
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

# Gates (also enforced by `make eval`): a model whose metrics miss these cannot be promoted.
TRIAGE_GATES = {
    "roc_auc": 0.90,
    "pr_auc": 0.85,
    "brier": 0.10,
    "ece": 0.08,
    "fp_reduction": 0.50,
    "recall_at_threshold": 0.95,
}
UEBA_GATES = {"precision_at_k": 0.80, "recall": 0.80, "precision": 0.70, "fpr_max_group_gap": 0.05}


def log(msg: str) -> None:
    print(f"[train] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# UEBA
# ─────────────────────────────────────────────────────────────────────────────
def _history_events(days: int, seed: int) -> list[gen.Ev]:
    rng = random.Random(seed)
    evs: list[gen.Ev] = []
    for d in range(days):
        start = (d - days) * gen.DAY
        evs += gen.background(rng, start, start + gen.DAY, rate=1.0, density=1.0)
        if d % 7 == 0:  # the backup agent's weekly shadow-copy check on every host is normal behaviour
            evs += gen.routine_backup_checks(rng, start + 1.2 * gen.HOUR)
    evs.sort(key=lambda e: e.t)
    return evs


def train_ueba() -> tuple[UebaModel, dict[str, Any]]:
    from sklearn.ensemble import IsolationForest

    t0 = time.perf_counter()
    hist = normalise_all(_history_events(14, SEED))
    first_half_end = (
        min(e.timestamp for e in hist.events).replace(hour=0)
        + (max(e.timestamp for e in hist.events) - min(e.timestamp for e in hist.events)) / 2
    )
    wins = windows_of(hist.events)
    profiles: dict[str, dict[str, EntityProfile]] = {"user": {}, "host": {}}
    for w in wins:
        if w.start < first_half_end:
            learn(profiles[w.entity_type].setdefault(w.entity_id, EntityProfile()), w)
    rows: dict[str, list[tuple[str, dict[str, float]]]] = {"user": [], "host": []}
    for w in wins:
        if w.start >= first_half_end:
            prof = profiles[w.entity_type].setdefault(w.entity_id, EntityProfile())
            rows[w.entity_type].append((w.entity_id, window_features(w, prof)))
            learn(prof, w)  # profiles keep growing through the baseline period
    forests, dist, baselines, population = {}, {}, {}, {}
    for et in ("user", "host"):
        names = features_for(et)
        x = np.array([[f[n] for n in names] for _, f in rows[et]], dtype=float)
        forest = IsolationForest(n_estimators=150, max_samples=256, contamination="auto", random_state=SEED).fit(x)
        forests[et] = forest
        dist[et] = sorted(float(s) for s in -forest.score_samples(x))
        per_entity: dict[str, list[dict[str, float]]] = defaultdict(list)
        for eid, f in rows[et]:
            per_entity[eid].append(f)
        baselines[et] = {
            eid: {n: robust_baseline(np.array([r[n] for r in fs]), n) for n in names}
            for eid, fs in per_entity.items()
            if len(fs) >= 10
        }
        population[et] = {n: robust_baseline(x[:, i], n) for i, n in enumerate(names)}
    model = UebaModel(UEBA_VERSION, forests, dist, baselines, population, profiles)
    info = {
        "historyEvents": len(hist.events),
        "baselineWindows": {et: len(rows[et]) for et in rows},
        "entities": {et: len(profiles[et]) for et in profiles},
        "seconds": round(time.perf_counter() - t0, 1),
    }
    log(f"UEBA trained on {info['baselineWindows']} windows in {info['seconds']}s")
    return model, info


def _ueba_eval_events(seed: int) -> list[gen.Ev]:
    """2 normal days plus injected attack activity (the anomalies to find)."""
    rng = random.Random(seed)
    evs = gen.background(rng, 0, 2 * gen.DAY, rate=1.0, density=1.0)
    builders = [
        lambda t, i: gen.phishing_credential_misuse(
            rng,
            t,
            user=rng.choice([p.username for p in gen.PEOPLE if p.kind == "employee"]),
            scenario=f"inj-ph{i}",
            segment="inj",
            items=rng.randint(40, 160),
        ),
        lambda t, i: gen.contractor_exfiltration(
            rng,
            t,
            user=rng.choice(["t.reyes", "l.peeters", "a.tanaka"]),
            workstation=rng.choice(["mem-ws-040", "ant-ws-022", "mel-ws-007"]),
            scenario=f"inj-ex{i}",
            segment="inj",
        ),
        lambda t, i: gen.ransomware_precursor(
            rng, t, beachhead=rng.choice(["mem-dispatch-01", "mem-sql-01"]), scenario=f"inj-rp{i}", segment="inj"
        ),
    ]
    for i in range(18):
        evs += rng.choice(builders)(rng.uniform(0.1, 1.9) * gen.DAY, i)
    evs.sort(key=lambda e: e.t)
    return evs


def eval_ueba(model: UebaModel, seed: int = SEED + 1) -> dict[str, Any]:
    from nightjar.workers.inmemory_platform import halden_identities

    norm = normalise_all(_ueba_eval_events(seed))
    people = {i.username: i for i in halden_identities()}
    site_of = {p.username: p.site for p in gen.PEOPLE}
    scored = []
    for w in windows_of(norm.events):
        score, _, z, feats = model.score_window(w)
        y = int(any(e.event_id in norm.malicious for e in w.events))
        flagged = model.is_anomalous(w.entity_type, score, z, feats)
        # rank by the forest score, with gated windows first (the gate is part of the detector)
        gate = gate_open(w.entity_type, feats)
        scored.append((w, score, y, flagged, gate))
    scenarios = {norm.scenario_of[i] for i in norm.malicious}
    detected = set()
    for w, _, y, f, _ in scored:
        if f and y:
            detected |= {norm.scenario_of[e.event_id] for e in w.events if e.event_id in norm.malicious}
    positives = sum(y for _, _, y, _, _ in scored)
    ranked = sorted(scored, key=lambda r: (-int(r[4]), -r[1]))
    k = len(scenarios)
    p_at_k = sum(y for _, _, y, _, _ in ranked[:k]) / k if k else 0.0
    tp = sum(1 for _, _, y, f, _ in scored if y and f)
    fp = sum(1 for _, _, y, f, _ in scored if not y and f)
    recall = len(detected) / len(scenarios) if scenarios else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    # fairness: false-positive rate on normal user windows by site and by staff type
    groups: dict[str, list[int]] = defaultdict(list)
    for w, _, y, f, _ in scored:
        if w.entity_type != "user" or y:
            continue
        ident = people.get(w.entity_id)
        groups[f"site:{site_of.get(w.entity_id, 'other')}"].append(int(f))
        groups[f"kind:{ident.kind if ident else 'service'}"].append(int(f))
    fpr = {g: round(sum(v) / len(v), 4) for g, v in groups.items() if len(v) >= 20}
    site_rates = [v for g, v in fpr.items() if g.startswith("site:")]
    gap = round(max(site_rates) - min(site_rates), 4) if site_rates else 0.0
    out = {
        "windows": len(scored),
        "injectedWindows": positives,
        "injectedSequences": len(scenarios),
        "windowRecall": round(tp / positives, 4) if positives else 0.0,
        "precision_at_k": round(p_at_k, 4),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "flagged": tp + fp,
        "fpr_by_group": fpr,
        "fpr_max_group_gap": gap,
    }
    log(
        f"UEBA eval: P@{k}={out['precision_at_k']} seq-recall={out['recall']} precision={out['precision']} "
        f"window-recall={out['windowRecall']} gap={gap}"
    )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Triage classifier
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Split:
    x: np.ndarray
    y: np.ndarray
    rows: list[Row]


def build_triage_rows(ueba: UebaModel, *, days: int = 60, seed: int = SEED) -> list[Row]:
    detector = Detector()
    featurizer = TriageFeaturizer(halden_inventory(), kev_lookup=_kev)
    rows: list[Row] = []
    t0 = time.perf_counter()
    for d, evs in gen.training_days(seed, days):
        norm = normalise_all(evs)
        dets = detect_all(norm, detector, ueba)
        rows += rows_for(norm, dets, featurizer, d)
    log(
        f"triage dataset: {len(rows)} detections from {days} days in {time.perf_counter() - t0:.1f}s "
        f"({sum(r.label for r in rows)} real); detector {detector.throughput():,.0f} events/s"
    )
    return rows


def _kev(cve: str) -> Any:
    from nightjar.intel.api import kev_lookup

    return kev_lookup(cve)


def rule_priors(rows: list[Row]) -> dict[str, float]:
    """Smoothed historical precision per rule on the training split (Beta(1,1) prior)."""
    hits: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        hits[r.detection.rule_id].append(r.label)
    return {rid: round((sum(v) + 1) / (len(v) + 2), 4) for rid, v in hits.items()}


def to_split(rows: list[Row], priors: dict[str, float]) -> Split:
    for r in rows:
        r.features["rule_prior"] = priors.get(r.detection.rule_id, r.features["rule_prior"])
    x = np.array([[r.features[f] for f in TRIAGE_FEATURES] for r in rows], dtype=float)
    y = np.array([r.label for r in rows], dtype=int)
    return Split(x, y, rows)


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in itertools.pairwise(edges):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def calibration_bins(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict[str, Any]]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i, (lo, hi) in enumerate(itertools.pairwise(edges)):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.any():
            out.append(
                {
                    "bin": i,
                    "predicted": round(float(p[m].mean()), 4),
                    "observed": round(float(y[m].mean()), 4),
                    "count": int(m.sum()),
                }
            )
    return out


def psi(a: np.ndarray, b: np.ndarray, bins: int = 10) -> float:
    qs = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)))
    if len(qs) < 3:
        qs = np.unique(np.concatenate([[a.min() - 1e-9], np.unique(a), [a.max() + 1e-9]]))
    ca = np.histogram(a, bins=qs)[0] / max(len(a), 1) + 1e-4
    cb = np.histogram(np.clip(b, qs[0], qs[-1]), bins=qs)[0] / max(len(b), 1) + 1e-4
    return float(np.sum((ca - cb) * np.log(ca / cb)))


def threshold_for_recall(y: np.ndarray, p: np.ndarray, recall: float = 0.98) -> float:
    pos = np.sort(p[y == 1])
    if len(pos) == 0:
        return 0.05
    idx = int(np.floor((1 - recall) * len(pos)))
    return float(max(1e-4, pos[idx] - 1e-6))


def fp_reduction(y: np.ndarray, p: np.ndarray, thr: float) -> tuple[float, float]:
    """(false-positive reduction vs every detection being an alert, recall of real detections kept)."""
    neg = y == 0
    kept_fp = int(((p >= thr) & neg).sum())
    total_fp = int(neg.sum())
    recall = float(((p >= thr) & (y == 1)).sum() / max((y == 1).sum(), 1))
    return (1 - kept_fp / total_fp if total_fp else 0.0), recall


def train_triage(rows: list[Row]) -> tuple[dict[str, Any], dict[str, Any]]:
    import lightgbm as lgb
    import optuna
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    train_rows = [r for r in rows if r.group < 40]
    valid_rows = [r for r in rows if 40 <= r.group < 50]
    test_rows = [r for r in rows if r.group >= 50]
    priors = rule_priors(train_rows)
    tr, va, te = to_split(train_rows, priors), to_split(valid_rows, priors), to_split(test_rows, priors)
    # Analyst labels are imperfect: flip 3 % of training labels (never validation or test).
    rng = np.random.default_rng(SEED)
    flip = rng.random(len(tr.y)) < 0.03
    y_train = np.where(flip, 1 - tr.y, tr.y)
    log(
        f"split: train {len(tr.y)} ({tr.y.mean():.2f} real) · valid {len(va.y)} · test {len(te.y)} "
        f"({te.y.mean():.2f} real); {int(flip.sum())} training labels flipped"
    )

    def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
        return {
            "roc_auc": round(float(roc_auc_score(y, p)), 4),
            "pr_auc": round(float(average_precision_score(y, p)), 4),
            "brier": round(float(brier_score_loss(y, p)), 4),
            "ece": round(ece(y, p), 4),
        }

    results: dict[str, Any] = {}
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0, random_state=SEED)).fit(tr.x, y_train)
    results["logistic"] = metrics(te.y, lr.predict_proba(te.x)[:, 1])
    rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=3, n_jobs=1, random_state=SEED).fit(tr.x, y_train)
    results["random_forest"] = metrics(te.y, rf.predict_proba(te.x)[:, 1])

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        params = {
            "objective": "binary",
            "verbosity": -1,
            "seed": SEED,
            "deterministic": True,
            "num_threads": 1,
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 4, 31),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 5, 60),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True),
        }
        rounds = trial.suggest_int("rounds", 80, 400)
        b = lgb.train(params, lgb.Dataset(tr.x, y_train), num_boost_round=rounds)
        return float(average_precision_score(va.y, b.predict(va.x)))

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=25)
    best = dict(study.best_params)
    rounds = best.pop("rounds")
    params = {"objective": "binary", "verbosity": -1, "seed": SEED, "deterministic": True, "num_threads": 1, **best}
    booster = lgb.train(params, lgb.Dataset(tr.x, y_train, feature_name=list(TRIAGE_FEATURES)), num_boost_round=rounds)
    raw_va, raw_te = booster.predict(va.x), booster.predict(te.x)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.005, y_max=0.995).fit(raw_va, va.y)
    p_va, p_te = iso.predict(raw_va), iso.predict(raw_te)
    results["lightgbm_uncalibrated"] = metrics(te.y, raw_te)
    results["lightgbm"] = metrics(te.y, p_te)
    # Noise threshold: keep ≥ 98 % of real detections on validation, but never above 0.2 (a calibrated 20 % chance
    # of being real is never "noise"); likely-real from 0.85.
    thr_noise = min(threshold_for_recall(va.y, p_va, 0.98), 0.2)
    thr_real = 0.85
    red, rec = fp_reduction(te.y, p_te, thr_noise)
    results["lightgbm"]["fp_reduction"] = round(red, 4)
    results["lightgbm"]["recall_at_threshold"] = round(rec, 4)
    for name, model in (("logistic", lr), ("random_forest", rf)):
        pv = model.predict_proba(va.x)[:, 1]
        pt = model.predict_proba(te.x)[:, 1]
        r_, c_ = fp_reduction(te.y, pt, min(threshold_for_recall(va.y, pv, 0.98), 0.2))
        results[name]["fp_reduction"] = round(r_, 4)
        results[name]["recall_at_threshold"] = round(c_, 4)
    # rules alone: every detection is an alert; a severity cut-off (medium+) as the naive alternative
    sev = np.array([r.features["rule_level"] for r in te.rows])
    r_sev, c_sev = fp_reduction(te.y, (sev >= 2).astype(float), 0.5)
    results["rules_alone"] = {"fp_reduction": 0.0, "recall_at_threshold": 1.0}
    results["severity_cutoff_medium"] = {"fp_reduction": round(r_sev, 4), "recall_at_threshold": round(c_sev, 4)}
    contrib = booster.predict(te.x, pred_contrib=True)[:, : len(TRIAGE_FEATURES)]
    importance = sorted(
        ((f, round(float(np.abs(contrib[:, i]).mean()), 4)) for i, f in enumerate(TRIAGE_FEATURES)),
        key=lambda kv: -kv[1],
    )
    drift = [{"feature": f, "psi": round(psi(tr.x[:, i], te.x[:, i]), 4)} for i, f in enumerate(TRIAGE_FEATURES)]
    gates = {k: v for k, v in results["lightgbm"].items() if k in TRIAGE_GATES}
    passed = (
        gates["roc_auc"] >= TRIAGE_GATES["roc_auc"]
        and gates["pr_auc"] >= TRIAGE_GATES["pr_auc"]
        and gates["brier"] <= TRIAGE_GATES["brier"]
        and gates["ece"] <= TRIAGE_GATES["ece"]
        and gates["fp_reduction"] >= TRIAGE_GATES["fp_reduction"]
        and gates["recall_at_threshold"] >= TRIAGE_GATES["recall_at_threshold"]
    )
    by_scenario = Counter(
        ("real" if r.label else "noise", r.scenario.split("-")[-1].rstrip("0123456789")) for r in te.rows
    )
    meta = {
        "version": TRIAGE_VERSION,
        "features": list(TRIAGE_FEATURES),
        "featureLabels": {f: FEATURE_LABELS[f] for f in TRIAGE_FEATURES},
        "calibration": {
            "x": [round(float(v), 6) for v in iso.X_thresholds_],
            "y": [round(float(v), 6) for v in iso.y_thresholds_],
        },
        "thresholdNoise": round(thr_noise, 6),
        "thresholdReal": round(thr_real, 6),
        "rulePriors": priors,
        "params": {**params, "rounds": rounds},
        "metrics": results,
        "calibrationBins": calibration_bins(te.y, p_te),
        "drift": drift,
        "importance": importance,
        "gates": TRIAGE_GATES,
        "gatesPassed": passed,
        "data": {
            "days": {"train": 40, "valid": 10, "test": 10},
            "rows": {"train": len(tr.y), "valid": len(va.y), "test": len(te.y)},
            "positiveRate": {"train": round(float(tr.y.mean()), 4), "test": round(float(te.y.mean()), 4)},
            "labelNoiseTrain": 0.03,
            "testComposition": {f"{k[0]}:{k[1]}": v for k, v in sorted(by_scenario.items())},
        },
        "optuna": {"trials": 25, "bestValidPrAuc": round(float(study.best_value), 4)},
    }
    models = {"booster": booster, "rf": rf, "lr": lr, "iso": iso, "test": te, "p_te": p_te}
    log(f"triage: LightGBM test {results['lightgbm']} · LR {results['logistic']} · RF {results['random_forest']}")
    return meta, models


# ─────────────────────────────────────────────────────────────────────────────
# Cards, registry, MLflow
# ─────────────────────────────────────────────────────────────────────────────
def triage_card(meta: dict[str, Any]) -> str:
    m = meta["metrics"]
    rows = "\n".join(
        f"| {name.replace('_', ' ')} | {v.get('roc_auc', '–')} | {v.get('pr_auc', '–')} | {v.get('brier', '–')} | "
        f"{v.get('ece', '–')} | {v.get('fp_reduction', '–')} | {v.get('recall_at_threshold', '–')} |"
        for name, v in m.items()
    )
    imp = "\n".join(f"| {meta['featureLabels'][f]} | {v} |" for f, v in meta["importance"][:10])
    drift = ", ".join(f"{d['feature']} {d['psi']}" for d in sorted(meta["drift"], key=lambda d: -d["psi"])[:5])
    return f"""# Model card: alert triage classifier v{meta["version"]}

**Purpose.** Score each detection with a calibrated probability that it reflects real attacker activity, so the
triage colleague can prioritise and a burst of known-benign alerts collapses into one likely-benign incident.
It never closes an incident on its own: the triage agent cites the score and its reasons, and people decide.

**Problem framing and metric choice.** Binary classification per detection (real vs noise). False negatives are
costly (a missed intrusion), so the operating point is the lowest score that still keeps at least 98 % of real
detections (chosen on validation); we then report how many false positives that removes compared with alerting on
every rule hit. PR-AUC is reported next to ROC-AUC because real detections are a minority.

**Data.** {meta["data"]["rows"]["train"]} / {meta["data"]["rows"]["valid"]} / {meta["data"]["rows"]["test"]}
detections (train / validation / test) from 60 synthetic Halden Logistics days passed through the real normaliser,
Sigma detector and UEBA: randomised attack scenarios modelled on OTRF datasets, benign look-alikes (backup
agent maintenance, administrator deployments, travellers, honest prompt refusals, large legitimate uploads) and
background activity. Label = the detection contains an attacker's event. 3 % of training labels are flipped to
mimic analyst error.

**Leakage-safe split.** By day in time order (40 / 10 / 10 days). Each scenario instance lives inside a single
day, so no scenario contributes to more than one split. Rule track records (`rule_prior`) are computed on the
training days only.

**Models (test split).**

| model | ROC-AUC | PR-AUC | Brier | ECE | FP reduction | recall kept |
|---|---|---|---|---|---|---|
{rows}

LightGBM is tuned with Optuna ({meta["optuna"]["trials"]} trials, validation PR-AUC
{meta["optuna"]["bestValidPrAuc"]}) and calibrated with isotonic regression on validation. Thresholds: noise below
{meta["thresholdNoise"]:.4f}, likely real from {meta["thresholdReal"]:.4f}.

**Explanations.** TreeSHAP contributions (LightGBM `pred_contrib`). The three largest per detection become
plain-language reasons ("Started by known maintenance software", "The same alert appeared on 12 computers at once").
Global importance (mean |SHAP|, test):

| feature | mean abs SHAP |
|---|---|
{imp}

**Fairness.** No attribute about people (age, gender, nationality, department) is a feature; entity criticality and
privilege describe systems and access rights. People-level scoring is the UEBA model's job and its false-positive
rate by site and staff type is reported in that model's card.

**Monitoring.** PSI per feature, train vs test (highest: {drift}); the eval re-computes PSI on the halden-night replay.
Champion/challenger promotion requires the gates below and a CISO sign-off (`models:promote`).

**Gates.** ROC-AUC ≥ {TRIAGE_GATES["roc_auc"]}, PR-AUC ≥ {TRIAGE_GATES["pr_auc"]}, Brier ≤ {TRIAGE_GATES["brier"]},
ECE ≤ {TRIAGE_GATES["ece"]}, FP reduction ≥ {TRIAGE_GATES["fp_reduction"]}
at recall ≥ {TRIAGE_GATES["recall_at_threshold"]}.
Passed: **{"yes" if meta["gatesPassed"] else "no"}**.

**Limitations.** Trained on synthetic scenarios: it learns the patterns we simulated, including how benign
maintenance looks in this estate. Real deployments need labelled history from the customer before the champion is
trusted to suppress; until then it only orders the queue.
"""


def ueba_card(info: dict[str, Any], ev: dict[str, Any], gates_passed: bool) -> str:
    fpr = "\n".join(f"| {g} | {v} |" for g, v in sorted(ev["fpr_by_group"].items()))
    return f"""# Model card: behaviour anomaly detector (UEBA) v{UEBA_VERSION}

**Purpose.** Notice when a person or a computer behaves unlike itself (new country or hosting network, refused
prompts, file shares or programs never used before, unusual volumes sent out) and put that finding on the stream as
a detection the triage colleague weighs.

**Method.** Per entity and 5-minute window: user features {", ".join(USER_FEATURES)}; host features
{", ".join(HOST_FEATURES)}. Novelty features compare with the entity's own profile. Two signals: an Isolation Forest
per entity type (150 trees), rank-normalised against the baseline windows, and a robust z-score per feature against
the entity's median with a floored MAD (`max(1.4826·MAD, 0.5·std, absolute floor)`). A window is reported when the
rank score ≥ 0.95, some robust z ≥ 3 and a novelty or risk feature is present.

**Data.** 14 days of synthetic Halden activity ({info["historyEvents"]} events): days 0–6 build profiles, days 7–13
give {info["baselineWindows"]["user"]} user and {info["baselineWindows"]["host"]} host baseline windows.

**Evaluation.** Two held-out normal days with 18 injected attack sequences (phishing take-over, contractor data
transfer, ransomware precursor) touching {ev["injectedWindows"]} of {ev["windows"]} entity windows.
precision@k = **{ev["precision_at_k"]}** (top k = {ev["injectedSequences"]} windows by score), sequence recall at the
operating threshold = **{ev["recall"]}** (an injected sequence counts as found when at least one of its windows is
flagged), window precision = **{ev["precision"]}** ({ev["flagged"]} windows flagged), window recall =
{ev["windowRecall"]} (many injected windows hold a single unremarkable event, such as the delivered email).

**Fairness (people are scored).** False-positive rate on normal user windows by group:

| group | false-positive rate |
|---|---|
{fpr}

Largest gap between sites: {ev["fpr_max_group_gap"]} (gate ≤ {UEBA_GATES["fpr_max_group_gap"]}).

**Gates.** precision@k ≥ {UEBA_GATES["precision_at_k"]}, recall ≥ {UEBA_GATES["recall"]}, site gap ≤
{UEBA_GATES["fpr_max_group_gap"]}, window precision ≥ {UEBA_GATES["precision"]}.
Passed: **{"yes" if gates_passed else "no"}**.

**Limitations.** Cold start: a new person has no profile, so novelty features stay at zero until a week of history
exists. Night-shift workers look unusual at first. Findings are evidence for a person to review, never a verdict.
"""


def write_artifacts(
    meta: dict[str, Any], models: dict[str, Any], ueba: UebaModel, ueba_info: dict[str, Any], ueba_eval: dict[str, Any]
) -> dict[str, Any]:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    models["booster"].save_model(str(ARTIFACTS / "triage_lgbm.txt"))
    (ARTIFACTS / "triage_meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    (ARTIFACTS / "triage_card.md").write_text(triage_card(meta))
    ueba.save(ARTIFACTS)
    u_passed = (
        ueba_eval["precision_at_k"] >= UEBA_GATES["precision_at_k"]
        and ueba_eval["recall"] >= UEBA_GATES["recall"]
        and ueba_eval["precision"] >= UEBA_GATES["precision"]
        and ueba_eval["fpr_max_group_gap"] <= UEBA_GATES["fpr_max_group_gap"]
    )
    ueba_meta = {
        "version": UEBA_VERSION,
        "training": ueba_info,
        "eval": ueba_eval,
        "gates": UEBA_GATES,
        "gatesPassed": u_passed,
    }
    (ARTIFACTS / "ueba_eval.json").write_text(json.dumps(ueba_meta, indent=1, sort_keys=True) + "\n")
    (ARTIFACTS / "ueba_card.md").write_text(ueba_card(ueba_info, ueba_eval, u_passed))
    from nightjar.ml.registry import write_base_registry

    rf_metrics = meta["metrics"]["random_forest"]
    registry = {
        "triage": {
            "champion": TRIAGE_VERSION,
            "versions": {
                TRIAGE_VERSION: {
                    "stage": "champion",
                    "algorithm": "LightGBM + isotonic",
                    "metrics": meta["metrics"]["lightgbm"],
                    "gatesPassed": meta["gatesPassed"],
                    "promotedBy": "Model governance (initial release)",
                },
                f"{TRIAGE_VERSION}-rf": {
                    "stage": "challenger",
                    "algorithm": "Random forest",
                    "metrics": rf_metrics,
                    "gatesPassed": bool(
                        rf_metrics["roc_auc"] >= TRIAGE_GATES["roc_auc"]
                        and rf_metrics["fp_reduction"] >= TRIAGE_GATES["fp_reduction"]
                    ),
                    "promotedBy": None,
                },
            },
        },
        "ueba": {
            "champion": UEBA_VERSION,
            "versions": {
                UEBA_VERSION: {
                    "stage": "champion",
                    "algorithm": "Isolation Forest + robust z",
                    "metrics": {
                        k: ueba_eval[k] for k in ("precision_at_k", "recall", "precision", "fpr_max_group_gap")
                    },
                    "gatesPassed": u_passed,
                    "promotedBy": "Model governance (initial release)",
                },
            },
        },
    }
    write_base_registry(registry)
    return registry


def log_mlflow(meta: dict[str, Any], ueba_eval: dict[str, Any]) -> None:
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except Exception as exc:  # pragma: no cover - optional
        log(f"MLflow unavailable ({exc}); skipped tracking")
        return
    store = REPO / "mlruns"
    store.mkdir(exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{store / 'mlflow.db'}")
    exp_name = "nightjar-triage"
    if mlflow.get_experiment_by_name(exp_name) is None:
        mlflow.create_experiment(exp_name, artifact_location=(store / "artifacts").as_uri())
    mlflow.set_experiment(exp_name)
    client = MlflowClient()
    with mlflow.start_run(run_name=f"triage-{meta['version']}") as run:
        mlflow.log_params({k: v for k, v in meta["params"].items() if k not in ("verbosity",)})
        for name, ms in meta["metrics"].items():
            for k, v in ms.items():
                mlflow.log_metric(f"{name}.{k}", float(v))
        for k in ("precision_at_k", "recall", "precision", "fpr_max_group_gap"):
            mlflow.log_metric(f"ueba.{k}", float(ueba_eval[k]))
        for f in ("triage_lgbm.txt", "triage_meta.json", "triage_card.md", "ueba_card.md"):
            mlflow.log_artifact(str(ARTIFACTS / f))
        name = "nightjar-triage"
        with contextlib.suppress(Exception):  # already registered
            client.create_registered_model(name)
        mv = client.create_model_version(
            name,
            f"{run.info.artifact_uri}/triage_lgbm.txt",
            run.info.run_id,
            description=f"LightGBM + isotonic v{meta['version']}",
        )
        client.set_registered_model_alias(name, "champion", mv.version)
    log(f"MLflow: run logged to {store} and registered as nightjar-triage (alias champion)")


def main(argv: list[str]) -> int:
    what = argv[0] if argv else "all"
    random.seed(SEED)
    np.random.seed(SEED)
    t0 = time.perf_counter()
    ueba, info = train_ueba()
    ueba_eval = eval_ueba(ueba)
    if what == "ueba":
        ueba.save(ARTIFACTS)
        return 0
    rows = build_triage_rows(ueba)
    meta, models = train_triage(rows)
    write_artifacts(meta, models, ueba, info, ueba_eval)
    if os.environ.get("NIGHTJAR_NO_MLFLOW") != "1":
        log_mlflow(meta, ueba_eval)
    sizes = {p.name: p.stat().st_size for p in sorted(ARTIFACTS.iterdir()) if p.is_file()}
    log("artefacts: " + ", ".join(f"{k} {v / 1024:.0f} KB" for k, v in sizes.items()))
    log(f"done in {time.perf_counter() - t0:.0f}s; gates passed: triage={meta['gatesPassed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
