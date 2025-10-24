"""The Watch, the shift log and the executive view (API contract §1.14)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import AppError, validation_error
from nightjar.core.events import emit
from nightjar.db.models.soc import ActionProposal, Incident, WeeklyBrief
from nightjar.executive import service as exe
from nightjar.incidents.service import OPEN_STATUSES, SEVERITIES, summaries
from nightjar.regimes.service import clocks as running_clocks
from nightjar.schemas.base import CamelIn
from nightjar.services.audit import audit
from nightjar.services.jobs import apply_update, create_job, job_out
from nightjar.services.pipeline import pipeline_health

router = APIRouter(tags=["executive"])
_SEV_RANK = case({s: i for i, s in enumerate(reversed(SEVERITIES))}, value=Incident.severity, else_=9)


def _since(ctx: RequestContext, since: datetime | None) -> datetime:
    if since:
        return since
    seen = ctx.membership.last_seen_at if ctx.membership else None
    return seen or clock.now() - timedelta(hours=12)


@router.get("/watch")
async def watch(ctx: RequestContext = Depends(require_perm("watch:read")), db: AsyncSession = Depends(get_db)) -> dict:
    rows = list(
        (
            await db.execute(
                select(Incident)
                .where(Incident.org_id == ctx.org_id, Incident.status.in_(OPEN_STATUSES))
                .order_by(_SEV_RANK, Incident.last_seen.desc())
                .limit(50)
            )
        ).scalars()
    )
    pending = (
        await db.execute(
            select(func.count())
            .select_from(ActionProposal)
            .where(ActionProposal.org_id == ctx.org_id, ActionProposal.status == "pending")
        )
    ).scalar_one()
    health = await pipeline_health()
    return {
        "clocks": await running_clocks(db, ctx.org_id),
        "incidents": await summaries(db, ctx.org_id, rows),
        "pendingApprovals": int(pending),
        "shiftLog": await exe.shift_log(db, ctx.org_id, since=_since(ctx, None), limit=30),
        "pipeline": {
            "status": health.status,
            "plainStatus": health.plain_status,
            "eventsPerMinute": health.events_per_minute,
        },
    }


@router.get("/watch/shift-log")
async def shift_log(
    since: datetime | None = Query(None),
    ctx: RequestContext = Depends(require_perm("watch:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await exe.shift_log(db, ctx.org_id, since=_since(ctx, since))


@router.post("/watch/seen", status_code=status.HTTP_204_NO_CONTENT)
async def mark_seen(
    ctx: RequestContext = Depends(require_perm("watch:read")), db: AsyncSession = Depends(get_db)
) -> Response:
    assert ctx.membership is not None
    ctx.membership.last_seen_at = clock.now()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/executive/posture")
async def posture(ctx: RequestContext = Depends(require_perm("exec:read")), db: AsyncSession = Depends(get_db)) -> dict:
    return await exe.posture(db, ctx.org_id)


@router.get("/executive/morning")
async def morning(ctx: RequestContext = Depends(require_perm("exec:read")), db: AsyncSession = Depends(get_db)) -> dict:
    assert ctx.org is not None
    return await exe.morning_brief(db, ctx.org)


@router.get("/executive/briefs")
async def briefs(
    ctx: RequestContext = Depends(require_perm("exec:read")), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        await db.execute(
            select(WeeklyBrief).where(WeeklyBrief.org_id == ctx.org_id).order_by(WeeklyBrief.date_from.desc())
        )
    ).scalars()
    return [
        {
            "week": b.week,
            "from": b.date_from.isoformat(),
            "to": b.date_to.isoformat(),
            "generatedAt": b.generated_at.isoformat(),
            "headline": b.headline,
        }
        for b in rows
    ]


async def _brief(db: AsyncSession, ctx: RequestContext, week: str) -> WeeklyBrief:
    b = (
        await db.execute(select(WeeklyBrief).where(WeeklyBrief.org_id == ctx.org_id, WeeklyBrief.week == week))
    ).scalar_one_or_none()
    if b is None:
        raise AppError(404, "not_found", "Brief not found", "No brief for that week.")
    return b


@router.get("/executive/briefs/{week}")
async def brief(
    week: str, ctx: RequestContext = Depends(require_perm("exec:read")), db: AsyncSession = Depends(get_db)
) -> dict:
    return await exe.brief_doc(db, await _brief(db, ctx, week))


@router.get("/executive/briefs/{week}/document.html", response_class=HTMLResponse)
async def brief_document(
    week: str, ctx: RequestContext = Depends(require_perm("exec:read")), db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    assert ctx.org is not None
    doc = await exe.brief_doc(db, await _brief(db, ctx, week))
    return HTMLResponse(
        exe.brief_html(doc, ctx.org.name),
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
    )


class GenerateIn(CamelIn):
    week: str | None = None


@router.post("/executive/briefs/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate(
    body: GenerateIn | None = None,
    ctx: RequestContext = Depends(require_perm("reports:generate")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Runs in the API process (a few queries and one text generation); the job row and `job.updated` /
    `brief.ready` events report progress like any other job."""
    assert ctx.org is not None
    week = (body.week if body and body.week else None) or exe.iso_week(clock.today() - timedelta(days=7))
    try:
        exe.week_bounds(week)
    except ValueError as exc:
        raise validation_error("Use a week like 2025-W47.", [{"field": "week", "message": str(exc)}]) from exc
    job = await create_job(db, org_id=ctx.org_id, kind="weekly_report", payload={"week": week}, created_by=ctx.user.id)
    await apply_update(db, job, status="running", progress=10)
    try:
        await exe.build_brief(db, ctx.org, week)
    except Exception as exc:
        await apply_update(db, job, status="failed", error=type(exc).__name__)
        await db.commit()
        raise
    await apply_update(db, job, status="succeeded", result={"week": week})
    await emit(db, ctx.org_id, "brief.ready", {"week": week}, permission="exec:read")
    await audit(
        db, ctx, action="report.generated", resource_type="weekly_brief", resource_id=week, metadata={"week": week}
    )
    await db.commit()
    await asyncio.sleep(0)
    return {"job": job_out(job)}
