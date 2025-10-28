"""Public interface of detection rules (builder B). Called by A's rules endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from functools import lru_cache

from nightjar import clock
from nightjar.contracts import RuleInfo
from nightjar.detect.engine import rulebook


@lru_cache(maxsize=1)
def _precision() -> dict[str, float]:
    """Historical precision per rule from the triage model's training split (`ml/artifacts/triage_meta.json`)."""
    from nightjar.ml.registry import ARTIFACTS

    path = ARTIFACTS / "triage_meta.json"
    if not path.exists():
        return {}
    return {k: float(v) for k, v in json.loads(path.read_text()).get("rulePriors", {}).items()}


async def disabled_rules(org_id: uuid.UUID) -> set[str]:
    overrides = await _overrides(org_id)
    return {rid for rid, enabled in overrides.items() if not enabled}


async def _overrides(org_id: uuid.UUID) -> dict[str, bool]:
    try:
        from sqlalchemy import select

        from nightjar.db.models.pipeline import RuleOverride
        from nightjar.db.session import session_scope

        async with session_scope() as db:
            rows = (await db.execute(select(RuleOverride).where(RuleOverride.org_id == org_id))).scalars().all()
            return {r.rule_id: r.enabled for r in rows}
    except Exception:
        return {}


async def _hits_7d(org_id: uuid.UUID) -> dict[str, int]:
    """Detections per rule in the last 7 days, read from the platform's detections table when it exists."""
    try:
        from sqlalchemy import text

        from nightjar.db.session import session_scope

        async with session_scope() as db:
            rows = await db.execute(
                text(
                    "SELECT rule_id, count(*) FROM detections"
                    " WHERE org_id = :org AND first_seen >= :since GROUP BY rule_id"
                ),
                {"org": str(org_id), "since": clock.now() - timedelta(days=7)},
            )
            return {str(r[0]): int(r[1]) for r in rows}
    except Exception:
        return {}


def _sync_overrides(org_id: uuid.UUID) -> dict[str, bool]:
    try:
        from sqlalchemy import create_engine, select

        from nightjar.core.config import get_settings
        from nightjar.db.models.pipeline import RuleOverride

        url = get_settings().database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")
        eng = create_engine(url)
        with eng.connect() as conn:
            rows = conn.execute(
                select(RuleOverride.rule_id, RuleOverride.enabled).where(RuleOverride.org_id == org_id)
            ).all()
        eng.dispose()
        return {r[0]: bool(r[1]) for r in rows}
    except Exception:
        return {}


def list_rules(org_id: uuid.UUID) -> list[RuleInfo]:
    book = rulebook()
    overrides = _sync_overrides(org_id)
    prec = _precision()
    return sorted(
        (
            book.info(rid, enabled=overrides.get(rid, True), precision=prec.get(rid))
            for rid in book.meta
            if rid not in book.hidden
        ),
        key=lambda r: (r.source != "nightjar", r.title.lower()),
    )


def get_rule(org_id: uuid.UUID, rule_id: str) -> tuple[RuleInfo, str] | None:
    """(info, rule YAML) or None."""
    book = rulebook()
    meta = book.meta.get(rule_id)
    if meta is None:
        return None
    overrides = _sync_overrides(org_id)
    return book.info(rule_id, enabled=overrides.get(rule_id, True), precision=_precision().get(rule_id)), meta.yaml_text


async def set_rule_enabled(org_id: uuid.UUID, rule_id: str, *, enabled: bool) -> RuleInfo:
    """Per-org override; the detector reloads within 5 s (subject `nj.control.rules`). KeyError if unknown."""
    book = rulebook()
    if rule_id not in book.meta:
        raise KeyError(rule_id)
    from sqlalchemy import select

    from nightjar.db.models.pipeline import RuleOverride
    from nightjar.db.session import session_scope

    async with session_scope() as db:
        row = (
            await db.execute(select(RuleOverride).where(RuleOverride.org_id == org_id, RuleOverride.rule_id == rule_id))
        ).scalar_one_or_none()
        if row is None:
            db.add(RuleOverride(org_id=org_id, rule_id=rule_id, enabled=enabled))
        else:
            row.enabled = enabled
    try:
        from nightjar.stream.runtime import get_bus
        from nightjar.stream.topology import CONTROL_RULES

        bus = await get_bus()
        await bus.publish_core(CONTROL_RULES, json.dumps({"orgId": str(org_id), "ruleId": rule_id}).encode())
    except Exception:  # noqa: S110 - workers also re-read overrides on start
        pass
    return book.info(rule_id, enabled=enabled, precision=_precision().get(rule_id))


async def rule_hits(org_id: uuid.UUID) -> dict[str, int]:
    return await _hits_7d(org_id)
