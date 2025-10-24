"""Normalised event search, the event river and live updates over Server-Sent Events (§1.6, §1.17)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from nightjar import clock
from nightjar.core.deps import RequestContext, get_ctx, get_db, require_perm
from nightjar.core.errors import AppError, unavailable
from nightjar.core.events import sse_response, stream
from nightjar.core.paging import Page
from nightjar.hunting import api as store
from nightjar.hunting.service import river

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream", tags=["live"])
async def event_stream(
    request: Request,
    ctx: RequestContext = Depends(get_ctx),
    last_event_id: int | None = Query(None, alias="lastEventId", ge=0),
) -> EventSourceResponse:
    header = request.headers.get("last-event-id")
    after = int(header) if header and header.isdigit() else last_event_id
    return sse_response(stream(ctx.org_id, ctx.user.id, ctx.permissions, after_id=after))


@router.get("/river")
async def event_river(
    after: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    ctx: RequestContext = Depends(require_perm("watch:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await river(db, ctx.org_id, after=after, limit=limit)
    except store.SearchTimeoutError as exc:
        raise unavailable("Search is busy. Try again.") from exc


@router.get("")
async def list_events(
    page: Page = Depends(),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    q: str | None = Query(None, max_length=200),
    dataset: str | None = None,
    host: str | None = None,
    user: str | None = None,
    ip: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    ctx: RequestContext = Depends(require_perm("events:read")),
) -> dict:
    to = date_to or clock.now()
    frm = date_from or (to - timedelta(days=7))
    filters: list[tuple[str, str, Any]] = []
    for field, value in (
        ("dataset", dataset),
        ("host.name", host),
        ("user.name", user),
        ("action", action),
        ("outcome", outcome),
        ("relatedIps", ip),
    ):
        if value:
            filters.append((field, "eq", value))
    try:
        items, total = await store.list_events(
            ctx.org_id, time_from=frm, time_to=to, filters=filters, q=q, offset=page.offset, limit=page.page_size
        )
    except store.InvalidQueryError as exc:
        raise AppError(422, "invalid_query", "Invalid search", str(exc)) from exc
    except store.SearchTimeoutError as exc:
        raise unavailable("Search took too long. Narrow the time range.") from exc
    return page.wrap(items, total)


@router.get("/{event_id}")
async def get_event(event_id: str, ctx: RequestContext = Depends(require_perm("events:read"))) -> dict:
    doc = await store.get_event(ctx.org_id, event_id[:64])
    if doc is None:
        raise AppError(404, "not_found", "Event not found", "Event not found.")
    return doc
