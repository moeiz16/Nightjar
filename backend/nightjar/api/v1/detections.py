"""Detections, detection rules and ATT&CK look-ups (API contract §1.7, §1.15 triage scores)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
        out.append(to_doc(r))
    return out


@router.get("/rules/{rule_id}")
async def get_rule(rule_id: str, ctx: RequestContext = Depends(require_perm("rules:read"))) -> dict:
    try:
        found = _rules_api().get_rule(ctx.org_id, rule_id)
    except NotImplementedError as exc:
        raise unavailable("Detection rules are not loaded yet.") from exc
    if found is None:
        raise AppError(404, "not_found", "Rule not found", "Rule not found.")
    info, yaml = found
    return {"rule": to_doc(info), "yaml": yaml}


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
    return to_doc(info)


@router.get("/attack/techniques/{technique_id}")
async def technique(technique_id: str, ctx: RequestContext = Depends(require_perm("rules:read"))) -> dict:
    found = technique_detail(technique_id.upper()[:16])
    if found is None:
        raise AppError(404, "not_found", "Technique not found", "Technique not found.")
    return found
