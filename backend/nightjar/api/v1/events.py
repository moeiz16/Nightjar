"""Live updates over Server-Sent Events (API contract §1.17)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse

from nightjar.core.deps import RequestContext, get_ctx
from nightjar.core.events import sse_response, stream

router = APIRouter(prefix="/events", tags=["live"])


@router.get("/stream")
async def event_stream(
    request: Request,
    ctx: RequestContext = Depends(get_ctx),
    last_event_id: int | None = Query(None, alias="lastEventId", ge=0),
) -> EventSourceResponse:
    header = request.headers.get("last-event-id")
    after = int(header) if header and header.isdigit() else last_event_id
    return sse_response(stream(ctx.org_id, ctx.user.id, ctx.permissions, after_id=after))
