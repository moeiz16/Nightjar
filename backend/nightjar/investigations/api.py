"""Public interface of investigation runs (builder A). Called by B's orchestrator and agents.

The ordered step list of a run is the replayable decision log: steps are append-only with a gap-free sequence
per run, and each carries the hash of its exact input so a replay can be checked against the record.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.contracts import AgentStep, EnrichmentResult, ProposedAction, RunRef, TriageVerdict, to_doc
from nightjar.core.events import emit
from nightjar.core.metrics import AGENT_STEPS, ALERT_TO_TRIAGE
from nightjar.db.models.platform import User
from nightjar.db.models.soc import AgentStepRow, Incident, InvestigationRun
from nightjar.db.session import session_scope
from nightjar.incidents.service import add_timeline, announce_updated, apply_status

AGENT_NAMES = {
    "triage": "Triage colleague",
    "enrichment": "Enrichment colleague",
    "response": "Response colleague",
    "orchestrator": "Nightjar SOC",
}
VERDICT_TEXT = {
    "true_positive": "a real attack",
    "benign": "expected activity, not an attack",
    "false_positive": "a false alarm",
    "needs_review": "unclear: needs a person to review",
}


async def start_run(
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    trigger: Literal["auto", "manual", "rerun"],
    requested_by: uuid.UUID | None,
    reason: str | None = None,
) -> RunRef:
    """Create a run (status `queued`). Audit `investigation.started`. SSE `investigation.started`."""
    from nightjar.services.audit import audit

    async with session_scope() as db:
        inc = await db.get(Incident, incident_id)
        if inc is None or inc.org_id != org_id:
            raise KeyError(f"incident {incident_id} not found in this organisation")
        run = InvestigationRun(
            org_id=org_id,
            incident_id=incident_id,
            trigger=trigger,
            status="queued",
            requested_by=requested_by,
            reason=(reason or None),
            started_at=clock.now(),
            proposals=[],
        )
        db.add(run)
        await db.flush()
        user = await db.get(User, requested_by) if requested_by else None
        await audit(
            db,
            None,
            org_id=org_id,
            action="investigation.started",
            resource_type="incident",
            resource_id=inc.id,
            actor_id=user.id if user else None,
            actor_type="user" if user else "agent",
            actor_name=user.full_name if user else "Orchestrator",
            metadata={"reference": inc.reference, "runId": str(run.id), "trigger": trigger},
        )
        await add_timeline(
            db,
            org_id,
            inc.id,
            kind="agent",
            title="The triage colleague started looking into this",
            actor_type="user" if user else "agent",
            actor_name=user.full_name if user else AGENT_NAMES["triage"],
            ref_id=run.id,
            body=reason or None,
        )
        if inc.status == "new":
            apply_status(inc, "triage")
            inc.version += 1
            await announce_updated(db, inc, ["status"])
        await emit(
            db,
            org_id,
            "investigation.started",
            {"runId": str(run.id), "incidentId": str(inc.id)},
            permission="investigations:read",
        )
        return RunRef(run_id=run.id, incident_id=inc.id, status="queued")


async def mark_running(run_id: uuid.UUID) -> None:
    async with session_scope() as db:
        run = await db.get(InvestigationRun, run_id)
        if run is None:
            raise KeyError(f"unknown run {run_id}")
        if run.status == "queued":
            run.status = "running"


async def append_step(run_id: uuid.UUID, step: AgentStep) -> int:
    """Append one step; returns its sequence number (1-based, gap-free per run). SSE `investigation.step`
    (title, agent, kind only; detail is fetched by id). Steps are immutable once written."""
    for attempt in range(8):
        try:
            return await _append(run_id, step)
        except IntegrityError:  # another agent process took the same seq; take the next one
            if attempt == 7:
                raise
    raise AssertionError("unreachable")


async def _append(run_id: uuid.UUID, step: AgentStep) -> int:
    async with session_scope() as db:
        run = await db.get(InvestigationRun, run_id)
        if run is None:
            raise KeyError(f"unknown run {run_id}")
        last = (await db.execute(select(func.max(AgentStepRow.seq)).where(AgentStepRow.run_id == run_id))).scalar()
        seq = int(last or 0) + 1
        db.add(
            AgentStepRow(
                run_id=run_id,
                org_id=run.org_id,
                seq=seq,
                agent=step.agent,
                kind=step.kind,
                title=step.title[:400],
                started_at=step.started_at,
                duration_ms=int(step.duration_ms),
                tool=step.tool,
                a2a_task_id=step.a2a_task_id,
                model=step.model,
                input_hash=step.input_hash,
                flags=list(step.flags),
                detail=to_doc(step.detail) if step.detail else {},
            )
        )
        await db.flush()
        if run.status == "queued":
            run.status = "running"
        if step.kind == "handoff_out":
            await add_timeline(
                db,
                run.org_id,
                run.incident_id,
                kind="handoff",
                title=step.title,
                actor_type="agent",
                actor_name=AGENT_NAMES.get(step.agent, step.agent),
                ref_id=run_id,
                at=step.started_at,
            )
        await emit(
            db,
            run.org_id,
            "investigation.step",
            {
                "runId": str(run_id),
                "incidentId": str(run.incident_id),
                "seq": seq,
                "agent": step.agent,
                "kind": step.kind,
                "title": step.title,
            },
            permission="investigations:read",
        )
        AGENT_STEPS.labels(step.agent, step.kind).inc()
        return seq


async def finish_run(
    run_id: uuid.UUID,
    *,
    status: Literal["completed", "failed", "cancelled"],
    verdict: TriageVerdict | None,
    enrichment: EnrichmentResult | None,
    proposals: tuple[ProposedAction, ...],
    error: str | None = None,
) -> None:
    """Close the run and apply the verdict to the incident: `true_positive` → stage `investigate`, status
    `investigating`; `benign`/`false_positive` → status `resolved` with reason (reversible by a human);
    `needs_review` → stays in triage, flagged. Records alert-to-triage time (NFR, metric
    `nightjar_alert_to_triage_seconds`). Proposes regime applicability from `verdict.facts` (incident
    clock §1.12). Audit `investigation.completed`. SSE `investigation.completed`, `incident.updated`."""
    async with session_scope() as db:
        run = await db.get(InvestigationRun, run_id)
        if run is None:
            raise KeyError(f"unknown run {run_id}")
        if run.status in ("completed", "failed", "cancelled"):
            return  # idempotent on redelivery
        run.status = status
        run.finished_at = clock.now()
        run.verdict = to_doc(verdict) if verdict else None
        run.enrichment = to_doc(enrichment) if enrichment else None
        run.proposals = to_doc(list(proposals))
        run.error = error
        inc = await db.get(Incident, run.incident_id)
        assert inc is not None
        await apply_verdict(db, inc, run, verdict)
        duration = (run.finished_at - run.started_at).total_seconds()
        await emit(
            db,
            run.org_id,
            "investigation.completed",
            {
                "runId": str(run.id),
                "incidentId": str(inc.id),
                "verdict": verdict.verdict if verdict else None,
                "durationSeconds": round(duration, 1),
            },
            permission="investigations:read",
        )


async def apply_verdict(db: AsyncSession, inc: Incident, run: InvestigationRun, verdict: TriageVerdict | None) -> None:
    from nightjar.regimes.service import propose_for_incident
    from nightjar.services.audit import audit

    changed: list[str] = []
    if run.status != "completed" or verdict is None:
        await add_timeline(
            db,
            inc.org_id,
            inc.id,
            kind="agent",
            actor_type="agent",
            actor_name=AGENT_NAMES["triage"],
            title="The investigation stopped before a conclusion"
            if run.status != "completed"
            else "The investigation finished without a conclusion",
            body=run.error,
            ref_id=run.id,
        )
    else:
        first = inc.triaged_at is None
        inc.verdict = verdict.verdict
        inc.verdict_confidence = float(verdict.confidence)
        inc.verdict_summary = verdict.summary
        inc.model_probability = verdict.model_probability
        inc.triage_run_id = run.id
        if verdict.facts is not None:
            inc.facts = to_doc(verdict.facts)
        changed.append("verdict")
        if first:
            inc.triaged_at = clock.now()
            ALERT_TO_TRIAGE.observe(max(0.0, (inc.triaged_at - inc.created_at).total_seconds()))
        if verdict.verdict == "true_positive" and inc.status in ("new", "triage"):
            apply_status(inc, "investigating")
            changed.append("status")
        elif verdict.verdict in ("benign", "false_positive") and inc.status in ("new", "triage", "investigating"):
            apply_status(inc, "resolved")
            inc.resolution = {
                "reason": verdict.verdict,
                "note": verdict.summary,
                "by": AGENT_NAMES["triage"],
                "at": clock.now().isoformat(),
                "reversible": True,
            }
            changed.append("status")
        elif verdict.verdict == "needs_review" and inc.status == "new":
            apply_status(inc, "triage")
            changed.append("status")
        await add_timeline(
            db,
            inc.org_id,
            inc.id,
            kind="agent",
            actor_type="agent",
            actor_name=AGENT_NAMES["triage"],
            title=f"The triage colleague concluded this is {VERDICT_TEXT.get(verdict.verdict, verdict.verdict)} "
            f"({round(verdict.confidence * 100)}% confident)",
            body=verdict.summary,
            ref_id=run.id,
            executive=True,
        )
        await propose_for_incident(db, inc)
    await audit(
        db,
        None,
        org_id=inc.org_id,
        action="investigation.completed",
        resource_type="incident",
        resource_id=inc.id,
        actor_type="agent",
        actor_name="Triage agent",
        metadata={
            "reference": inc.reference,
            "runId": str(run.id),
            "status": run.status,
            "verdict": verdict.verdict if verdict else None,
        },
    )
    inc.version += 1
    await announce_updated(db, inc, changed or ["investigation"])


# ── Serialisers (§1.9) ───────────────────────────────────────────────────────
def step_doc(s: AgentStepRow, *, detail: bool = False) -> dict:
    doc = {
        "seq": s.seq,
        "agent": s.agent,
        "kind": s.kind,
        "title": s.title,
        "startedAt": s.started_at.isoformat(),
        "durationMs": s.duration_ms,
        "tool": s.tool,
        "a2aTaskId": s.a2a_task_id,
        "model": s.model,
        "inputHash": s.input_hash,
        "flags": list(s.flags or []),
    }
    if detail:
        doc["detail"] = s.detail or {}
    return doc


async def run_summaries(db: AsyncSession, runs: list[InvestigationRun]) -> list[dict]:
    from nightjar.services.auth import user_out

    if not runs:
        return []
    counts = dict(
        (
            await db.execute(
                select(AgentStepRow.run_id, func.count())
                .where(AgentStepRow.run_id.in_([r.id for r in runs]))
                .group_by(AgentStepRow.run_id)
            )
        ).all()
    )
    uids = {r.requested_by for r in runs if r.requested_by}
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    out = []
    for r in runs:
        end = r.finished_at
        out.append(
            {
                "id": str(r.id),
                "incidentId": str(r.incident_id),
                "trigger": r.trigger,
                "status": r.status,
                "startedAt": r.started_at.isoformat(),
                "finishedAt": end.isoformat() if end else None,
                "verdict": (r.verdict or {}).get("verdict"),
                "durationSeconds": round((end - r.started_at).total_seconds(), 1) if end else None,
                "stepCount": int(counts.get(r.id, 0)),
                "requestedBy": user_out(users[r.requested_by]) if r.requested_by in users else None,
                "reason": r.reason,
            }
        )
    return out


async def run_full(db: AsyncSession, run: InvestigationRun) -> dict:
    from nightjar.approvals.service import proposals_for

    (summary,) = await run_summaries(db, [run])
    steps = (
        (await db.execute(select(AgentStepRow).where(AgentStepRow.run_id == run.id).order_by(AgentStepRow.seq)))
        .scalars()
        .all()
    )
    return {
        **summary,
        "steps": [step_doc(s) for s in steps],
        "verdictDetail": run.verdict,
        "enrichment": run.enrichment,
        "proposals": await proposals_for(db, run.org_id, run_id=run.id),
        "error": run.error,
    }
