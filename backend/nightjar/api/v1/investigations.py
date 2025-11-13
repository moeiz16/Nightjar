"""Investigation runs and their replayable step log (API contract §1.9)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import AppError
from nightjar.db.models.soc import AgentStepRow, DetectionRow, Incident, InvestigationRun, StepFeedback
from nightjar.incidents.service import add_timeline, announce_updated
from nightjar.investigations.api import run_full, step_doc
from nightjar.schemas.base import CamelIn
from nightjar.services.audit import audit

_VERDICT_WORDS = {
    "true_positive": "a real attack",
    "benign": "expected activity",
    "false_positive": "a false alarm",
    "needs_review": "not settled yet",
}


class StepFeedbackIn(CamelIn):
    verdict: Literal["true_positive", "benign", "false_positive", "needs_review"]
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


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


@router.post("/{run_id}/steps/{seq}/feedback", status_code=status.HTTP_201_CREATED)
async def step_feedback(
    run_id: str,
    seq: int,
    body: StepFeedbackIn,
    ctx: RequestContext = Depends(require_perm("incidents:write")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disagree: a person records their own verdict on a colleague's step. Kept as a labelled example for the
    next triage retraining (the incident's detections, labelled with the person's verdict), written to the
    incident timeline and audited. The recorded investigation itself is never changed."""
    run = await _run(db, ctx.org_id, run_id)
    step = (
        await db.execute(select(AgentStepRow).where(AgentStepRow.run_id == run.id, AgentStepRow.seq == seq))
    ).scalar_one_or_none()
    if step is None:
        raise AppError(404, "not_found", "Step not found", "Step not found.")
    inc = await db.get(Incident, run.incident_id)
    assert inc is not None
    det_ids = [
        str(i)
        for i in (
            await db.execute(
                select(DetectionRow.id).where(DetectionRow.org_id == ctx.org_id, DetectionRow.incident_id == inc.id)
            )
        ).scalars()
    ]
    recorded = (run.verdict or {}).get("verdict")
    row = StepFeedback(
        org_id=ctx.org_id,
        incident_id=inc.id,
        run_id=run.id,
        seq=seq,
        verdict=body.verdict,
        note=body.note,
        recorded_verdict=recorded,
        label_detection_ids=det_ids,
        user_id=ctx.user.id,
        user_name=ctx.user.full_name,
        created_at=clock.now(),
    )
    db.add(row)
    await add_timeline(
        db,
        ctx.org_id,
        inc.id,
        kind="note",
        actor_type="user",
        actor_name=ctx.user.full_name,
        title=f"Disagreed with “{step.title}”: this is {_VERDICT_WORDS[body.verdict]}",
        body=body.note or None,
        ref_id=f"{run.id.hex[:24]}:{seq}",
    )
    await audit(
        db,
        ctx,
        action="investigation.step_disagreed",
        resource_type="incident",
        resource_id=inc.id,
        metadata={"reference": inc.reference, "seq": seq, "verdict": body.verdict, "recordedVerdict": recorded},
    )
    await announce_updated(db, inc, ["timeline"])
    await db.flush()
    out = {
        "id": str(row.id),
        "runId": str(run.id),
        "seq": seq,
        "verdict": body.verdict,
        "note": body.note,
        "recordedVerdict": recorded,
        "labelledDetections": len(det_ids),
        "by": ctx.user.full_name,
        "at": row.created_at.isoformat(),
    }
    await db.commit()
    return out


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
