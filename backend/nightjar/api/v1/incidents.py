"""Incidents, timelines and agent investigations (API contract §1.8, §1.9)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field, StringConstraints
from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import AppError, conflict, validation_error
from nightjar.core.paging import Page
from nightjar.db.models.platform import Membership
from nightjar.db.models.soc import DetectionRow, Incident, IncidentEvent, InvestigationRun, TriageScoreRow
from nightjar.incidents.api import merge_into
from nightjar.incidents.service import (
    OPEN_STATUSES,
    SEVERITIES,
    STATUS_ORDER,
    add_timeline,
    announce_updated,
    apply_status,
    check_transition,
    detection_doc,
    get_incident,
    incident_full,
    summaries,
    timeline_doc,
)
from nightjar.investigations.api import run_summaries
from nightjar.regimes.service import on_incident_resolved
from nightjar.schemas.base import CamelIn
from nightjar.services.audit import audit
from nightjar.services.pipeline import AgentsUnavailableError, start_investigation

router = APIRouter(prefix="/incidents", tags=["incidents"])

Text4k = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
_SEV_RANK = case({s: i for i, s in enumerate(reversed(SEVERITIES))}, value=Incident.severity, else_=9)


class ResolutionIn(CamelIn):
    reason: Literal["contained", "benign", "false_positive", "duplicate", "merged"]
    note: str | None = Field(default=None, max_length=2000)


class IncidentPatchIn(CamelIn):
    status: Literal["new", "triage", "investigating", "containing", "monitoring", "resolved", "closed"] | None = None
    severity: Literal["info", "low", "medium", "high", "critical"] | None = None
    assignee_id: str | None = None
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)] | None = None
    resolution: ResolutionIn | None = None
    version: int


class NoteIn(CamelIn):
    body: Text4k


class MergeIn(CamelIn):
    incident_ids: list[str] = Field(min_length=1, max_length=20)


class ReasonIn(CamelIn):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class RerunIn(CamelIn):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


@router.get("")
async def list_incidents(
    page: Page = Depends(),
    status_: str = Query("open", alias="status"),
    severity: str | None = None,
    stage: str | None = None,
    kind: str | None = None,
    assignee: str | None = None,
    reportable: bool | None = None,
    q: str | None = Query(None, max_length=200),
    ctx: RequestContext = Depends(require_perm("incidents:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(Incident).where(Incident.org_id == ctx.org_id)
    if status_ == "open":
        stmt = stmt.where(Incident.status.in_(OPEN_STATUSES))
    elif status_ != "all":
        wanted = [s for s in status_.split(",") if s in STATUS_ORDER]
        if not wanted:
            raise validation_error("Unknown status.", [{"field": "status", "message": "Unknown status."}])
        stmt = stmt.where(Incident.status.in_(wanted))
    if severity:
        stmt = stmt.where(Incident.severity.in_(severity.split(",")))
    if stage:
        stmt = stmt.where(Incident.stage.in_(stage.split(",")))
    if kind:
        stmt = stmt.where(Incident.kind.in_(kind.split(",")))
    if assignee:
        if assignee == "me":
            stmt = stmt.where(Incident.assignee_id == ctx.user.id)
        else:
            try:
                stmt = stmt.where(Incident.assignee_id == uuid.UUID(assignee))
            except ValueError as exc:
                raise validation_error("Unknown assignee.", [{"field": "assignee", "message": "Unknown."}]) from exc
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Incident.title.ilike(like), Incident.reference.ilike(like)))
    stmt = stmt.order_by(_SEV_RANK, Incident.last_seen.desc())
    rows = list((await db.execute(stmt)).scalars())
    if reportable:
        docs = [s for s in await summaries(db, ctx.org_id, rows) if s["reportable"] in ("yes", "assess")]
        return page.slice(docs)
    window = rows[page.offset : page.offset + page.page_size]
    return page.wrap(await summaries(db, ctx.org_id, window), len(rows))


@router.get("/{incident_id}")
async def get_one(
    incident_id: str, ctx: RequestContext = Depends(require_perm("incidents:read")), db: AsyncSession = Depends(get_db)
) -> dict:
    return await incident_full(db, await get_incident(db, ctx.org_id, incident_id))


@router.get("/{incident_id}/timeline")
async def timeline(
    incident_id: str,
    audience: Literal["all", "executive"] = "all",
    ctx: RequestContext = Depends(require_perm("incidents:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    inc = await get_incident(db, ctx.org_id, incident_id)
    stmt = select(IncidentEvent).where(IncidentEvent.org_id == ctx.org_id, IncidentEvent.incident_id == inc.id)
    if audience == "executive":
        stmt = stmt.where(IncidentEvent.executive.is_(True))
    rows = (await db.execute(stmt.order_by(IncidentEvent.at, IncidentEvent.id))).scalars()
    return [timeline_doc(e) for e in rows]


@router.get("/{incident_id}/detections")
async def detections(
    incident_id: str, ctx: RequestContext = Depends(require_perm("detections:read")), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    inc = await get_incident(db, ctx.org_id, incident_id)
    rows = list(
        (
            await db.execute(
                select(DetectionRow)
                .where(DetectionRow.org_id == ctx.org_id, DetectionRow.incident_id == inc.id)
                .order_by(DetectionRow.first_seen)
            )
        ).scalars()
    )
    scores = {
        s.detection_id: s
        for s in (
            await db.execute(select(TriageScoreRow).where(TriageScoreRow.detection_id.in_([d.id for d in rows])))
        ).scalars()
    }
    return [detection_doc(d, scores.get(d.id), incident_ref=inc.reference) for d in rows]


@router.patch("/{incident_id}")
async def patch(
    incident_id: str,
    body: IncidentPatchIn,
    ctx: RequestContext = Depends(require_perm("incidents:write")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    inc = await get_incident(db, ctx.org_id, incident_id)
    if body.version != inc.version:
        raise conflict("stale_version", "Someone else changed this incident. Reload to see the latest version.")
    fields = body.model_fields_set
    changed: list[str] = []
    meta_base = {"reference": inc.reference}
    if "status" in fields and body.status and body.status != inc.status:
        reason = body.resolution.reason if body.resolution else None
        check_transition(inc.status, body.status, resolution_reason=reason)
        old = inc.status
        apply_status(inc, body.status)
        if body.status == "resolved":
            assert body.resolution is not None
            inc.resolution = {
                "reason": body.resolution.reason,
                "note": body.resolution.note,
                "by": ctx.user.full_name,
                "at": clock.now().isoformat(),
            }
            await on_incident_resolved(db, inc)
        changed.append("status")
        await audit(
            db,
            ctx,
            action="incident.status_changed",
            resource_type="incident",
            resource_id=inc.id,
            metadata={**meta_base, "from": old, "status": body.status},
        )
        await add_timeline(
            db,
            ctx.org_id,
            inc.id,
            kind="status",
            actor_type="user",
            actor_name=ctx.user.full_name,
            title=f"Status changed from {old} to {body.status}",
            body=body.resolution.note if body.resolution else None,
            executive=body.status in ("resolved", "closed"),
        )
    if "severity" in fields and body.severity and body.severity != inc.severity:
        old = inc.severity
        inc.severity = body.severity
        changed.append("severity")
        await audit(
            db,
            ctx,
            action="incident.severity_changed",
            resource_type="incident",
            resource_id=inc.id,
            metadata={**meta_base, "from": old, "to": body.severity},
        )
        await add_timeline(
            db,
            ctx.org_id,
            inc.id,
            kind="status",
            actor_type="user",
            actor_name=ctx.user.full_name,
            title=f"Severity changed from {old} to {body.severity}",
        )
    if "assignee_id" in fields:
        new_id = None
        if body.assignee_id:
            try:
                new_id = uuid.UUID(body.assignee_id)
            except ValueError:
                new_id = None
            member = (
                (
                    await db.execute(
                        select(Membership).where(
                            Membership.org_id == ctx.org_id, Membership.user_id == new_id, Membership.status == "active"
                        )
                    )
                ).scalar_one_or_none()
                if new_id
                else None
            )
            if member is None:
                raise validation_error(
                    "Choose a member of this organisation.", [{"field": "assigneeId", "message": "Not a member."}]
                )
        if new_id != inc.assignee_id:
            inc.assignee_id = new_id
            changed.append("assignee")
            await audit(
                db,
                ctx,
                action="incident.assigned",
                resource_type="incident",
                resource_id=inc.id,
                metadata={**meta_base, "assigneeId": str(new_id) if new_id else None},
            )
    if "title" in fields and body.title and body.title != inc.title:
        inc.title = body.title
        changed.append("title")
        await audit(
            db,
            ctx,
            action="incident.updated",
            resource_type="incident",
            resource_id=inc.id,
            metadata={**meta_base, "fields": ["title"]},
        )
    if changed:
        inc.version += 1
        await announce_updated(db, inc, changed)
    await db.commit()
    return await incident_full(db, inc)


@router.post("/{incident_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(
    incident_id: str,
    body: NoteIn,
    ctx: RequestContext = Depends(require_perm("incidents:write")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    inc = await get_incident(db, ctx.org_id, incident_id)
    row = await add_timeline(
        db,
        ctx.org_id,
        inc.id,
        kind="note",
        actor_type="user",
        actor_name=ctx.user.full_name,
        title="Note",
        body=body.body,
    )
    await audit(
        db,
        ctx,
        action="incident.note_added",
        resource_type="incident",
        resource_id=inc.id,
        metadata={"reference": inc.reference},
    )
    await announce_updated(db, inc, ["timeline"])
    await db.commit()
    return timeline_doc(row)


@router.post("/{incident_id}/merge")
async def merge(
    incident_id: str,
    body: MergeIn,
    ctx: RequestContext = Depends(require_perm("incidents:write")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    target = await get_incident(db, ctx.org_id, incident_id)
    if target.status not in OPEN_STATUSES:
        raise AppError(422, "invalid_transition", "Invalid merge", "Merge into an open incident.")
    others = [await get_incident(db, ctx.org_id, i) for i in body.incident_ids]
    for other in others:
        if other.id == target.id:
            raise validation_error(
                "An incident cannot be merged into itself.",
                [{"field": "incidentIds", "message": "Remove this incident."}],
            )
        if other.status == "closed":
            raise AppError(422, "invalid_transition", "Invalid merge", f"{other.reference} is already closed.")
    for other in others:
        await merge_into(db, target, other, actor_name=ctx.user.full_name, actor_type="user")
    target.version += 1
    await audit(
        db,
        ctx,
        action="incident.merged",
        resource_type="incident",
        resource_id=target.id,
        metadata={"reference": target.reference, "merged": [o.reference for o in others]},
    )
    await announce_updated(db, target, ["merged"])
    await db.commit()
    return await incident_full(db, target)


@router.post("/{incident_id}/reopen")
async def reopen(
    incident_id: str,
    body: ReasonIn,
    ctx: RequestContext = Depends(require_perm("incidents:write")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    inc = await get_incident(db, ctx.org_id, incident_id)
    if inc.status not in ("resolved", "closed") or inc.merged_into_id:
        raise AppError(422, "invalid_transition", "Invalid status change", "Only a resolved incident can be reopened.")
    old = inc.status
    inc.status, inc.stage, inc.resolution, inc.resolved_at = "investigating", "investigate", None, None
    inc.version += 1
    await audit(
        db,
        ctx,
        action="incident.reopened",
        resource_type="incident",
        resource_id=inc.id,
        metadata={"reference": inc.reference, "from": old, "reason": body.reason},
    )
    await add_timeline(
        db,
        ctx.org_id,
        inc.id,
        kind="status",
        actor_type="user",
        actor_name=ctx.user.full_name,
        title="Incident reopened",
        body=body.reason,
        executive=True,
    )
    await announce_updated(db, inc, ["status"])
    await db.commit()
    return await incident_full(db, inc)


# ── Investigations on an incident ────────────────────────────────────────────
@router.get("/{incident_id}/investigations")
async def investigations(
    incident_id: str,
    ctx: RequestContext = Depends(require_perm("investigations:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    inc = await get_incident(db, ctx.org_id, incident_id)
    runs = list(
        (
            await db.execute(
                select(InvestigationRun)
                .where(InvestigationRun.org_id == ctx.org_id, InvestigationRun.incident_id == inc.id)
                .order_by(InvestigationRun.started_at.desc())
            )
        ).scalars()
    )
    return await run_summaries(db, runs)


@router.post("/{incident_id}/investigations", status_code=status.HTTP_202_ACCEPTED)
async def rerun(
    incident_id: str,
    body: RerunIn | None = None,
    ctx: RequestContext = Depends(require_perm("investigations:run")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    inc = await get_incident(db, ctx.org_id, incident_id)
    inc_id, org_id = inc.id, ctx.org_id
    has_runs = (
        await db.execute(select(InvestigationRun.id).where(InvestigationRun.incident_id == inc_id).limit(1))
    ).first() is not None
    await db.commit()  # end our read transaction before the agents write
    try:
        run_id = await start_investigation(
            org_id, inc_id, trigger="rerun" if has_runs else "manual", requested_by=ctx.user.id
        )
    except AgentsUnavailableError as exc:
        raise AppError(
            503,
            "agents_unavailable",
            "Service unavailable",
            "The investigation colleagues are not available right now. Try again in a minute.",
            headers={"Retry-After": "30"},
        ) from exc
    run = await db.get(InvestigationRun, run_id)
    if run is None:
        raise AppError(503, "agents_unavailable", "Service unavailable", "The investigation could not be started.")
    if body and body.reason:
        run.reason = body.reason
        await db.commit()
    return (await run_summaries(db, [run]))[0]
