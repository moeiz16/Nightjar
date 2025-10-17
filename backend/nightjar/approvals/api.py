"""Public interface of the approval gate (builder A). Called by B's response agent and response tool server.

Policy (ADR 0006): every kind in `contracts.DESTRUCTIVE_ACTIONS` needs a `responder` approval; if the target is a
crown-jewel asset (or a privileged identity) it also needs an `executive` approval. Non-destructive kinds are
auto-approved and recorded. Approval yields a signed, single-use token bound to org + action + target
(`core.security.sign_approval_token`); the response tool server verifies it and calls `consume()`.
"""

from __future__ import annotations

import uuid

from nightjar.contracts import ActionResult, ApprovalRef, ProposedAction


async def request_approval(
    org_id: uuid.UUID, incident_id: uuid.UUID, run_id: uuid.UUID | None, action: ProposedAction
) -> ApprovalRef:
    """Validate the target is one of the incident's entities (else ValueError: agents cannot act on things
    outside the incident), dedupe identical pending requests, store the proposal. Audit `action.proposed`.
    SSE `approval.requested` (permission `actions:approve`)."""
    raise NotImplementedError


async def get_approval(approval_id: uuid.UUID) -> ApprovalRef:
    raise NotImplementedError


async def consume(org_id: uuid.UUID, approval_id: uuid.UUID, nonce: str) -> bool:
    """Atomically mark the approval token used. False if already used, expired, rejected or unknown."""
    raise NotImplementedError


async def record_result(org_id: uuid.UUID, result: ActionResult) -> None:
    """Store the outcome; audit `action.executed` / `action.refused`; SSE `action.executed` and
    `incident.updated`. A refused destructive call without approval is also a guardrail metric."""
    raise NotImplementedError
