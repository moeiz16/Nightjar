"""Public interface of the models (builder B). Called by A's model and UEBA endpoints."""

from __future__ import annotations

import json
import uuid
from typing import Any

from nightjar.contracts import ModelInfo
from nightjar.ml import registry
from nightjar.ml.features import FEATURE_TEXT

ARTIFACTS = registry.ARTIFACTS
_TRAINED_ON = {
    "triage": "60 synthetic Halden Logistics days (attack scenarios modelled on OTRF datasets, benign look-alikes, "
    "background); split by day 40/10/10",
    "ueba": "14 days of synthetic Halden Logistics activity (profiles, then baseline windows)",
}


def _card(name: str) -> str:
    path = ARTIFACTS / f"{name}_card.md"
    return path.read_text() if path.exists() else f"# {name}\n\nNot trained yet: run `make train`."


def _info(name: str, version: str, v: dict[str, Any]) -> ModelInfo:
    metrics = {k: float(x) for k, x in (v.get("metrics") or {}).items() if isinstance(x, int | float)}
    return ModelInfo(
        name=name,  # type: ignore[arg-type]
        version=version,
        stage=v.get("stage", "archived"),
        trained_on=_TRAINED_ON.get(name, ""),
        metrics=metrics,
        gates_passed=bool(v.get("gatesPassed")),
        card_markdown=_card(name),
        promoted_by=v.get("promotedBy"),
    )


def list_models() -> list[ModelInfo]:
    """Champion and challenger per model, with metrics and gate status (from committed model cards)."""
    reg = registry.load()
    out = []
    for name in ("triage", "ueba"):
        entry = reg.get(name, {})
        for ver, v in sorted(entry.get("versions", {}).items(), key=lambda kv: kv[1].get("stage") != "champion"):
            out.append(_info(name, ver, v))
    return out


def model_detail(name: str) -> dict[str, Any] | None:
    """Extras for GET /models/{name}: calibration bins and PSI drift (triage), fairness table (ueba)."""
    if name == "triage" and (ARTIFACTS / "triage_meta.json").exists():
        meta = json.loads((ARTIFACTS / "triage_meta.json").read_text())
        return {
            "calibration": meta.get("calibrationBins", []),
            "drift": meta.get("drift", []),
            "importance": meta.get("importance", []),
        }
    if name == "ueba" and (ARTIFACTS / "ueba_eval.json").exists():
        ev = json.loads((ARTIFACTS / "ueba_eval.json").read_text())["eval"]
        return {"calibration": [], "drift": [], "fairness": ev.get("fpr_by_group", {})}
    return None


def promote(name: str, version: str, *, promoted_by: uuid.UUID) -> ModelInfo:
    """Champion/challenger switch after `models:promote` sign-off (A audits `model.promoted`). Refuses a
    version whose eval gates failed (ValueError → 409)."""
    reg = registry.load()
    entry = reg.get(name)
    if not entry or version not in entry.get("versions", {}):
        raise KeyError(f"{name} {version}")
    v = entry["versions"][version]
    if not v.get("gatesPassed"):
        raise ValueError("this version did not pass its evaluation gates")
    versions = {
        ver: {"stage": "challenger" if ver != version and x.get("stage") == "champion" else x.get("stage")}
        for ver, x in entry["versions"].items()
    }
    versions[version] = {"stage": "champion", "promotedBy": str(promoted_by)}
    registry.save_overlay(name, version, versions)
    from nightjar.ml import triage

    triage.champion.cache_clear()
    reg = registry.load()
    return _info(name, version, reg[name]["versions"][version])


def entity_baseline(org_id: uuid.UUID, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    """`{ feature, label, median, p95 }[]` for GET /ueba/entities/{type}/{id}: the org's stored baselines
    (`pl_ueba_baselines`) when present, otherwise the trained model's baseline for that entity."""
    rows = _db_baselines(org_id, entity_type, entity_id)
    if rows:
        return rows
    from nightjar.ml.runtime import ueba_model

    model = ueba_model()
    if model is None:
        return []
    base = model.baselines.get(entity_type, {}).get(entity_id.lower()) or model.population.get(entity_type, {})
    return [
        {"feature": f, "label": FEATURE_TEXT.get(f, f), "median": round(b.median, 3), "p95": round(b.p95, 3)}
        for f, b in base.items()
    ]


def _db_baselines(org_id: uuid.UUID, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    try:
        from sqlalchemy import create_engine, select

        from nightjar.core.config import get_settings
        from nightjar.db.models.pipeline import UebaBaseline

        url = get_settings().database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")
        eng = create_engine(url)
        with eng.connect() as conn:
            rows = conn.execute(
                select(UebaBaseline).where(
                    UebaBaseline.org_id == org_id,
                    UebaBaseline.entity_type == entity_type,
                    UebaBaseline.entity_id == entity_id.lower(),
                )
            ).all()
        eng.dispose()
        return [
            {"feature": r.feature, "label": FEATURE_TEXT.get(r.feature, r.feature), "median": r.median, "p95": r.p95}
            for r in rows
        ]
    except Exception:
        return []
