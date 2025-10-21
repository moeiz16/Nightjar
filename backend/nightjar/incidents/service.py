"""Incident store internals shared by the B-facing `incidents.api` and the HTTP routers: timeline rows,
references, status transitions and the wire serialisers (API contract §1.7, §1.8)."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.contracts import SEVERITY_ORDER
from nightjar.core.errors import AppError
from nightjar.core.events import emit
from nightjar.db.models.platform import Organization, User
from nightjar.db.models.soc import (
    ActionProposal,
    DetectionRow,
    Identity,
    Incident,
    IncidentEvent,
    TriageScoreRow,
)
from nightjar.directory.api import (
    asset_doc,
    assets_by_names_or_ips,
    identities_by_keys,
    identity_doc,
    is_crown_jewel,
)
from nightjar.services.pipeline import kev_docs, tactic_label

FIRST_REFERENCE = 1001
OPEN_STATUSES = ("new", "triage", "investigating", "containing", "monitoring")
STATUS_ORDER = ("new", "triage", "investigating", "containing", "monitoring", "resolved", "closed")
STAGE_FOR_STATUS = {
    "new": "triage",
    "triage": "triage",
    "investigating": "investigate",
    "containing": "contain",
    "monitoring": "contain",
}
RESOLUTION_REASONS = ("contained", "benign", "false_positive", "duplicate", "merged")
SEVERITIES = ("info", "low", "medium", "high", "critical")
ENTITY_KEYS = ("hosts", "users", "ips", "cloudAccounts", "accessKeys", "hashes", "emails")
STATUS_LABELS = {
    "new": "New",
    "triage": "In triage",
    "investigating": "Investigating",
    "containing": "Containing",
    "monitoring": "Monitoring",
    "resolved": "Resolved",
    "closed": "Closed",
}


def bump(severity: str, levels: int = 1) -> str:
    return SEVERITIES[min(len(SEVERITIES) - 1, SEVERITY_ORDER.get(severity, 2) + levels)]


def max_severity(values: Iterable[str]) -> str:
    vals = [v for v in values if v in SEVERITY_ORDER]
    return max(vals, key=lambda v: SEVERITY_ORDER[v]) if vals else "low"


def merge_entities(a: dict | None, b: dict | None) -> dict:
    out: dict[str, list[str]] = {}
    for key in ENTITY_KEYS:
        seen: list[str] = []
        for v in [*((a or {}).get(key) or []), *((b or {}).get(key) or [])]:
            if v not in seen:
                seen.append(v)
        out[key] = seen
    return out


def merge_attack(a: Sequence[dict] | None, b: Sequence[dict] | None) -> list[dict]:
    out: dict[str, dict] = {}
    for ref in [*(a or []), *(b or [])]:
        out.setdefault(ref["techniqueId"], ref)
    return sorted(out.values(), key=lambda r: r["techniqueId"])


def tactics_of(attack: Sequence[dict]) -> list[str]:
    seen: list[str] = []
    for ref in attack:
        for t in ref.get("tactics") or []:
            if t not in seen:
                seen.append(t)
    return seen


async def next_reference(db: AsyncSession, org_id: uuid.UUID) -> tuple[int, str]:
    last = (await db.execute(select(func.max(Incident.number)).where(Incident.org_id == org_id))).scalar_one()
    number = max(int(last or 0) + 1, FIRST_REFERENCE)
    return number, f"NJ-{number}"


async def add_timeline(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    actor_type: str = "system",
    actor_name: str = "Nightjar SOC",
    body: str | None = None,
    ref_id: Any = None,
    executive: bool = False,
    at: datetime | None = None,
) -> IncidentEvent:
    row = IncidentEvent(
        org_id=org_id,
        incident_id=incident_id,
        at=at or clock.now(),
        kind=kind,
        actor_type=actor_type,
        actor_name=actor_name,
        title=title[:400],
        body=body,
        ref_id=str(ref_id) if ref_id is not None else None,
        executive=executive,
    )
    db.add(row)
    await db.flush()
    return row


async def announce_updated(db: AsyncSession, inc: Incident, changed: list[str]) -> None:
    await emit(
        db,
        inc.org_id,
        "incident.updated",
        {
            "incidentId": str(inc.id),
            "reference": inc.reference,
            "status": inc.status,
            "stage": inc.stage,
            "severity": inc.severity,
            "changed": changed,
        },
        permission="incidents:read",
    )


async def get_incident(db: AsyncSession, org_id: uuid.UUID, incident_id: Any) -> Incident:
    """By UUID or by reference (`NJ-1042`), in this org only; 404 otherwise (never 403 across tenants)."""
    inc: Incident | None = None
    key = str(incident_id)
    if key.upper().startswith("NJ-"):
        inc = (
            await db.execute(select(Incident).where(Incident.org_id == org_id, Incident.reference == key.upper()))
        ).scalar_one_or_none()
    else:
        try:
            uid = uuid.UUID(key)
        except ValueError:
            uid = None
        if uid is not None:
            inc = (
                await db.execute(select(Incident).where(Incident.org_id == org_id, Incident.id == uid))
            ).scalar_one_or_none()
    if inc is None:
        raise AppError(404, "not_found", "Incident not found", "Incident not found.")
    return inc


def crown_tags(org: Organization | None) -> list[str]:
    settings = (org.settings or {}) if org else {}
    return list(settings.get("crownJewelAssetTags") or ["crown-jewel"])


async def involves_crown_jewel(db: AsyncSession, org_id: uuid.UUID, entities: dict) -> bool:
    org = await db.get(Organization, org_id)
    assets = await assets_by_names_or_ips(db, org_id, list(entities.get("hosts") or []))
    return any(is_crown_jewel(a, crown_tags(org)) for a in assets)


# ── Status transitions (§1.8) ────────────────────────────────────────────────
def check_transition(current: str, target: str, *, resolution_reason: str | None) -> None:
    if current == target:
        return
    if target == "resolved":
        if current not in OPEN_STATUSES:
            raise AppError(
                422, "invalid_transition", "Invalid status change", f"A {current} incident cannot be resolved."
            )
        if resolution_reason not in RESOLUTION_REASONS:
            raise AppError(
                422,
                "invalid_transition",
                "Invalid status change",
                "Resolving needs a resolution reason: contained, benign, false_positive, duplicate or merged.",
                errors=[{"field": "resolution.reason", "message": "Choose a resolution reason."}],
            )
        return
    if target == "closed":
        if current != "resolved":
            raise AppError(
                422, "invalid_transition", "Invalid status change", "Only a resolved incident can be closed."
            )
        return
    if current in ("resolved", "closed"):
        raise AppError(422, "invalid_transition", "Invalid status change", "Reopen the incident first.")
    if target not in STATUS_ORDER or STATUS_ORDER.index(target) < STATUS_ORDER.index(current):
        raise AppError(
            422, "invalid_transition", "Invalid status change", f"An incident cannot move from {current} to {target}."
        )


def apply_status(inc: Incident, status: str) -> None:
    inc.status = status
    if status in STAGE_FOR_STATUS:
        # stages never move backwards except on reopen
        new_stage = STAGE_FOR_STATUS[status]
        order = ("triage", "investigate", "contain", "report")
        if order.index(new_stage) > order.index(inc.stage):
            inc.stage = new_stage
    if status == "resolved":
        inc.resolved_at = clock.now()


# ── Serialisers ──────────────────────────────────────────────────────────────
def triage_doc(score: TriageScoreRow | None) -> dict | None:
    if score is None:
        return None
    return {
        "probability": score.probability,
        "band": score.band,
        "threshold": score.threshold,
        "reasons": score.reasons or [],
        "modelVersion": score.model_version,
        "suppressed": score.suppressed,
    }


def detection_doc(
    d: DetectionRow, score: TriageScoreRow | None, *, incident_ref: str | None = None, evidence: bool = False
) -> dict:
    doc = {
        "id": str(d.id),
        "source": d.source,
        "ruleId": d.rule_id,
        "ruleTitle": d.rule_title,
        "severity": d.severity,
        "confidence": d.confidence,
        "attack": d.attack or [],
        "entities": merge_entities(d.entities, None),
        "firstSeen": d.first_seen.isoformat(),
        "lastSeen": d.last_seen.isoformat(),
        "eventCount": len(d.event_ids or []),
        "summary": d.summary,
        "fields": d.fields or {},
        "triage": triage_doc(score),
        "incidentId": str(d.incident_id) if d.incident_id else None,
        "incidentReference": incident_ref,
    }
    if evidence:
        doc["evidence"] = d.evidence or []
    return doc


def timeline_doc(e: IncidentEvent) -> dict:
    return {
        "id": str(e.id),
        "at": e.at.isoformat(),
        "kind": e.kind,
        "actor": {"type": e.actor_type, "name": e.actor_name},
        "title": e.title,
        "body": e.body,
        "refId": e.ref_id,
        "executive": e.executive,
    }


def _user_doc(u: User | None) -> dict | None:
    if u is None:
        return None
    from nightjar.services.auth import user_out

    return user_out(u)


async def summaries(db: AsyncSession, org_id: uuid.UUID, incidents: Sequence[Incident]) -> list[dict]:
    """`IncidentSummary[]` with batched lookups (assignees, counts, clocks)."""
    from nightjar.regimes.service import incident_reporting_overview

    if not incidents:
        return []
    ids = [i.id for i in incidents]
    det_counts = dict(
        (
            await db.execute(
                select(DetectionRow.incident_id, func.count())
                .where(DetectionRow.org_id == org_id, DetectionRow.incident_id.in_(ids))
                .group_by(DetectionRow.incident_id)
            )
        ).all()
    )
    pending = dict(
        (
            await db.execute(
                select(ActionProposal.incident_id, func.count())
                .where(
                    ActionProposal.org_id == org_id,
                    ActionProposal.incident_id.in_(ids),
                    ActionProposal.status == "pending",
                )
                .group_by(ActionProposal.incident_id)
            )
        ).all()
    )
    assignee_ids = {i.assignee_id for i in incidents if i.assignee_id}
    users = (
        {u.id: u for u in (await db.execute(select(User).where(User.id.in_(assignee_ids)))).scalars()}
        if assignee_ids
        else {}
    )
    overview = await incident_reporting_overview(db, org_id, incidents)
    out = []
    for inc in incidents:
        ents = merge_entities(inc.entities, None)
        reportable, next_deadline = overview.get(inc.id, (None, None))
        out.append(
            {
                "id": str(inc.id),
                "reference": inc.reference,
                "title": inc.title,
                "kind": inc.kind,
                "severity": inc.severity,
                "status": inc.status,
                "statusLabel": STATUS_LABELS.get(inc.status, inc.status),
                "stage": inc.stage,
                "assets": ents["hosts"] + ents["cloudAccounts"],
                "identities": ents["users"],
                "attackTactics": tactics_of(inc.attack or []),
                "detectionCount": int(det_counts.get(inc.id, 0)),
                "firstSeen": inc.first_seen.isoformat(),
                "lastSeen": inc.last_seen.isoformat(),
                "assignee": _user_doc(users.get(inc.assignee_id)) if inc.assignee_id else None,
                "verdict": inc.verdict,
                "likelyBenign": inc.likely_benign,
                "reportable": reportable,
                "nextDeadline": next_deadline,
                "pendingApprovals": int(pending.get(inc.id, 0)),
            }
        )
    return out


async def incident_full(db: AsyncSession, inc: Incident) -> dict:
    from nightjar.regimes.service import assessments_for_incident
    from nightjar.ticketing.api import ticket_doc_list

    (summary,) = await summaries(db, inc.org_id, [inc])
    ents = merge_entities(inc.entities, None)
    assets = await assets_by_names_or_ips(db, inc.org_id, ents["hosts"] + ents["cloudAccounts"] + ents["ips"])
    idents: list[Identity] = await identities_by_keys(db, inc.org_id, ents["users"] + ents["emails"])
    merged_ref = None
    if inc.merged_into_id:
        target = await db.get(Incident, inc.merged_into_id)
        merged_ref = target.reference if target else None
    triage = None
    if inc.verdict:
        triage = {
            "verdict": inc.verdict,
            "confidence": inc.verdict_confidence,
            "summary": inc.verdict_summary,
            "modelProbability": inc.model_probability,
            "decidedAt": inc.triaged_at.isoformat() if inc.triaged_at else None,
            "runId": str(inc.triage_run_id) if inc.triage_run_id else None,
        }
    return {
        **summary,
        "version": inc.version,
        "summary": inc.summary,
        "attack": [{**a, "tacticLabels": [tactic_label(t) for t in a.get("tactics") or []]} for a in inc.attack or []],
        "entities": ents,
        "assetsDetail": [asset_doc(a, kev_docs(list(a.vulnerabilities or []))) for a in assets],
        "identitiesDetail": [identity_doc(i) for i in idents],
        "triage": triage,
        "timeToTriageSeconds": _seconds(inc.first_seen, inc.triaged_at),
        "timeToContainSeconds": _seconds(inc.first_seen, inc.contained_at),
        "resolution": inc.resolution,
        "mergedInto": merged_ref,
        "regimes": await assessments_for_incident(db, inc),
        "tickets": await ticket_doc_list(db, inc.org_id, inc.id),
    }


def _seconds(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds()))


def jurisdictions_of(assets: Iterable[Any], identities: Iterable[Any]) -> list[str]:
    c: Counter[str] = Counter()
    for a in assets:
        c[a.jurisdiction] += 1
    for i in identities:
        c[i.jurisdiction] += 1
    return [j for j, _n in c.most_common()]
