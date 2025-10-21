"""Public interface of the ticket store (builder A): the built-in service desk (§1.11).

Keys run per organisation from `OPS-301`. Tickets created by the response agent are idempotent per
(incident, title), so a redelivered task never opens a duplicate.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.db.models.soc import Incident, Ticket, TicketComment
from nightjar.db.session import session_scope

FIRST_TICKET = 301
PRIORITIES = ("p1", "p2", "p3", "p4")


def ticket_doc(t: Ticket, comments: list[TicketComment] | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": str(t.id),
        "key": t.key,
        "incidentId": str(t.incident_id),
        "title": t.title,
        "body": t.body,
        "priority": t.priority,
        "assigneeTeam": t.assignee_team,
        "status": t.status,
        "createdBy": {"type": t.created_by_type, "name": t.created_by_name},
        "createdAt": t.created_at.isoformat() if t.created_at else None,
    }
    if comments is not None:
        doc["comments"] = [{"author": c.author, "body": c.body, "at": c.at.isoformat()} for c in comments]
    return doc


async def ticket_doc_list(db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Ticket).where(Ticket.org_id == org_id, Ticket.incident_id == incident_id).order_by(Ticket.number)
        )
    ).scalars()
    return [ticket_doc(t) for t in rows]


async def comments_of(db: AsyncSession, ticket_id: uuid.UUID) -> list[TicketComment]:
    return list(
        (
            await db.execute(
                select(TicketComment).where(TicketComment.ticket_id == ticket_id).order_by(TicketComment.at)
            )
        ).scalars()
    )


async def create_in(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    title: str,
    body: str,
    priority: str,
    assignee_team: str,
    created_by: tuple[str, str] = ("agent", "Response colleague"),
) -> tuple[Ticket, bool]:
    from nightjar.incidents.service import add_timeline

    inc = await db.get(Incident, incident_id)
    if inc is None or inc.org_id != org_id:
        raise KeyError("incident not found in this organisation")
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    existing = (
        await db.execute(
            select(Ticket).where(
                Ticket.org_id == org_id, Ticket.incident_id == incident_id, Ticket.title == title[:300]
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing, False
    last = (await db.execute(select(func.max(Ticket.number)).where(Ticket.org_id == org_id))).scalar_one()
    number = max(int(last or 0) + 1, FIRST_TICKET)
    t = Ticket(
        org_id=org_id,
        incident_id=incident_id,
        number=number,
        key=f"OPS-{number}",
        title=title[:300],
        body=body,
        priority=priority,
        assignee_team=assignee_team[:120],
        status="open",
        created_by_type=created_by[0],
        created_by_name=created_by[1],
    )
    db.add(t)
    await db.flush()
    await add_timeline(
        db,
        org_id,
        incident_id,
        kind="action",
        title=f"Ticket {t.key} opened for {t.assignee_team}: {t.title}",
        actor_type=created_by[0],
        actor_name=created_by[1],
        ref_id=t.id,
    )
    return t, True


async def create_ticket(
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    title: str,
    body: str,
    priority: Literal["p1", "p2", "p3", "p4"],
    assignee_team: str,
) -> dict[str, Any]:
    """Returns `Ticket` (§1.11). Idempotent per (incident, title)."""
    for attempt in range(3):
        try:
            async with session_scope() as db:
                t, _ = await create_in(
                    db, org_id, incident_id, title=title, body=body, priority=priority, assignee_team=assignee_team
                )
                return ticket_doc(t, [])
        except IntegrityError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


async def add_comment(org_id: uuid.UUID, ticket_id: uuid.UUID, *, body: str, author: str) -> dict[str, Any]:
    async with session_scope() as db:
        t = await db.get(Ticket, ticket_id)
        if t is None or t.org_id != org_id:
            raise KeyError("ticket not found in this organisation")
        db.add(TicketComment(ticket_id=t.id, org_id=org_id, author=author[:200], body=body[:4000], at=clock.now()))
        await db.flush()
        return ticket_doc(t, await comments_of(db, t.id))


async def get_ticket(org_id: uuid.UUID, ticket_id: uuid.UUID) -> dict[str, Any] | None:
    async with session_scope() as db:
        t = await db.get(Ticket, ticket_id)
        if t is None or t.org_id != org_id:
            return None
        return ticket_doc(t, await comments_of(db, t.id))
