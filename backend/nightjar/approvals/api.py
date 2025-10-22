"""Public interface of the approval gate (builder A). Called by B's response agent and response tool server.

Policy (ADR 0006): every kind in `contracts.DESTRUCTIVE_ACTIONS` needs a `responder` approval; if the target is a
crown-jewel asset (or a privileged identity) it also needs an `executive` approval. Non-destructive kinds are
auto-approved and recorded. Approval yields a signed, single-use token bound to org + action + target
(`core.security.sign_approval_token`); the response tool server verifies it and calls `consume()`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from nightjar import clock
from nightjar.approvals.service import (
    CONTAINMENT,
    KIND_LABELS,
    TargetNotInIncidentError,
    approvals_of,
    consume_token,
    expire_if_due,
    request,
)
from nightjar.contracts import ActionResult, ApprovalRef, ProposedAction
from nightjar.core.events import emit
from nightjar.core.metrics import TOOL_DENIALS
from nightjar.db.models.soc import ActionProposal, Incident
from nightjar.db.session import session_scope
from nightjar.incidents.service import add_timeline, announce_updated, apply_status
from nightjar.services.audit import audit

__all__ = ["TargetNotInIncidentError", "consume", "get_approval", "record_result", "request_approval"]


def _ref(p: ActionProposal, decided: list) -> ApprovalRef:
    return ApprovalRef(
        approval_id=p.id,
        status=p.status,  # type: ignore[arg-type]
        required_roles=tuple(p.required_roles or ()),
        approvals=tuple(
            {"userId": str(a.user_id), "role": a.role, "decidedAt": a.decided_at.isoformat()}
            for a in decided
            if a.decision == "approve"
        ),
        token=p.token if p.status == "approved" and p.token_used_at is None else None,
    )


async def request_approval(
    org_id: uuid.UUID, incident_id: uuid.UUID, run_id: uuid.UUID | None, action: ProposedAction
) -> ApprovalRef:
    """Validate the target is one of the incident's entities (else ValueError: agents cannot act on things
    outside the incident), dedupe identical pending requests, store the proposal. Audit `action.proposed`.
    SSE `approval.requested` (permission `actions:read`)."""
    async with session_scope() as db:
        inc = await db.get(Incident, incident_id)
        if inc is None or inc.org_id != org_id:
            raise ValueError("incident not found in this organisation")
        p, _created = await request(
            db,
            inc,
            kind=action.kind,
            target=action.target,
            target_type=action.target_type,
            rationale=action.rationale,
            expected_effect=action.expected_effect,
            reversible=action.reversible,
            params=dict(action.params),
            run_id=run_id,
        )
        return _ref(p, await approvals_of(db, p.id))


async def get_approval(approval_id: uuid.UUID) -> ApprovalRef:
    async with session_scope() as db:
        p = await db.get(ActionProposal, approval_id)
        if p is None:
            raise KeyError(f"unknown approval {approval_id}")
        await expire_if_due(db, p)
        return _ref(p, await approvals_of(db, p.id))


async def consume(org_id: uuid.UUID, approval_id: uuid.UUID, nonce: str) -> bool:
    """Atomically mark the approval token used. False if already used, expired, rejected or unknown."""
    async with session_scope() as db:
        return await consume_token(db, org_id, approval_id, nonce)


async def record_result(org_id: uuid.UUID, result: ActionResult) -> None:
    """Store the outcome; audit `action.executed` / `action.refused`; SSE `action.executed` and
    `incident.updated`. A refused destructive call without approval is also a guardrail metric."""
    async with session_scope() as db:
        p = await db.get(ActionProposal, result.approval_id)
        if p is None or p.org_id != org_id:
            raise KeyError(f"unknown approval {result.approval_id}")
        inc = await db.get(Incident, p.incident_id)
        assert inc is not None
        label = KIND_LABELS.get(p.kind, p.kind)
        doc = {
            "executed": result.executed,
            "status": result.status,
            "detail": result.detail,
            "executedAt": result.executed_at.isoformat(),
            "toolCall": result.tool_call,
        }
        meta = {
            "actionLabel": label,
            "target": p.target,
            "kind": p.kind,
            "reference": inc.reference,
            "detail": result.detail,
        }
        if result.status == "refused":
            TOOL_DENIALS.labels("response", result.tool_call).inc()
            await audit(
                db,
                None,
                org_id=org_id,
                action="action.refused",
                resource_type="action",
                resource_id=p.id,
                actor_type="agent",
                actor_name="Response agent",
                metadata=meta,
            )
            if p.result is None or not p.result.get("executed"):
                p.result = doc
            await add_timeline(
                db,
                org_id,
                inc.id,
                kind="action",
                actor_type="agent",
                actor_name="Response colleague",
                title=f"Refused: {label.lower()} ({p.target})",
                body=result.detail,
                ref_id=p.id,
            )
        else:
            if p.status in ("executed", "failed") and p.result and p.result.get("executed"):
                return  # idempotent on redelivery
            p.result = doc
            p.status = "executed" if result.executed and result.status == "executed" else "failed"
            await audit(
                db,
                None,
                org_id=org_id,
                action="action.executed" if p.status == "executed" else "action.failed",
                resource_type="action",
                resource_id=p.id,
                actor_type="agent",
                actor_name="Response agent",
                metadata=meta,
            )
            changed = ["actions"]
            if p.status == "executed" and p.kind in CONTAINMENT:
                if inc.contained_at is None:
                    inc.contained_at = result.executed_at or clock.now()
                    changed.append("containedAt")
                if inc.status in ("new", "triage", "investigating"):
                    apply_status(inc, "containing")
                    changed.append("status")
            await add_timeline(
                db,
                org_id,
                inc.id,
                kind="action",
                actor_type="agent",
                actor_name="Response colleague",
                title=(
                    f"Done: {label.lower()} ({p.target})"
                    if p.status == "executed"
                    else f"Could not complete: {label.lower()} ({p.target})"
                ),
                body=result.detail,
                ref_id=p.id,
                executive=p.status == "executed",
                at=result.executed_at,
            )
            inc.version += 1
            await announce_updated(db, inc, changed)
        await emit(
            db,
            org_id,
            "action.executed",
            {
                "approvalId": str(p.id),
                "incidentId": str(inc.id),
                "kind": p.kind,
                "target": p.target,
                "status": p.status if result.status != "refused" else "refused",
            },
            permission="actions:read",
        )


async def pending_for_dispatch(org_id: uuid.UUID | None = None) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Approved, unexecuted, token still valid: (org_id, approval_id) pairs to hand (again) to the response
    agent (used by the API's sweeper when the agents were down at approval time)."""
    async with session_scope() as db:
        stmt = select(ActionProposal.org_id, ActionProposal.id).where(
            ActionProposal.status == "approved",
            ActionProposal.token_used_at.is_(None),
            ActionProposal.token_expires_at > clock.real_now(),
        )
        if org_id:
            stmt = stmt.where(ActionProposal.org_id == org_id)
        return [(o, i) for o, i in (await db.execute(stmt)).all()]
