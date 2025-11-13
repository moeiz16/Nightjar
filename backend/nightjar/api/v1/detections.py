"""Detections, detection rules and ATT&CK look-ups (API contract §1.7, §1.15 triage scores)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.contracts import to_doc
from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import AppError, unavailable
from nightjar.core.paging import Page, count
from nightjar.db.models.soc import DetectionRow, Incident, TriageScoreRow
from nightjar.incidents.service import detection_doc, triage_doc
from nightjar.schemas.base import CamelIn
from nightjar.services.audit import audit
from nightjar.services.pipeline import technique_detail

router = APIRouter(tags=["detections"])


async def _with_scores(db: AsyncSession, rows: list[DetectionRow], *, evidence: bool = False) -> list[dict]:
    ids = [d.id for d in rows]
    scores = (
        {
            s.detection_id: s
            for s in (await db.execute(select(TriageScoreRow).where(TriageScoreRow.detection_id.in_(ids)))).scalars()
        }
        if ids
        else {}
    )
    inc_ids = {d.incident_id for d in rows if d.incident_id}
    refs = (
        dict((await db.execute(select(Incident.id, Incident.reference).where(Incident.id.in_(inc_ids)))).all())
        if inc_ids
        else {}
    )
    return [
        detection_doc(
            d, scores.get(d.id), incident_ref=refs.get(d.incident_id) if d.incident_id else None, evidence=evidence
        )
        for d in rows
    ]


async def _detection(db: AsyncSession, org_id: uuid.UUID, detection_id: str) -> DetectionRow:
    try:
        uid = uuid.UUID(detection_id)
    except ValueError:
        uid = None
    d = await db.get(DetectionRow, uid) if uid else None
    if d is None or d.org_id != org_id:
        raise AppError(404, "not_found", "Detection not found", "Detection not found.")
    return d


@router.get("/detections")
async def list_detections(
    page: Page = Depends(),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    severity: str | None = None,
    source: str | None = None,
    band: str | None = None,
    incident_id: str | None = Query(None, alias="incidentId"),
    rule_id: str | None = Query(None, alias="ruleId"),
    ctx: RequestContext = Depends(require_perm("detections:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(DetectionRow).where(DetectionRow.org_id == ctx.org_id)
    if date_from:
        stmt = stmt.where(DetectionRow.last_seen >= date_from)
    if date_to:
        stmt = stmt.where(DetectionRow.first_seen <= date_to)
    if severity:
        stmt = stmt.where(DetectionRow.severity.in_(severity.split(",")))
    if source:
        stmt = stmt.where(DetectionRow.source == source)
    if rule_id:
        stmt = stmt.where(DetectionRow.rule_id == rule_id)
    if incident_id:
        try:
            stmt = stmt.where(DetectionRow.incident_id == uuid.UUID(incident_id))
        except ValueError:
            inc = (
                await db.execute(
                    select(Incident.id).where(Incident.org_id == ctx.org_id, Incident.reference == incident_id.upper())
                )
            ).scalar_one_or_none()
            stmt = stmt.where(DetectionRow.incident_id == inc)
    if band:
        stmt = stmt.join(TriageScoreRow, TriageScoreRow.detection_id == DetectionRow.id).where(
            TriageScoreRow.band == band
        )
    total = await count(db, stmt)
    rows = list(
        (
            await db.execute(stmt.order_by(DetectionRow.first_seen.desc()).offset(page.offset).limit(page.page_size))
        ).scalars()
    )
    return page.wrap(await _with_scores(db, rows), total)


@router.get("/detections/{detection_id}")
async def get_detection(
    detection_id: str,
    ctx: RequestContext = Depends(require_perm("detections:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    d = await _detection(db, ctx.org_id, detection_id)
    return (await _with_scores(db, [d], evidence=True))[0]


@router.get("/detections/{detection_id}/triage")
async def get_triage(
    detection_id: str,
    ctx: RequestContext = Depends(require_perm("detections:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    d = await _detection(db, ctx.org_id, detection_id)
    score = await db.get(TriageScoreRow, d.id)
    if score is None:
        raise AppError(404, "not_found", "Not scored", "This detection has not been scored yet.")
    return triage_doc(score) or {}


# ── Rules (builder B's detection engine) ─────────────────────────────────────
def _rules_api():
    from nightjar.detect import api as detect

    return detect


@router.get("/rules")
async def list_rules(
    q: str | None = None,
    source: str | None = None,
    enabled: bool | None = None,
    tactic: str | None = None,
    ctx: RequestContext = Depends(require_perm("rules:read")),
) -> list[dict]:
    try:
        rules = _rules_api().list_rules(ctx.org_id)
    except NotImplementedError as exc:
        raise unavailable("Detection rules are not loaded yet.") from exc
    hits, daily, exceptions = await _rule_stats(ctx.org_id)
    out = []
    for r in rules:
        if q and q.lower() not in f"{r.title} {r.id}".lower():
            continue
        if source and r.source != source:
            continue
        if enabled is not None and r.enabled != enabled:
            continue
        if tactic and not any(tactic in a.tactics for a in r.attack):
            continue
        out.append(_rule_doc(r, hits, daily, exceptions))
    return out


async def _rule_stats(org_id: uuid.UUID) -> tuple[dict[str, int], dict[str, list[int]], dict[str, int]]:
    """Hits in the last 7 days, hits per day (oldest first) and active exceptions per rule."""
    detect = _rules_api()
    hits = await detect.rule_hits(org_id)
    daily = await detect.rule_hits_daily(org_id)
    exceptions: dict[str, int] = {}
    for sup in await detect.active_suppressions(org_id):
        exceptions[sup.rule_id] = exceptions.get(sup.rule_id, 0) + 1
    return hits, daily, exceptions


def _rule_doc(r: Any, hits: dict[str, int], daily: dict[str, list[int]], exceptions: dict[str, int]) -> dict:
    doc = to_doc(r)
    doc["hits7d"] = hits.get(r.id, 0)
    doc["hits7dDaily"] = daily.get(r.id, [0] * 7)
    doc["exceptions"] = exceptions.get(r.id, 0)
    return doc


def _suppression_doc(sup: Any) -> dict:
    return {
        "id": str(sup.id),
        "ruleId": sup.rule_id,
        "field": sup.field,
        "value": sup.value,
        "reason": sup.reason,
        "createdBy": sup.created_by,
        "createdAt": sup.created_at.isoformat(),
        "expiresAt": sup.expires_at.isoformat() if sup.expires_at else None,
    }


@router.get("/rules/{rule_id}")
async def get_rule(rule_id: str, ctx: RequestContext = Depends(require_perm("rules:read"))) -> dict:
    try:
        found = _rules_api().get_rule(ctx.org_id, rule_id)
    except NotImplementedError as exc:
        raise unavailable("Detection rules are not loaded yet.") from exc
    if found is None:
        raise AppError(404, "not_found", "Rule not found", "Rule not found.")
    info, yaml = found
    hits, daily, exceptions = await _rule_stats(ctx.org_id)
    sups = await _rules_api().active_suppressions(ctx.org_id, rule_id)
    return {
        "rule": _rule_doc(info, hits, daily, exceptions),
        "yaml": yaml,
        "exceptions": [_suppression_doc(x) for x in sups],
    }


class RulePatchIn(CamelIn):
    enabled: bool


@router.patch("/rules/{rule_id}")
async def toggle_rule(
    rule_id: str,
    body: RulePatchIn,
    ctx: RequestContext = Depends(require_perm("rules:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    detect = _rules_api()
    try:
        if detect.get_rule(ctx.org_id, rule_id) is None:
            raise AppError(404, "not_found", "Rule not found", "Rule not found.")
        info = await detect.set_rule_enabled(ctx.org_id, rule_id, enabled=body.enabled)
    except NotImplementedError as exc:
        raise unavailable("Detection rules are not loaded yet.") from exc
    await audit(
        db,
        ctx,
        action="rule.toggled",
        resource_type="rule",
        resource_id=rule_id[:64],
        metadata={"title": info.title, "state": "turned on" if body.enabled else "turned off", "enabled": body.enabled},
    )
    await db.commit()
    hits, daily, exceptions = await _rule_stats(ctx.org_id)
    return _rule_doc(info, hits, daily, exceptions)


# ── Exceptions ("Lower the noise", "Mark as expected") ───────────────────────
_FIELD_LABELS = {"user": "person", "host": "device", "ip": "address"}


class SuppressionIn(CamelIn):
    rule_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=128)]
    field: Literal["user", "host", "ip"]
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1000)]
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


def _rule_title(rule_id: str) -> str:
    if rule_id.startswith("ueba:"):
        kind = rule_id.split(":")[1] if ":" in rule_id else "user"
        return "Unusual behaviour (" + ("people" if kind == "user" else "devices") + ")"
    from nightjar.detect.engine import rulebook

    meta = rulebook().meta.get(rule_id)
    return meta.title if meta else rule_id


@router.get("/suppressions")
async def list_suppressions(
    rule_id: str | None = Query(None, alias="ruleId", max_length=128),
    ctx: RequestContext = Depends(require_perm("rules:read")),
) -> list[dict]:
    return [_suppression_doc(x) for x in await _rules_api().active_suppressions(ctx.org_id, rule_id)]


@router.post("/suppressions", status_code=status.HTTP_201_CREATED)
async def add_suppression(
    body: SuppressionIn,
    ctx: RequestContext = Depends(require_perm("rules:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    expires = clock.now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    try:
        sup = await _rules_api().add_suppression(
            ctx.org_id,
            body.rule_id,
            field=body.field,
            value=body.value,
            reason=body.reason,
            created_by_id=ctx.user.id,
            created_by=ctx.user.full_name,
            expires_at=expires,
        )
    except KeyError as exc:
        raise AppError(404, "not_found", "Rule not found", "Rule not found.") from exc
    await audit(
        db,
        ctx,
        action="rule.exception_added",
        resource_type="rule",
        resource_id=body.rule_id[:64],
        metadata={
            "title": _rule_title(body.rule_id),
            "field": body.field,
            "fieldLabel": _FIELD_LABELS[body.field],
            "value": body.value,
            "reason": body.reason,
            "expiresAt": expires.isoformat() if expires else None,
        },
    )
    await db.commit()
    return _suppression_doc(sup)


@router.delete("/suppressions/{suppression_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_suppression(
    suppression_id: str,
    ctx: RequestContext = Depends(require_perm("rules:manage")),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        sid = uuid.UUID(suppression_id)
    except ValueError as exc:
        raise AppError(404, "not_found", "Exception not found", "Exception not found.") from exc
    sup = await _rules_api().revoke_suppression(ctx.org_id, sid, revoked_by=ctx.user.full_name)
    if sup is None:
        raise AppError(404, "not_found", "Exception not found", "Exception not found.")
    await audit(
        db,
        ctx,
        action="rule.exception_removed",
        resource_type="rule",
        resource_id=sup.rule_id[:64],
        metadata={
            "title": _rule_title(sup.rule_id),
            "field": sup.field,
            "fieldLabel": _FIELD_LABELS[sup.field],
            "value": sup.value,
        },
    )
    await db.commit()


@router.get("/attack/techniques/{technique_id}")
async def technique(technique_id: str, ctx: RequestContext = Depends(require_perm("rules:read"))) -> dict:
    found = technique_detail(technique_id.upper()[:16])
    if found is None:
        raise AppError(404, "not_found", "Technique not found", "Technique not found.")
    return found
