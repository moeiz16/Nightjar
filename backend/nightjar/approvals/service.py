"""The human approval gate (ADR 0006): proposals, role checks, separation of duties, signed single-use tokens.

* Every kind in `contracts.DESTRUCTIVE_ACTIONS` needs a `responder`; a crown-jewel asset or a privileged identity
  also needs an `executive` (two-person rule). Non-destructive kinds are auto-approved and recorded.
* Nobody may be the only approver of an action they requested (`409 self_approval`).
* Pending proposals expire after 24 hours (business clock); the execution token lives `APPROVAL_TTL_MINUTES`
  (real clock) and is single use.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.contracts import DESTRUCTIVE_ACTIONS
from nightjar.core.config import get_settings
from nightjar.core.errors import AppError, conflict, forbidden
from nightjar.core.events import emit
from nightjar.core.security import sign_approval_token
from nightjar.db.models.platform import Organization, User
from nightjar.db.models.soc import ActionApproval, ActionProposal, DetectionRow, Incident
from nightjar.directory.api import assets_by_names_or_ips, identities_by_keys, is_crown_jewel
from nightjar.incidents.service import add_timeline, announce_updated, crown_tags, merge_entities
from nightjar.services.audit import audit

PENDING_TTL = timedelta(hours=24)
KIND_LABELS = {
    "isolate_host": "Isolate this computer from the network",
    "disable_account": "Turn off this account",
    "revoke_sessions": "Sign this person out everywhere",
    "reset_password": "Make this person choose a new password",
    "revoke_access_key": "Withdraw this cloud access key",
    "block_ip": "Block this internet address",
    "quarantine_email": "Move this email out of every inbox",
    "release_host": "Reconnect this computer to the network",
    "enable_account": "Turn this account back on",
    "unblock_ip": "Unblock this internet address",
    "open_ticket": "Open a ticket for the service desk",
    "notify_owner": "Let the owner know",
    "collect_triage_package": "Collect a snapshot for investigation",
    "add_to_watchlist": "Add to the watchlist",
}
TARGET_TYPES = {
    "isolate_host": "host",
    "release_host": "host",
    "disable_account": "user",
    "enable_account": "user",
    "revoke_sessions": "user",
    "reset_password": "user",
    "revoke_access_key": "access_key",
    "block_ip": "ip",
    "unblock_ip": "ip",
    "quarantine_email": "email",
}
CONTAINMENT = frozenset(
    {
        "isolate_host",
        "disable_account",
        "revoke_sessions",
        "reset_password",
        "revoke_access_key",
        "block_ip",
        "quarantine_email",
    }
)
ROLE_PERMISSION = {"responder": "actions:approve", "executive": "actions:approve_crown_jewel"}
ROLE_LABELS = {"responder": "Incident responder", "executive": "CISO / executive"}


class TargetNotInIncidentError(ValueError):
    pass


def _norm(value: str) -> str:
    return value.strip().split("\\")[-1].lower()


async def incident_targets(db: AsyncSession, inc: Incident) -> dict[str, set[str]]:
    """Every value an action may target, by target type (from the incident's entity sets)."""
    ents = merge_entities(inc.entities, None)
    targets: dict[str, set[str]] = {
        "host": {_norm(h) for h in ents["hosts"] + ents["cloudAccounts"]},
        "user": {_norm(u) for u in ents["users"] + ents["emails"]},
        "access_key": {k.strip().lower() for k in ents["accessKeys"]},
        "ip": set(),
        "email": {e.strip().lower() for e in ents["emails"]},
        "ticket": {inc.reference.lower(), str(inc.id)},
    }
    for ip in ents["ips"]:
        try:
            targets["ip"].add(str(ipaddress.ip_address(ip.strip())))
        except ValueError:
            continue
    # usernames whose email appears in the incident, and message ids from the detections' matched fields
    for ident in await identities_by_keys(db, inc.org_id, ents["users"] + ents["emails"]):
        targets["user"].add(ident.username.lower())
    for fields in (await db.execute(select(DetectionRow.fields).where(DetectionRow.incident_id == inc.id))).scalars():
        for key, value in (fields or {}).items():
            if "message" in key.lower() and isinstance(value, str) and "id" in key.lower():
                targets["email"].add(value.strip().lower())
    return targets


def _target_key(target_type: str, target: str) -> str:
    if target_type == "ip":
        try:
            return str(ipaddress.ip_address(target.strip()))
        except ValueError:
            return target.strip()
    if target_type in ("host", "user"):
        return _norm(target)
    return target.strip().lower()


async def request(
    db: AsyncSession,
    inc: Incident,
    *,
    kind: str,
    target: str,
    target_type: str | None,
    rationale: str,
    expected_effect: str = "",
    reversible: bool = True,
    params: dict[str, Any] | None = None,
    run_id: uuid.UUID | None = None,
    proposed_by: User | None = None,
    agent_name: str = "Response colleague",
) -> tuple[ActionProposal, bool]:
    """Validate, dedupe and store a proposal. Returns (proposal, created)."""
    if kind not in KIND_LABELS:
        raise ValueError(f"unknown action kind {kind!r}")
    expected_type = TARGET_TYPES.get(kind)
    target_type = expected_type or target_type or "ticket"
    if expected_type and target_type != expected_type:
        raise TargetNotInIncidentError(f"{kind} needs a {expected_type} target")
    targets = await incident_targets(db, inc)
    key = _target_key(target_type, target)
    destructive = kind in DESTRUCTIVE_ACTIONS
    if destructive and key not in targets.get(target_type, set()):
        raise TargetNotInIncidentError(f"{target!r} is not one of {inc.reference}'s {target_type} entities")
    existing = (
        await db.execute(
            select(ActionProposal).where(
                ActionProposal.org_id == inc.org_id,
                ActionProposal.incident_id == inc.id,
                ActionProposal.kind == kind,
                ActionProposal.target == target.strip(),
                ActionProposal.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    crown = False
    if target_type == "host":
        org = await db.get(Organization, inc.org_id)
        crown = any(is_crown_jewel(a, crown_tags(org)) for a in await assets_by_names_or_ips(db, inc.org_id, [target]))
    elif target_type == "user":
        crown = any(i.privileged for i in await identities_by_keys(db, inc.org_id, [target]))
    roles = (["responder", "executive"] if crown else ["responder"]) if destructive else []
    now = clock.now()
    p = ActionProposal(
        org_id=inc.org_id,
        incident_id=inc.id,
        run_id=run_id,
        kind=kind,
        target=target.strip(),
        target_type=target_type,
        rationale=rationale[:4000],
        expected_effect=expected_effect[:2000],
        reversible=reversible,
        destructive=destructive,
        crown_jewel=crown,
        params=params or {},
        proposed_by_type="user" if proposed_by else "agent",
        proposed_by_name=proposed_by.full_name if proposed_by else agent_name,
        proposed_by_user_id=proposed_by.id if proposed_by else None,
        status="pending" if destructive else "auto_approved",
        required_roles=roles,
        expires_at=now + PENDING_TTL,
    )
    db.add(p)
    await db.flush()
    label = KIND_LABELS[kind]
    await audit(
        db,
        None,
        org_id=inc.org_id,
        action="action.proposed",
        resource_type="action",
        resource_id=p.id,
        actor_id=proposed_by.id if proposed_by else None,
        actor_type="user" if proposed_by else "agent",
        actor_name=proposed_by.full_name if proposed_by else "Response agent",
        metadata={
            "actionLabel": label,
            "target": p.target,
            "kind": kind,
            "reference": inc.reference,
            "requiredRoles": roles,
        },
    )
    if destructive:
        who = " and ".join(ROLE_LABELS[r].lower() for r in roles)
        await add_timeline(
            db,
            inc.org_id,
            inc.id,
            kind="approval",
            title=f"Proposed: {label.lower()} ({p.target})",
            actor_type="user" if proposed_by else "agent",
            actor_name=p.proposed_by_name,
            body=f"Waiting for approval by an {who}." if roles else None,
            ref_id=p.id,
        )
        await emit(
            db,
            inc.org_id,
            "approval.requested",
            {
                "approvalId": str(p.id),
                "incidentId": str(inc.id),
                "kind": kind,
                "kindLabel": label,
                "target": p.target,
                "requiredRoles": roles,
            },
            permission="actions:read",
        )
    return p, True


async def get_proposal(db: AsyncSession, org_id: uuid.UUID, approval_id: Any) -> ActionProposal:
    try:
        uid = uuid.UUID(str(approval_id))
    except ValueError:
        uid = None
    p = await db.get(ActionProposal, uid) if uid else None
    if p is None or p.org_id != org_id:
        raise AppError(404, "not_found", "Approval not found", "Approval not found.")
    return p


async def approvals_of(db: AsyncSession, proposal_id: uuid.UUID) -> list[ActionApproval]:
    return list(
        (
            await db.execute(
                select(ActionApproval)
                .where(ActionApproval.proposal_id == proposal_id)
                .order_by(ActionApproval.decided_at)
            )
        ).scalars()
    )


async def expire_if_due(db: AsyncSession, p: ActionProposal) -> bool:
    if p.status == "pending" and p.expires_at <= clock.now():
        p.status = "expired"
        p.decided_at = clock.now()
        await emit(
            db,
            p.org_id,
            "approval.decided",
            {"approvalId": str(p.id), "incidentId": str(p.incident_id), "status": "expired"},
            permission="actions:read",
        )
        return True
    return False


def remaining_roles(p: ActionProposal, decided: list[ActionApproval]) -> list[str]:
    done = {a.role for a in decided if a.decision == "approve"}
    return [r for r in p.required_roles or [] if r not in done]


async def decide(
    db: AsyncSession,
    p: ActionProposal,
    *,
    user: User,
    permissions: set[str],
    approve: bool,
    note: str | None,
    ip: str | None = None,
) -> ActionProposal:
    """A person approves or declines. Raises 403/409/410 per §1.10."""
    if await expire_if_due(db, p):
        await db.commit()
        raise AppError(410, "expired", "Approval expired", "This request expired after 24 hours. Propose it again.")
    if p.status != "pending":
        raise conflict("already_decided", "This request has already been decided.")
    decided = await approvals_of(db, p.id)
    remaining = remaining_roles(p, decided)
    role = next((r for r in remaining if ROLE_PERMISSION[r] in permissions), None)
    if role is None:
        if any(ROLE_PERMISSION[r] in permissions for r in p.required_roles or []):
            raise conflict("already_decided", "Your part of this approval has already been given.")
        raise forbidden("Your role cannot approve this action.")
    if any(a.user_id == user.id for a in decided):
        raise conflict("already_decided", "You have already approved this request.")
    if approve and p.proposed_by_user_id == user.id:
        others = [a for a in decided if a.user_id != user.id]
        if len(remaining) == 1 and not others:
            raise AppError(
                409,
                "self_approval",
                "Conflict",
                "You proposed this action, so someone else has to approve it.",
            )
    now = clock.now()
    db.add(
        ActionApproval(
            proposal_id=p.id,
            org_id=p.org_id,
            user_id=user.id,
            user_name=user.full_name,
            role=role,
            decision="approve" if approve else "reject",
            note=(note or None),
            decided_at=now,
        )
    )
    await db.flush()
    inc = await db.get(Incident, p.incident_id)
    assert inc is not None
    label = KIND_LABELS.get(p.kind, p.kind)
    meta = {"actionLabel": label, "target": p.target, "kind": p.kind, "reference": inc.reference, "role": role}
    if not approve:
        p.status = "rejected"
        p.decided_at = now
        await audit(
            db,
            None,
            org_id=p.org_id,
            action="action.rejected",
            resource_type="action",
            resource_id=p.id,
            actor_id=user.id,
            actor_name=user.full_name,
            ip=ip,
            metadata={**meta, "reason": note},
        )
        await add_timeline(
            db,
            p.org_id,
            inc.id,
            kind="approval",
            title=f"Declined: {label.lower()} ({p.target})",
            actor_type="user",
            actor_name=user.full_name,
            body=note,
            ref_id=p.id,
        )
    else:
        left = [r for r in remaining if r != role]
        await audit(
            db,
            None,
            org_id=p.org_id,
            action="action.approved",
            resource_type="action",
            resource_id=p.id,
            actor_id=user.id,
            actor_name=user.full_name,
            ip=ip,
            metadata={**meta, "final": not left},
        )
        if left:
            await add_timeline(
                db,
                p.org_id,
                inc.id,
                kind="approval",
                title=f"{user.full_name} approved: {label.lower()} ({p.target}); waiting for the "
                f"{' and '.join(ROLE_LABELS[r].lower() for r in left)}",
                actor_type="user",
                actor_name=user.full_name,
                body=note,
                ref_id=p.id,
            )
        else:
            p.status = "approved"
            p.decided_at = now
            issue_token(p)
            await add_timeline(
                db,
                p.org_id,
                inc.id,
                kind="approval",
                title=f"Approved: {label.lower()} ({p.target})",
                actor_type="user",
                actor_name=user.full_name,
                body=note,
                ref_id=p.id,
                executive=True,
            )
    await emit(
        db,
        p.org_id,
        "approval.decided",
        {"approvalId": str(p.id), "incidentId": str(p.incident_id), "status": p.status},
        permission="actions:read",
    )
    inc.version += 1
    await announce_updated(db, inc, ["approvals"])
    return p


def issue_token(p: ActionProposal) -> str:
    token, claims = sign_approval_token(
        approval_id=str(p.id),
        org_id=str(p.org_id),
        action=p.kind,
        target=p.target,
        ttl=timedelta(minutes=get_settings().approval_ttl_minutes),
    )
    p.token = token
    p.token_nonce = claims.nonce
    p.token_expires_at = claims.expires_at
    p.token_used_at = None
    return token


async def consume_token(db: AsyncSession, org_id: uuid.UUID, approval_id: uuid.UUID, nonce: str) -> bool:
    res = await db.execute(
        update(ActionProposal)
        .where(
            ActionProposal.id == approval_id,
            ActionProposal.org_id == org_id,
            ActionProposal.status == "approved",
            ActionProposal.token_nonce == nonce,
            ActionProposal.token_used_at.is_(None),
            ActionProposal.token_expires_at > clock.real_now(),
        )
        .values(token_used_at=clock.real_now(), token=None)
    )
    return (res.rowcount or 0) == 1  # type: ignore[attr-defined]


# ── Serialisers (§1.10) ──────────────────────────────────────────────────────
def proposal_doc(p: ActionProposal, decided: list[ActionApproval], reference: str) -> dict:
    rejection = next((a for a in decided if a.decision == "reject"), None)
    return {
        "id": str(p.id),
        "incidentId": str(p.incident_id),
        "incidentReference": reference,
        "runId": str(p.run_id) if p.run_id else None,
        "kind": p.kind,
        "kindLabel": KIND_LABELS.get(p.kind, p.kind),
        "target": p.target,
        "targetType": p.target_type,
        "rationale": p.rationale,
        "expectedEffect": p.expected_effect,
        "reversible": p.reversible,
        "destructive": p.destructive,
        "crownJewel": p.crown_jewel,
        "proposedBy": {"type": p.proposed_by_type, "name": p.proposed_by_name},
        "proposedByUserId": str(p.proposed_by_user_id) if p.proposed_by_user_id else None,
        "status": p.status,
        "requiredRoles": list(p.required_roles or []),
        "awaitingRoles": remaining_roles(p, decided) if p.status == "pending" else [],
        "approvals": [
            {
                "userId": str(a.user_id),
                "name": a.user_name,
                "role": a.role,
                "decidedAt": a.decided_at.isoformat(),
                "note": a.note,
            }
            for a in decided
            if a.decision == "approve"
        ],
        "rejection": {
            "userId": str(rejection.user_id),
            "name": rejection.user_name,
            "reason": rejection.note,
            "decidedAt": rejection.decided_at.isoformat(),
        }
        if rejection
        else None,
        "result": p.result,
        "createdAt": p.created_at.isoformat() if p.created_at else None,
        "expiresAt": p.expires_at.isoformat(),
    }


async def docs_for(db: AsyncSession, proposals: list[ActionProposal]) -> list[dict]:
    if not proposals:
        return []
    ids = [p.id for p in proposals]
    rows = (
        (
            await db.execute(
                select(ActionApproval).where(ActionApproval.proposal_id.in_(ids)).order_by(ActionApproval.decided_at)
            )
        )
        .scalars()
        .all()
    )
    by: dict[uuid.UUID, list[ActionApproval]] = {}
    for a in rows:
        by.setdefault(a.proposal_id, []).append(a)
    inc_ids = {p.incident_id for p in proposals}
    refs = dict((await db.execute(select(Incident.id, Incident.reference).where(Incident.id.in_(inc_ids)))).all())
    return [proposal_doc(p, by.get(p.id, []), refs.get(p.incident_id, "")) for p in proposals]


async def proposals_for(
    db: AsyncSession, org_id: uuid.UUID, *, incident_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None
) -> list[dict]:
    stmt = select(ActionProposal).where(ActionProposal.org_id == org_id)
    if incident_id:
        stmt = stmt.where(ActionProposal.incident_id == incident_id)
    if run_id:
        stmt = stmt.where(ActionProposal.run_id == run_id)
    rows = list((await db.execute(stmt.order_by(ActionProposal.created_at))).scalars())
    return await docs_for(db, rows)
