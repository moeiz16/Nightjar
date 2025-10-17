"""Public interface of the agents (builder B). Called by A's incident and approval endpoints."""

from __future__ import annotations

import uuid
from typing import Literal

from nightjar.contracts import AgentCardInfo


async def start_investigation(
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    trigger: Literal["auto", "manual", "rerun"],
    requested_by: uuid.UUID | None,
) -> uuid.UUID:
    """Create the run through `investigations.api.start_run`, publish `nj.agents.tasks.<org>` and return the run
    id. The orchestrator hands the task to the Triage agent over A2A; triage → enrichment → response hand-offs
    are A2A tasks too. Auto investigations start when an incident is created (org setting `autoInvestigate`)."""
    raise NotImplementedError


async def execute_approved_action(org_id: uuid.UUID, approval_id: uuid.UUID) -> None:
    """Called by A right after the last required human approval. Publishes `nj.actions.approved.<org>`; the
    Response agent executes through the response tool server with the approval token. The outcome arrives via
    `approvals.api.record_result`."""
    raise NotImplementedError


def agent_cards() -> list[AgentCardInfo]:
    raise NotImplementedError


async def agents_health() -> list[AgentCardInfo]:
    """Cards with `healthy` from a live card fetch (1 s timeout each)."""
    raise NotImplementedError
