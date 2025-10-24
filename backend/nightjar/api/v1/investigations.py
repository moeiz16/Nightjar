"""Investigation runs and their replayable step log (API contract §1.9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import AppError
from nightjar.db.models.soc import AgentStepRow, InvestigationRun
from nightjar.investigations.api import run_full, step_doc

router = APIRouter(prefix="/investigations", tags=["investigations"])


async def _run(db: AsyncSession, org_id: uuid.UUID, run_id: str) -> InvestigationRun:
    try:
        uid = uuid.UUID(run_id)
    except ValueError:
        uid = None
    run = await db.get(InvestigationRun, uid) if uid else None
    if run is None or run.org_id != org_id:
        raise AppError(404, "not_found", "Investigation not found", "Investigation not found.")
    return run


@router.get("/{run_id}")
async def get_run(
    run_id: str, ctx: RequestContext = Depends(require_perm("investigations:read")), db: AsyncSession = Depends(get_db)
) -> dict:
    return await run_full(db, await _run(db, ctx.org_id, run_id))


@router.get("/{run_id}/steps/{seq}")
async def get_step(
    run_id: str,
    seq: int,
    ctx: RequestContext = Depends(require_perm("investigations:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    run = await _run(db, ctx.org_id, run_id)
    step = (
        await db.execute(select(AgentStepRow).where(AgentStepRow.run_id == run.id, AgentStepRow.seq == seq))
    ).scalar_one_or_none()
    if step is None:
        raise AppError(404, "not_found", "Step not found", "Step not found.")
    return step_doc(step, detail=True)


@router.get("/{run_id}/replay")
async def replay(
    run_id: str, ctx: RequestContext = Depends(require_perm("investigations:read")), db: AsyncSession = Depends(get_db)
) -> dict:
    """Re-runs the offline policy over the recorded inputs (builder B's `agents.replay_verify`) and compares; if
    the agents are not available, reports whether the record itself is complete (every tool and model step
    carries the hash of its exact input)."""
    run = await _run(db, ctx.org_id, run_id)
    steps = list(
        (
            await db.execute(select(AgentStepRow).where(AgentStepRow.run_id == run.id).order_by(AgentStepRow.seq))
        ).scalars()
    )
    recorded = (run.verdict or {}).get("verdict")
    verification = None
    try:
        from nightjar.agents import api as agents

        fn = getattr(agents, "replay_verify", None)
        if fn is not None:
            verification = dict(await fn(ctx.org_id, run.id))
            verification.setdefault("method", "replayed")
    except Exception:
        verification = None
    if verification is None:
        needs_hash = [s for s in steps if s.kind in ("tool_call", "verdict") or s.model]
        verification = {
            "inputHashesMatch": all(s.input_hash for s in needs_hash),
            "offlineReplayVerdict": recorded,
            "method": "recorded",
        }
    verification["recordedVerdict"] = recorded
    return {"runId": str(run.id), "steps": [step_doc(s, detail=True) for s in steps], "verification": verification}
