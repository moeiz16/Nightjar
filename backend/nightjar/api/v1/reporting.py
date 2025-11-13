"""The incident clock: regime determinations, deadlines and notices (API contract §1.12)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import HTMLResponse
from pydantic import Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.db.models.soc import Incident
from nightjar.incidents.service import get_incident
from nightjar.regimes import service as clock_svc
from nightjar.schemas.base import CamelIn

router = APIRouter(tags=["reporting"])

Rationale = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]


class DeterminationIn(CamelIn):
    applies: bool
    rationale: Rationale
    entity_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    aware_at: datetime | None = None
    materiality_determined_at: datetime | None = None
    impact_level: Literal["significant", "relevant"] | None = None


class DelayIn(CamelIn):
    granted_until: datetime
    reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class DeferIn(CamelIn):
    entity_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    decide_by: datetime
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class NoticeIn(CamelIn):
    regime: Literal["nis2", "sec_8k", "soci", "gdpr_art33"]
    type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=60)]


class SectionIn(CamelIn):
    key: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    body: str = Field(max_length=8000)


class NoticePatchIn(CamelIn):
    sections: list[SectionIn] = Field(min_length=1, max_length=20)
    version: int


class SubmitIn(CamelIn):
    channel: Literal["portal", "email", "oral"]
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


@router.get("/reporting/clocks")
async def clocks(
    ctx: RequestContext = Depends(require_perm("reporting:read")), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    return await clock_svc.clocks(db, ctx.org_id)


@router.get("/incidents/{incident_id}/reporting")
async def incident_reporting(
    incident_id: str, ctx: RequestContext = Depends(require_perm("reporting:read")), db: AsyncSession = Depends(get_db)
) -> dict:
    return await clock_svc.incident_reporting(db, await get_incident(db, ctx.org_id, incident_id))


@router.post("/incidents/{incident_id}/reporting/sec_8k/delay")
async def sec_delay(
    incident_id: str,
    body: DelayIn,
    ctx: RequestContext = Depends(require_perm("reporting:assess")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    inc = await get_incident(db, ctx.org_id, incident_id)
    out = await clock_svc.sec_delay(
        db, inc, granted_until=body.granted_until, reference=body.reference, user=ctx.user, ip=ctx.ip
    )
    await db.commit()
    return out


@router.post("/incidents/{incident_id}/reporting/{regime}/defer")
async def defer(
    incident_id: str,
    regime: str,
    body: DeferIn,
    ctx: RequestContext = Depends(require_perm("reporting:assess")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    inc = await get_incident(db, ctx.org_id, incident_id)
    out = await clock_svc.defer(
        db,
        inc,
        regime,
        entity_id=body.entity_id,
        decide_by=body.decide_by,
        note=body.note,
        user=ctx.user,
        ip=ctx.ip,
    )
    await db.commit()
    return out


@router.post("/incidents/{incident_id}/reporting/{regime}/assess")
async def assess(
    incident_id: str,
    regime: str,
    body: DeterminationIn,
    ctx: RequestContext = Depends(require_perm("reporting:assess")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    inc = await get_incident(db, ctx.org_id, incident_id)
    out = await clock_svc.assess(db, inc, regime, body.model_dump(by_alias=True), user=ctx.user, ip=ctx.ip)
    await db.commit()
    return out


@router.post("/incidents/{incident_id}/notices", status_code=status.HTTP_201_CREATED)
async def draft(
    incident_id: str,
    body: NoticeIn,
    ctx: RequestContext = Depends(require_perm("notices:draft")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    inc = await get_incident(db, ctx.org_id, incident_id)
    n = await clock_svc.draft_notice(db, inc, regime=body.regime, notice_type=body.type, user=ctx.user, ip=ctx.ip)
    await db.commit()
    return await clock_svc.notice_doc(db, n)


@router.get("/notices/{notice_id}")
async def get_notice(
    notice_id: str, ctx: RequestContext = Depends(require_perm("reporting:read")), db: AsyncSession = Depends(get_db)
) -> dict:
    return await clock_svc.notice_doc(db, await clock_svc.get_notice(db, ctx.org_id, notice_id))


@router.patch("/notices/{notice_id}")
async def edit_notice(
    notice_id: str,
    body: NoticePatchIn,
    ctx: RequestContext = Depends(require_perm("notices:draft")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    n = await clock_svc.get_notice(db, ctx.org_id, notice_id)
    await clock_svc.edit_notice(
        db, n, sections=[s.model_dump() for s in body.sections], version=body.version, user=ctx.user
    )
    await db.commit()
    return await clock_svc.notice_doc(db, n)


@router.post("/notices/{notice_id}/approve")
async def approve_notice(
    notice_id: str, ctx: RequestContext = Depends(require_perm("notices:approve")), db: AsyncSession = Depends(get_db)
) -> dict:
    n = await clock_svc.get_notice(db, ctx.org_id, notice_id)
    await clock_svc.approve_notice(db, n, user=ctx.user, ip=ctx.ip)
    await db.commit()
    return await clock_svc.notice_doc(db, n)


@router.post("/notices/{notice_id}/submit")
async def submit_notice(
    notice_id: str,
    body: SubmitIn,
    ctx: RequestContext = Depends(require_perm("notices:submit")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    n = await clock_svc.get_notice(db, ctx.org_id, notice_id)
    await clock_svc.submit_notice(db, n, channel=body.channel, note=body.note, user=ctx.user, ip=ctx.ip)
    await db.commit()
    return await clock_svc.notice_doc(db, n)


@router.get("/notices/{notice_id}/document.html", response_class=HTMLResponse)
async def notice_document(
    notice_id: str, ctx: RequestContext = Depends(require_perm("reporting:read")), db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    n = await clock_svc.get_notice(db, ctx.org_id, notice_id)
    inc = await db.get(Incident, n.incident_id)
    assert ctx.org is not None
    assert inc is not None
    doc = await clock_svc.notice_doc(db, n)
    return HTMLResponse(
        clock_svc.notice_html(doc, org_name=ctx.org.name, incident_reference=inc.reference),
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
    )
