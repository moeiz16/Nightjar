"""Public interface of detection rules (builder B). Called by A's rules endpoints."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

from nightjar import clock
from nightjar.contracts import Entities, RuleInfo
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
        from sqlalchemy import func, select

        from nightjar.db.models.soc import DetectionRow
        from nightjar.db.session import session_scope

        async with session_scope() as db:
            rows = await db.execute(
                select(DetectionRow.rule_id, func.count())
                .where(DetectionRow.org_id == org_id, DetectionRow.first_seen >= clock.now() - timedelta(days=7))
                .group_by(DetectionRow.rule_id)
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
    text = meta.yaml_text
    if meta.correlation:
        # a correlation rule reads with the base rules it counts (Sigma's multi-document form): base first, `---`
        import re

        names = re.findall(r"[\w-]+", (re.search(r"rules:\s*\[([^\]]*)\]", text) or ["", ""])[1])
        bases = [m.yaml_text.strip() for m in book.meta.values() if m.name in names and not m.correlation]
        if bases:
            text = "\n---\n".join([*bases, text.strip()]) + "\n"
    return book.info(rule_id, enabled=overrides.get(rule_id, True), precision=_precision().get(rule_id)), text


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


async def rule_hits_daily(org_id: uuid.UUID, days: int = 7) -> dict[str, list[int]]:
    """Detections per rule per day (demo-clock days in UTC, oldest first) for the last `days` days."""
    try:
        from sqlalchemy import select

        from nightjar.db.models.soc import DetectionRow
        from nightjar.db.session import session_scope

        today = clock.today()
        start = clock.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        async with session_scope() as db:
            rows = (
                await db.execute(
                    select(DetectionRow.rule_id, DetectionRow.first_seen).where(
                        DetectionRow.org_id == org_id, DetectionRow.first_seen >= start
                    )
                )
            ).all()
    except Exception:
        return {}
    out: dict[str, list[int]] = {}
    for rule_id, first_seen in rows:
        idx = days - 1 - (today - first_seen.date()).days
        if 0 <= idx < days:
            out.setdefault(str(rule_id), [0] * days)[idx] += 1
    return out


# ── Per-org suppressions ("Lower the noise", "Mark as expected") ──────────────
SUPPRESSION_FIELDS = ("user", "host", "ip")


@dataclass(frozen=True, slots=True)
class Suppression:
    id: uuid.UUID
    rule_id: str
    field: str
    value: str
    reason: str
    created_by: str
    created_at: datetime
    expires_at: datetime | None

    def matches(self, rule_id: str, entities: Entities) -> bool:
        if self.rule_id.endswith("*"):
            if not rule_id.startswith(self.rule_id[:-1]):
                return False
        elif rule_id != self.rule_id:
            return False
        pool = {"user": entities.users, "host": entities.hosts, "ip": entities.ips}[self.field]
        want = self.value.lower()
        return any(v.lower() == want or v.lower().split("@")[0] == want for v in pool)


def _suppression(row: object) -> Suppression:
    return Suppression(
        id=row.id,  # type: ignore[attr-defined]
        rule_id=row.rule_id,  # type: ignore[attr-defined]
        field=row.field,  # type: ignore[attr-defined]
        value=row.value,  # type: ignore[attr-defined]
        reason=row.reason,  # type: ignore[attr-defined]
        created_by=row.created_by_name,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        expires_at=row.expires_at,  # type: ignore[attr-defined]
    )


async def active_suppressions(org_id: uuid.UUID, rule_id: str | None = None) -> list[Suppression]:
    """Unrevoked, unexpired suppressions for the org (optionally those that could apply to one rule)."""
    from sqlalchemy import or_, select

    from nightjar.db.models.pipeline import RuleSuppression
    from nightjar.db.session import session_scope

    now = clock.now()
    async with session_scope() as db:
        stmt = select(RuleSuppression).where(
            RuleSuppression.org_id == org_id,
            RuleSuppression.revoked_at.is_(None),
            or_(RuleSuppression.expires_at.is_(None), RuleSuppression.expires_at > now),
        )
        rows = (await db.execute(stmt.order_by(RuleSuppression.created_at))).scalars().all()
        items = [_suppression(r) for r in rows]
    if rule_id is None:
        return items
    return [
        s for s in items if s.rule_id == rule_id or (s.rule_id.endswith("*") and rule_id.startswith(s.rule_id[:-1]))
    ]


async def _signal_reload(org_id: uuid.UUID, rule_id: str) -> None:
    try:
        from nightjar.stream.runtime import get_bus
        from nightjar.stream.topology import CONTROL_RULES

        bus = await get_bus()
        await bus.publish_core(CONTROL_RULES, json.dumps({"orgId": str(org_id), "ruleId": rule_id}).encode())
    except Exception:  # noqa: S110 - the scorer also re-reads suppressions every 30 s
        pass


async def add_suppression(
    org_id: uuid.UUID,
    rule_id: str,
    *,
    field: str,
    value: str,
    reason: str,
    created_by_id: uuid.UUID | None,
    created_by: str,
    expires_at: datetime | None = None,
) -> Suppression:
    """KeyError if the rule is unknown (a trailing `*` matches the UEBA rule families); ValueError on a bad field."""
    if field not in SUPPRESSION_FIELDS:
        raise ValueError(field)
    book = rulebook()
    if rule_id.endswith("*"):
        if not rule_id.startswith("ueba:"):
            raise KeyError(rule_id)
    elif rule_id not in book.meta and not rule_id.startswith("ueba:"):
        raise KeyError(rule_id)
    from nightjar.db.models.pipeline import RuleSuppression
    from nightjar.db.session import session_scope

    async with session_scope() as db:
        row = RuleSuppression(
            org_id=org_id,
            rule_id=rule_id,
            field=field,
            value=value.strip(),
            reason=reason.strip(),
            created_by_id=created_by_id,
            created_by_name=created_by,
            created_at=clock.now(),
            expires_at=expires_at,
        )
        db.add(row)
        await db.flush()
        out = _suppression(row)
    await _signal_reload(org_id, rule_id)
    return out


async def revoke_suppression(org_id: uuid.UUID, suppression_id: uuid.UUID, *, revoked_by: str) -> Suppression | None:
    """None when unknown, another org's or already revoked."""
    from sqlalchemy import select

    from nightjar.db.models.pipeline import RuleSuppression
    from nightjar.db.session import session_scope

    async with session_scope() as db:
        row = (
            await db.execute(
                select(RuleSuppression).where(
                    RuleSuppression.org_id == org_id,
                    RuleSuppression.id == suppression_id,
                    RuleSuppression.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.revoked_at, row.revoked_by_name = clock.now(), revoked_by
        out = _suppression(row)
    await _signal_reload(org_id, out.rule_id)
    return out
