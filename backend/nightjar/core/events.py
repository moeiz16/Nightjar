"""Live updates: the `ui_events` outbox and the Server-Sent Events stream.

Any process appends with `emit()` inside its own transaction (API handlers, pipeline workers, agents), so an
event is visible exactly when the change that caused it commits. The API's `/events/stream` tails the table
for the caller's org (every `POLL_SECONDS`), filters by permission / recipient list and resumes from
`Last-Event-ID`. Event names and payloads: API contract §1.17.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterable
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from nightjar import clock
from nightjar.core.metrics import SSE_CLIENTS
from nightjar.db.models.platform import UiEvent
from nightjar.db.session import get_sessionmaker

log = structlog.get_logger(__name__)

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0
MAX_STREAM_SECONDS = 300.0  # the browser reconnects with Last-Event-ID
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


async def emit(
    db: AsyncSession,
    org_id: uuid.UUID,
    name: str,
    data: dict[str, Any],
    *,
    permission: str | None = None,
    user_ids: Iterable[uuid.UUID] | None = None,
) -> None:
    """Queue one live update in the caller's transaction (delivered after commit)."""
    db.add(
        UiEvent(
            org_id=org_id,
            name=name,
            data=json.loads(json.dumps(data, default=str)),
            permission=permission,
            user_ids=[str(u) for u in user_ids] if user_ids is not None else None,
            created_at=clock.now(),
        )
    )


async def latest_id(db: AsyncSession, org_id: uuid.UUID) -> int:
    row = (
        await db.execute(select(UiEvent.id).where(UiEvent.org_id == org_id).order_by(UiEvent.id.desc()).limit(1))
    ).scalar_one_or_none()
    return int(row or 0)


def _visible(ev: UiEvent, user_id: str, permissions: set[str]) -> bool:
    if ev.user_ids is not None and user_id not in ev.user_ids:
        return False
    return ev.permission is None or ev.permission in permissions


async def stream(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    permissions: set[str],
    *,
    after_id: int | None,
    max_seconds: float = MAX_STREAM_SECONDS,
) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE frames. `after_id=None` starts at the newest event (no backlog)."""
    maker = get_sessionmaker()
    uid = str(user_id)
    async with maker() as db:
        cursor = after_id if after_id is not None else await latest_id(db, org_id)
    loop = asyncio.get_running_loop()
    started = idle_since = loop.time()
    SSE_CLIENTS.inc()
    try:
        yield {"retry": 3000, "event": "ready", "id": str(cursor), "data": json.dumps({"lastEventId": cursor})}
        while loop.time() - started < max_seconds:
            async with maker() as db:
                rows = (
                    (
                        await db.execute(
                            select(UiEvent)
                            .where(UiEvent.org_id == org_id, UiEvent.id > cursor)
                            .order_by(UiEvent.id)
                            .limit(200)
                        )
                    )
                    .scalars()
                    .all()
                )
            for ev in rows:
                cursor = ev.id
                if _visible(ev, uid, permissions):
                    idle_since = loop.time()
                    yield {"id": str(ev.id), "event": ev.name, "data": json.dumps(ev.data, separators=(",", ":"))}
            if loop.time() - idle_since >= HEARTBEAT_SECONDS:
                idle_since = loop.time()
                yield {"comment": "heartbeat"}
            if not rows:
                await asyncio.sleep(POLL_SECONDS)
    finally:
        SSE_CLIENTS.dec()


def sse_response(frames: AsyncIterator[dict[str, Any]]) -> EventSourceResponse:
    return EventSourceResponse(frames, headers=SSE_HEADERS, ping=None)
