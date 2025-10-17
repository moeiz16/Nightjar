"""Public interface of investigation runs (builder A). Called by B's orchestrator and agents."""

from __future__ import annotations

import uuid
from typing import Literal

from nightjar.contracts import AgentStep, EnrichmentResult, ProposedAction, RunRef, TriageVerdict


async def start_run(
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    trigger: Literal["auto", "manual", "rerun"],
    requested_by: uuid.UUID | None,
) -> RunRef:
    """Create a run (status `queued`). Audit `investigation.started`. SSE `investigation.started`."""
    raise NotImplementedError


async def mark_running(run_id: uuid.UUID) -> None:
    raise NotImplementedError


async def append_step(run_id: uuid.UUID, step: AgentStep) -> int:
    """Append one step; returns its sequence number (1-based, gap-free per run). SSE `investigation.step`
    (title, agent, kind only; detail is fetched by id). Steps are immutable once written."""
    raise NotImplementedError


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
    raise NotImplementedError
