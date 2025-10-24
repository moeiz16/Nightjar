"""Response actions, human approvals and the built-in service desk (API contract §1.10, §1.11)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.approvals.service import (
    KIND_LABELS,
    ROLE_PERMISSION,
    TargetNotInIncidentError,
    decide,
    docs_for,
    expire_if_due,
    get_proposal,
    proposals_for,
    request,
)
from nightjar.core.deps import RequestContext, get_db, require_any, require_perm
from nightjar.core.errors import AppError
from nightjar.core.paging import Page
from nightjar.db.models.soc import ActionProposal, Ticket
from nightjar.incidents.service import get_incident
from nightjar.schemas.base import CamelIn
from nightjar.services.pipeline import execute_approved_action
from nightjar.ticketing.api import comments_of, ticket_doc

router = APIRouter(tags=["actions"])


class ProposeIn(CamelIn):
    kind: Literal[tuple(KIND_LABELS)]  # type: ignore[valid-type]
    target: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class ApproveIn(CamelIn):
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


class RejectIn(CamelIn):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class CommentIn(CamelIn):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


def _can_approve(doc: dict, ctx: RequestContext) -> bool:
    if doc["status"] != "pending":
        return False
    mine = [r for r in doc["awaitingRoles"] if ROLE_PERMISSION[r] in ctx.permissions]
    if not mine or any(a["userId"] == str(ctx.user.id) for a in doc["approvals"]):
        return False
    if doc.get("proposedByUserId") == str(ctx.user.id):
        others = [a for a in doc["approvals"] if a["userId"] != str(ctx.user.id)]
        return not (len(doc["awaitingRoles"]) == 1 and not others)
    return True


@router.get("/approvals")
async def list_approvals(
    page: Page = Depends(),
    status_: str = Query("pending", alias="status"),
    incident_id: str | None = Query(None, alias="incidentId"),
    mine: bool = False,
    ctx: RequestContext = Depends(require_perm("actions:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(ActionProposal).where(ActionProposal.org_id == ctx.org_id)
    if incident_id:
        inc = await get_incident(db, ctx.org_id, incident_id)
        stmt = stmt.where(ActionProposal.incident_id == inc.id)
    rows = list((await db.execute(stmt)).scalars())
    expired = [p for p in rows if await expire_if_due(db, p)]
    if expired:
        await db.commit()
    if status_ != "all":
        rows = [p for p in rows if p.status in status_.split(",")]
    pending_first = {"pending": 0}
    rows.sort(
        key=lambda p: (
            pending_first.get(p.status, 1),
            p.created_at if p.status == "pending" else -p.created_at.timestamp(),
        )
    )
    docs = await docs_for(db, rows)
    for d in docs:
        d["canApprove"] = _can_approve(d, ctx)
    if mine:
        docs = [d for d in docs if d["canApprove"]]
    return page.slice(docs)


@router.get("/incidents/{incident_id}/actions")
async def incident_actions(
    incident_id: str, ctx: RequestContext = Depends(require_perm("actions:read")), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    inc = await get_incident(db, ctx.org_id, incident_id)
    docs = await proposals_for(db, ctx.org_id, incident_id=inc.id)
    for d in docs:
        d["canApprove"] = _can_approve(d, ctx)
    return docs


@router.post("/incidents/{incident_id}/actions", status_code=status.HTTP_201_CREATED)
async def propose(
    incident_id: str,
    body: ProposeIn,
    ctx: RequestContext = Depends(require_perm("actions:propose")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    inc = await get_incident(db, ctx.org_id, incident_id)
    try:
        p, _created = await request(
            db,
            inc,
            kind=body.kind,
            target=body.target,
            target_type=None,
            rationale=body.rationale,
            expected_effect="",
            reversible=True,
            proposed_by=ctx.user,
        )
    except TargetNotInIncidentError as exc:
        raise AppError(
            422,
            "target_not_in_incident",
            "Validation failed",
            "Actions can only target computers, people, keys or addresses that are part of this incident.",
            errors=[{"field": "target", "message": str(exc)}],
        ) from exc
    await db.commit()
    doc = (await docs_for(db, [p]))[0]
    doc["canApprove"] = _can_approve(doc, ctx)
    return doc


async def _decide(db: AsyncSession, ctx: RequestContext, approval_id: str, *, approve: bool, note: str | None) -> dict:
    p = await get_proposal(db, ctx.org_id, approval_id)
    await decide(db, p, user=ctx.user, permissions=ctx.permissions, approve=approve, note=note, ip=ctx.ip)
    await db.commit()
    if approve and p.status == "approved":
        await execute_approved_action(p.org_id, p.id)
        await db.refresh(p)
    doc = (await docs_for(db, [p]))[0]
    doc["canApprove"] = _can_approve(doc, ctx)
    return doc


@router.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: str,
    body: ApproveIn | None = None,
    ctx: RequestContext = Depends(require_any("actions:approve", "actions:approve_crown_jewel")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _decide(db, ctx, approval_id, approve=True, note=body.note if body else None)


@router.post("/approvals/{approval_id}/reject")
async def reject(
    approval_id: str,
    body: RejectIn,
    ctx: RequestContext = Depends(require_any("actions:approve", "actions:approve_crown_jewel")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _decide(db, ctx, approval_id, approve=False, note=body.reason)


# ── Tickets ──────────────────────────────────────────────────────────────────
async def _ticket(db: AsyncSession, org_id: uuid.UUID, ticket_id: str) -> Ticket:
    t = None
    if ticket_id.upper().startswith("OPS-"):
        t = (
            await db.execute(select(Ticket).where(Ticket.org_id == org_id, Ticket.key == ticket_id.upper()))
        ).scalar_one_or_none()
    else:
        try:
            t = await db.get(Ticket, uuid.UUID(ticket_id))
        except ValueError:
            t = None
    if t is None or t.org_id != org_id:
        raise AppError(404, "not_found", "Ticket not found", "Ticket not found.")
    return t


@router.get("/incidents/{incident_id}/tickets")
async def incident_tickets(
    incident_id: str, ctx: RequestContext = Depends(require_perm("incidents:read")), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    inc = await get_incident(db, ctx.org_id, incident_id)
    rows = (
        await db.execute(
            select(Ticket).where(Ticket.org_id == ctx.org_id, Ticket.incident_id == inc.id).order_by(Ticket.number)
        )
    ).scalars()
    return [ticket_doc(t) for t in rows]


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: str, ctx: RequestContext = Depends(require_perm("incidents:read")), db: AsyncSession = Depends(get_db)
) -> dict:
    t = await _ticket(db, ctx.org_id, ticket_id)
    return ticket_doc(t, await comments_of(db, t.id))


@router.post("/tickets/{ticket_id}/comments", status_code=status.HTTP_201_CREATED)
async def comment(
    ticket_id: str,
    body: CommentIn,
    ctx: RequestContext = Depends(require_perm("incidents:write")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from nightjar import clock
    from nightjar.db.models.soc import TicketComment

    t = await _ticket(db, ctx.org_id, ticket_id)
    db.add(TicketComment(ticket_id=t.id, org_id=ctx.org_id, author=ctx.user.full_name, body=body.body, at=clock.now()))
    await db.commit()
    return ticket_doc(t, await comments_of(db, t.id))
