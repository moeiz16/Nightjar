"""Background housekeeping in the API process (every `SWEEP_SECONDS`):

* announce regulator clock transitions (`clock.state` on due_soon / overdue);
* expire pending approvals after 24 hours;
* close incidents resolved more than 24 hours ago;
* hand approved actions to the response agent again if it was unavailable at approval time;
* announce the pipeline's plain status (`pipeline.status`) on change and every 30 seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

import structlog
from sqlalchemy import select

from nightjar import clock
from nightjar.core.events import emit
from nightjar.db.models.platform import Organization
from nightjar.db.models.soc import ActionProposal, Incident
from nightjar.db.session import session_scope

log = structlog.get_logger(__name__)
SWEEP_SECONDS = 30.0
_last_status: str | None = None


async def sweep_once(*, announce_pipeline: bool = True) -> dict[str, int]:
    from nightjar.approvals.api import pending_for_dispatch
    from nightjar.approvals.service import expire_if_due
    from nightjar.incidents.service import add_timeline, announce_updated
    from nightjar.regimes.service import sweep as sweep_clocks
    from nightjar.services.pipeline import execute_approved_action, pipeline_health

    global _last_status
    stats = {"clocks": 0, "expired": 0, "closed": 0, "dispatched": 0}
    async with session_scope() as db:
        stats["clocks"] = await sweep_clocks(db)
        for p in (await db.execute(select(ActionProposal).where(ActionProposal.status == "pending"))).scalars():
            if await expire_if_due(db, p):
                stats["expired"] += 1
        cutoff = clock.now() - timedelta(hours=24)
        for inc in (
            await db.execute(select(Incident).where(Incident.status == "resolved", Incident.resolved_at < cutoff))
        ).scalars():
            inc.status = "closed"
            inc.version += 1
            await add_timeline(db, inc.org_id, inc.id, kind="status", title="Closed 24 hours after resolution")
            await announce_updated(db, inc, ["status"])
            stats["closed"] += 1
    for org_id, approval_id in await pending_for_dispatch():
        async with session_scope() as db:
            proposal = await db.get(ActionProposal, approval_id)
            if proposal is None or proposal.result is not None:
                continue
        if await execute_approved_action(org_id, approval_id):
            stats["dispatched"] += 1
    if announce_pipeline:
        h = await pipeline_health()
        async with session_scope() as db:
            for (org_id,) in (await db.execute(select(Organization.id))).all():
                await emit(
                    db,
                    org_id,
                    "pipeline.status",
                    {"status": h.status, "plainStatus": h.plain_status, "eventsPerMinute": h.events_per_minute},
                    permission="watch:read",
                )
        _last_status = h.status
    return stats


async def run_forever() -> None:
    while True:
        try:
            await sweep_once()
        except Exception as exc:
            log.warning("sweep_failed", error=type(exc).__name__)
        await asyncio.sleep(SWEEP_SECONDS)


def start() -> asyncio.Task[None]:
    return asyncio.create_task(run_forever(), name="nightjar-sweeper")


async def stop(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
