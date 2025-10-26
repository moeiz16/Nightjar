"""The incident clock (API contract §1.12): applicability proposals, human determinations, deadlines and notices.

The clock never decides on its own. Proposals come from the triage facts and the org's regulatory profile; a
person with `reporting:assess` determines; the determination starts the clocks; notices are drafted, approved and
submitted (mock receipts) by people. Submissions start follow-on clocks (NIS2 final report, SOCI written record).
"""

from __future__ import annotations

import html
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.errors import AppError, conflict, validation_error
from nightjar.core.events import emit
from nightjar.db.models.platform import Organization, User
from nightjar.db.models.soc import (
    ActionProposal,
    DeadlineRow,
    Incident,
    Notice,
    NoticeVersion,
    RegimeAssessmentRow,
)
from nightjar.regimes import drafting, rules
from nightjar.services.audit import audit

KIND_LABELS = {
    "phishing": "Phishing",
    "credential_misuse": "Misuse of a person's sign-in",
    "ransomware_precursor": "Early stage of a ransomware attack",
    "cloud_credential_exposure": "Exposed cloud access key",
    "brute_force": "Password guessing",
    "data_exfiltration": "Unusual data transfer",
    "malware": "Malicious software",
    "insider": "Insider activity",
    "benign_activity": "Expected activity",
    "other": "Security incident",
}
CHANNELS = ("portal", "email", "oral")
OPEN = ("new", "triage", "investigating", "containing", "monitoring")


# ── Profile helpers ──────────────────────────────────────────────────────────
def org_entities(org: Organization) -> list[dict]:
    return list((org.profile or {}).get("entities") or [])


def entity_by_id(org: Organization, entity_id: str) -> dict | None:
    return next((e for e in org_entities(org) if e.get("id") == entity_id), None)


def regime_contact(org: Organization, regime: str) -> dict | None:
    rc = ((org.settings or {}).get("regimeContacts") or {}).get(regime) or {}
    contacts = rc.get("contacts") or []
    return contacts[0] if contacts else None


async def _jurisdictions(db: AsyncSession, inc: Incident) -> list[str]:
    from nightjar.directory.api import assets_by_names_or_ips, identities_by_keys
    from nightjar.incidents.service import jurisdictions_of, merge_entities

    ents = merge_entities(inc.entities, None)
    assets = await assets_by_names_or_ips(db, inc.org_id, ents["hosts"] + ents["cloudAccounts"])
    idents = await identities_by_keys(db, inc.org_id, ents["users"])
    return jurisdictions_of(assets, idents)


# ── Proposals ────────────────────────────────────────────────────────────────
async def propose_for_incident(db: AsyncSession, inc: Incident) -> list[RegimeAssessmentRow]:
    """(Re)compute applicability proposals for every regime of every legal entity. Determinations are kept."""
    org = await db.get(Organization, inc.org_id)
    assert org is not None
    juris = await _jurisdictions(db, inc)
    existing = {
        (a.regime, a.entity_id): a
        for a in (
            await db.execute(select(RegimeAssessmentRow).where(RegimeAssessmentRow.incident_id == inc.id))
        ).scalars()
    }
    out = []
    for ent in org_entities(org):
        for regime in rules.entity_regimes(ent):
            if inc.verdict in ("benign", "false_positive"):
                applies, reasons = "no", ["The triage colleague concluded this was not an attack."]
            else:
                applies, reasons = rules.propose(
                    regime, ent, inc.facts, incident={"jurisdictions": juris, "kind": inc.kind}
                )
            proposal = {"applies": applies, "reasons": reasons, "proposedAt": clock.now().isoformat()}
            row = existing.get((regime, ent["id"]))
            if row is None:
                row = RegimeAssessmentRow(
                    org_id=inc.org_id, incident_id=inc.id, regime=regime, entity_id=ent["id"], proposal=proposal
                )
                db.add(row)
            else:
                row.proposal = proposal
            out.append(row)
    await db.flush()
    return out


def _user_doc(u: User | None) -> dict | None:
    if u is None:
        return None
    from nightjar.services.auth import user_out

    return user_out(u)


async def assessments_for_incident(db: AsyncSession, inc: Incident) -> list[dict]:
    org = await db.get(Organization, inc.org_id)
    assert org is not None
    rows = list(
        (
            await db.execute(
                select(RegimeAssessmentRow)
                .where(RegimeAssessmentRow.org_id == inc.org_id, RegimeAssessmentRow.incident_id == inc.id)
                .order_by(RegimeAssessmentRow.regime)
            )
        ).scalars()
    )
    order = {r: i for i, r in enumerate(rules.REGIME_IDS)}
    rows.sort(key=lambda r: order.get(r.regime, 9))
    uids = {uuid.UUID(r.determination["byId"]) for r in rows if r.determination and r.determination.get("byId")}
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    out = []
    for r in rows:
        ent = entity_by_id(org, r.entity_id) or {}
        det = None
        if r.determination:
            det = {k: v for k, v in r.determination.items() if k != "byId"}
            by = users.get(uuid.UUID(r.determination["byId"])) if r.determination.get("byId") else None
            det["by"] = _user_doc(by)
        out.append(
            {
                "regime": r.regime,
                "regimeLabel": rules.regime_label(r.regime),
                "jurisdiction": ent.get("jurisdiction", rules.REGIMES[r.regime]["jurisdiction"]),
                "entityId": r.entity_id,
                "entityName": ent.get("name", r.entity_id),
                "proposal": r.proposal,
                "determination": det,
            }
        )
    return out


# ── Deadlines ────────────────────────────────────────────────────────────────
def _display_type(d: DeadlineRow, inc_open: bool) -> str:
    if d.notice_type == "nis2.final_report" and inc_open and d.submitted_at is None:
        return "nis2.progress_report"
    return d.notice_type


def deadline_doc(d: DeadlineRow, inc: Incident, ent: dict | None, now: datetime | None = None) -> dict:
    now = now or clock.now()
    state = rules.clock_state(
        regime=d.regime,
        starts_at=d.starts_at,
        due_at=d.due_at,
        now=now,
        submitted=d.submitted_at is not None,
        required=d.active,
    )
    ntype = _display_type(d, inc.status in OPEN)
    tz = ZoneInfo(d.timezone)
    live = state in ("on_track", "due_soon", "overdue")
    return {
        "id": str(d.id),
        "incidentId": str(inc.id),
        "incidentReference": inc.reference,
        "regime": d.regime,
        "regimeLabel": rules.regime_label(d.regime),
        "noticeType": ntype,
        "noticeLabel": rules.notice_label(ntype),
        "jurisdiction": (ent or {}).get("jurisdiction", rules.REGIMES[d.regime]["jurisdiction"]),
        "entityId": d.entity_id,
        "startsAt": d.starts_at.isoformat(),
        "dueAt": d.due_at.isoformat(),
        "dueAtLocal": {"at": d.due_at.astimezone(tz).isoformat(), "timezone": d.timezone},
        "businessDays": rules.business_days_left(now, d.due_at) if d.regime == "sec_8k" and live else None,
        "state": state,
        "remainingSeconds": int((d.due_at - now).total_seconds()) if live else None,
        "noticeId": str(d.notice_id) if d.notice_id else None,
        "submittedAt": d.submitted_at.isoformat() if d.submitted_at else None,
        "basis": d.basis,
    }


def _pending_doc(a: RegimeAssessmentRow, inc: Incident, ent: dict | None) -> dict:
    """`not_started`: an assessment is open (proposal yes/assess, no determination yet)."""
    return {
        "id": f"assessment-{a.id}",
        "incidentId": str(inc.id),
        "incidentReference": inc.reference,
        "regime": a.regime,
        "regimeLabel": rules.regime_label(a.regime),
        "noticeType": None,
        "noticeLabel": "Assessment open",
        "jurisdiction": (ent or {}).get("jurisdiction", rules.REGIMES[a.regime]["jurisdiction"]),
        "entityId": a.entity_id,
        "startsAt": None,
        "dueAt": None,
        "dueAtLocal": None,
        "businessDays": None,
        "state": "not_started",
        "remainingSeconds": None,
        "noticeId": None,
        "submittedAt": None,
        "basis": "; ".join((a.proposal or {}).get("reasons") or []),
    }


async def _create_deadline(
    db: AsyncSession, inc: Incident, regime: str, entity_id: str, spec: rules.DeadlineSpec, *, announce: bool = True
) -> DeadlineRow:
    d = DeadlineRow(
        org_id=inc.org_id,
        incident_id=inc.id,
        regime=regime,
        entity_id=entity_id,
        notice_type=spec.notice_type,
        starts_at=spec.starts_at,
        due_at=spec.due_at,
        timezone=spec.timezone,
        state="on_track",
        basis=spec.basis,
        active=True,
    )
    db.add(d)
    await db.flush()
    d.state = rules.clock_state(regime=regime, starts_at=d.starts_at, due_at=d.due_at, now=clock.now(), submitted=False)
    if announce:
        await emit(
            db,
            inc.org_id,
            "clock.started",
            {
                "incidentId": str(inc.id),
                "regime": regime,
                "noticeType": spec.notice_type,
                "dueAt": spec.due_at.isoformat(),
            },
            permission="reporting:read",
        )
    return d


async def _deadlines(db: AsyncSession, org_id: uuid.UUID, incident_ids: list[uuid.UUID]) -> list[DeadlineRow]:
    if not incident_ids:
        return []
    return list(
        (
            await db.execute(
                select(DeadlineRow)
                .where(DeadlineRow.org_id == org_id, DeadlineRow.incident_id.in_(incident_ids))
                .order_by(DeadlineRow.due_at)
            )
        ).scalars()
    )


async def clocks(db: AsyncSession, org_id: uuid.UUID) -> list[dict]:
    """Running clocks (not submitted) and open assessments, soonest first."""
    org = await db.get(Organization, org_id)
    assert org is not None
    rows = list(
        (
            await db.execute(
                select(DeadlineRow).where(
                    DeadlineRow.org_id == org_id, DeadlineRow.active.is_(True), DeadlineRow.submitted_at.is_(None)
                )
            )
        ).scalars()
    )
    assessments = list(
        (
            await db.execute(
                select(RegimeAssessmentRow).where(
                    RegimeAssessmentRow.org_id == org_id, RegimeAssessmentRow.determination.is_(None)
                )
            )
        ).scalars()
    )
    assessments = [a for a in assessments if (a.proposal or {}).get("applies") in ("yes", "assess")]
    inc_ids = {r.incident_id for r in rows} | {a.incident_id for a in assessments}
    incs = (
        {i.id: i for i in (await db.execute(select(Incident).where(Incident.id.in_(inc_ids)))).scalars()}
        if inc_ids
        else {}
    )
    now = clock.now()
    docs = [deadline_doc(r, incs[r.incident_id], entity_by_id(org, r.entity_id), now) for r in rows]
    docs.sort(key=lambda d: d["dueAt"])
    pending = [
        _pending_doc(a, incs[a.incident_id], entity_by_id(org, a.entity_id))
        for a in assessments
        if incs[a.incident_id].status in OPEN
    ]
    pending.sort(key=lambda d: (d["incidentReference"], d["regime"]))
    return docs + pending


async def incident_reporting_overview(
    db: AsyncSession, org_id: uuid.UUID, incidents: list[Incident] | Any
) -> dict[uuid.UUID, tuple[str | None, dict | None]]:
    """incident id → (reportable: yes|no|assess|None, next running deadline doc)."""
    ids = [i.id for i in incidents]
    if not ids:
        return {}
    org = await db.get(Organization, org_id)
    assess_rows = list(
        (await db.execute(select(RegimeAssessmentRow).where(RegimeAssessmentRow.incident_id.in_(ids)))).scalars()
    )
    by_inc: dict[uuid.UUID, list[RegimeAssessmentRow]] = {}
    for a in assess_rows:
        by_inc.setdefault(a.incident_id, []).append(a)
    deadlines = await _deadlines(db, org_id, ids)
    now = clock.now()
    out: dict[uuid.UUID, tuple[str | None, dict | None]] = {}
    incs = {i.id: i for i in incidents}
    for inc_id in ids:
        rows = by_inc.get(inc_id, [])
        reportable: str | None = None
        if rows:
            if any(r.determination and r.determination.get("applies") for r in rows):
                reportable = "yes"
            elif any(not r.determination and (r.proposal or {}).get("applies") in ("yes", "assess") for r in rows):
                reportable = "assess"
            else:
                reportable = "no"
        nxt = next(
            (d for d in deadlines if d.incident_id == inc_id and d.active and d.submitted_at is None),
            None,
        )
        out[inc_id] = (
            reportable,
            deadline_doc(nxt, incs[inc_id], entity_by_id(org, nxt.entity_id) if org else None, now) if nxt else None,
        )
    return out


# ── Determination ────────────────────────────────────────────────────────────
def _req_dt(body: dict, key: str, label: str) -> datetime:
    value = body.get(key)
    if value is None:
        raise validation_error(f"{label} is required.", [{"field": key, "message": f"{label} is required."}])
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        raise validation_error("Include a time zone.", [{"field": key, "message": "Include a time zone offset."}])
    if value > clock.now() + timedelta(minutes=5):
        raise validation_error(
            f"{label} cannot be in the future.", [{"field": key, "message": "This time is in the future."}]
        )
    return value


async def assess(db: AsyncSession, inc: Incident, regime: str, body: dict, *, user: User, ip: str | None) -> dict:
    if regime not in rules.REGIME_IDS:
        raise AppError(404, "not_found", "Regime not found", "Unknown reporting regime.")
    org = await db.get(Organization, inc.org_id)
    assert org is not None
    ent = entity_by_id(org, body["entityId"])
    if ent is None or regime not in rules.entity_regimes(ent):
        raise validation_error(
            "This legal entity is not in scope of that regime.",
            [{"field": "entityId", "message": "Choose a legal entity that this regime applies to."}],
        )
    applies = bool(body["applies"])
    det: dict[str, Any] = {"applies": applies, "rationale": body["rationale"]}
    if applies:
        if regime == "sec_8k":
            det["material"] = True
            det["materialityDeterminedAt"] = _req_dt(body, "materialityDeterminedAt", "The materiality decision time")
        else:
            det["awareAt"] = _req_dt(body, "awareAt", "The time the entity became aware")
            if regime == "nis2":
                det["significant"] = True
            if regime == "soci":
                level = body.get("impactLevel")
                if level not in ("significant", "relevant"):
                    raise validation_error(
                        "Choose the impact level.",
                        [{"field": "impactLevel", "message": "Choose significant or relevant impact."}],
                    )
                det["impactLevel"] = level
    else:
        if regime == "sec_8k":
            det["material"] = False
        if regime == "nis2":
            det["significant"] = False
    row = (
        await db.execute(
            select(RegimeAssessmentRow).where(
                RegimeAssessmentRow.incident_id == inc.id,
                RegimeAssessmentRow.regime == regime,
                RegimeAssessmentRow.entity_id == ent["id"],
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = RegimeAssessmentRow(
            org_id=inc.org_id,
            incident_id=inc.id,
            regime=regime,
            entity_id=ent["id"],
            proposal={
                "applies": "assess",
                "reasons": ["Assessment opened by a person."],
                "proposedAt": clock.now().isoformat(),
            },
        )
        db.add(row)
    existing = [
        d
        for d in await _deadlines(db, inc.org_id, [inc.id])
        if d.regime == regime and d.entity_id == ent["id"] and d.active
    ]
    if not applies and any(d.submitted_at for d in existing):
        raise conflict("already_submitted", "A notice under this regime has already been submitted.")
    for d in existing:  # a new determination replaces running clocks that have not been met
        if d.submitted_at is None:
            d.active = False
    now = clock.now()
    stored = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in det.items()}
    row.determination = {**stored, "byId": str(user.id), "at": now.isoformat()}
    await db.flush()
    label = rules.regime_label(regime)
    await audit(
        db,
        None,
        org_id=inc.org_id,
        action="reporting.assessed",
        resource_type="incident",
        resource_id=inc.id,
        actor_id=user.id,
        actor_name=user.full_name,
        ip=ip,
        metadata={
            "regime": regime,
            "regimeLabel": label,
            "reference": inc.reference,
            "applies": applies,
            "entityId": ent["id"],
        },
    )
    from nightjar.incidents.service import add_timeline, announce_updated

    started = []
    for spec in rules.deadlines_for_determination(regime, det, ent):
        started.append(await _create_deadline(db, inc, regime, ent["id"], spec))
    if started:
        await audit(
            db,
            None,
            org_id=inc.org_id,
            action="reporting.clock_started",
            resource_type="incident",
            resource_id=inc.id,
            actor_id=user.id,
            actor_name=user.full_name,
            ip=ip,
            metadata={
                "regime": regime,
                "regimeLabel": label,
                "reference": inc.reference,
                "noticeTypes": [d.notice_type for d in started],
            },
        )
        inc.stage = "report"
        first = min(started, key=lambda d: d.due_at)
        tz = ZoneInfo(first.timezone)
        await add_timeline(
            db,
            inc.org_id,
            inc.id,
            kind="clock",
            actor_type="user",
            actor_name=user.full_name,
            title=f"{label}: {user.full_name} determined the incident is reportable for {ent['name']}; "
            f"first notice due {first.due_at.astimezone(tz):%a %d %b %H:%M} {first.due_at.astimezone(tz).tzname()}",
            body=body["rationale"],
            ref_id=row.id,
            executive=True,
        )
    else:
        await add_timeline(
            db,
            inc.org_id,
            inc.id,
            kind="clock",
            actor_type="user",
            actor_name=user.full_name,
            title=f"{label}: {user.full_name} determined no report is required for {ent['name']}",
            body=body["rationale"],
            ref_id=row.id,
            executive=True,
        )
    inc.version += 1
    await announce_updated(db, inc, ["reporting"])
    docs = await assessments_for_incident(db, inc)
    return next(d for d in docs if d["regime"] == regime and d["entityId"] == ent["id"])


async def sec_delay(
    db: AsyncSession, inc: Incident, *, granted_until: datetime, reference: str, user: User, ip: str | None
) -> list[dict]:
    rows = [
        d
        for d in await _deadlines(db, inc.org_id, [inc.id])
        if d.regime == "sec_8k" and d.notice_type == "sec_8k.item_105" and d.active and d.submitted_at is None
    ]
    if not rows:
        raise AppError(409, "clock_not_started", "Conflict", "There is no running Form 8-K clock for this incident.")
    if granted_until.tzinfo is None:
        raise validation_error("Include a time zone.", [{"field": "grantedUntil", "message": "Include a time zone."}])
    out = []
    org = await db.get(Organization, inc.org_id)
    for d in rows:
        if granted_until > d.due_at:
            d.due_at = granted_until
        d.basis += (
            f" The Attorney General granted a delay (reference {reference}) until "
            f"{granted_until.astimezone(ZoneInfo(d.timezone)):%d %b %Y %H:%M %Z}."
        )
        d.state = rules.clock_state(
            regime=d.regime, starts_at=d.starts_at, due_at=d.due_at, now=clock.now(), submitted=False
        )
        out.append(deadline_doc(d, inc, entity_by_id(org, d.entity_id) if org else None))
    await audit(
        db,
        None,
        org_id=inc.org_id,
        action="reporting.delay_recorded",
        resource_type="incident",
        resource_id=inc.id,
        actor_id=user.id,
        actor_name=user.full_name,
        ip=ip,
        metadata={"reference": inc.reference, "delayReference": reference, "grantedUntil": granted_until.isoformat()},
    )
    from nightjar.incidents.service import add_timeline

    await add_timeline(
        db,
        inc.org_id,
        inc.id,
        kind="clock",
        actor_type="user",
        actor_name=user.full_name,
        title="Form 8-K: Attorney General delay recorded",
        body=f"Reference {reference}.",
        executive=True,
    )
    return out


# ── Notices ──────────────────────────────────────────────────────────────────
def notice_summary(n: Notice, due_at: datetime | None) -> dict:
    return {
        "id": str(n.id),
        "regime": n.regime,
        "type": n.type,
        "typeLabel": rules.notice_label(n.type),
        "status": n.status,
        "version": n.version,
        "dueAt": due_at.isoformat() if due_at else None,
        "submittedAt": (n.submission or {}).get("submittedAt"),
    }


async def notice_doc(db: AsyncSession, n: Notice) -> dict:
    org = await db.get(Organization, n.org_id)
    assert org is not None
    ent = entity_by_id(org, n.entity_id) or {"id": n.entity_id, "name": n.entity_id}
    deadline = await db.get(DeadlineRow, n.deadline_id) if n.deadline_id else None
    tpl = drafting.template(n.type)
    titles = {s["key"]: s for s in tpl["sections"]}
    bodies = {s["key"]: s.get("body", "") for s in n.sections or []}
    sections = []
    for key, spec in titles.items():
        body = bodies.get(key, "")
        required = _required(n, spec, deadline)
        sections.append(
            {
                "key": key,
                "title": spec["title"],
                "hint": spec.get("hint") or None,
                "body": body,
                "required": required,
                "filled": drafting.is_filled(body),
            }
        )
    versions = list(
        (
            await db.execute(
                select(NoticeVersion).where(NoticeVersion.notice_id == n.id).order_by(NoticeVersion.version)
            )
        ).scalars()
    )
    approver = await db.get(User, n.approved_by) if n.approved_by else None
    sub = dict(n.submission) if n.submission else None
    if sub and sub.get("submittedById"):
        submitter = await db.get(User, uuid.UUID(sub.pop("submittedById")))
        sub["submittedBy"] = _user_doc(submitter)
    regime_info = rules.REGIMES[n.regime]
    authority = (
        (ent.get("nis2") or {}).get("authorityName")
        if n.regime == "nis2"
        else ((ent.get("gdpr") or {}).get("leadAuthorityName") if n.regime == "gdpr_art33" else None)
    )
    rc = ((org.settings or {}).get("regimeContacts") or {}).get(n.regime) or {}
    return {
        **notice_summary(n, deadline.due_at if deadline else None),
        "incidentId": str(n.incident_id),
        "entity": {"id": ent.get("id"), "name": ent.get("name")},
        "recipient": {
            "name": rc.get("authorityName") or authority or regime_info["recipient"],
            "channel": "EDGAR" if n.regime == "sec_8k" else "portal",
            "portalUrl": rc.get("portalUrl"),
            "contact": regime_contact(org, n.regime),
        },
        "sections": sections,
        "missing": [s["key"] for s in sections if s["required"] and not s["filled"]],
        "versions": [
            {"version": v.version, "editedBy": v.edited_by_name, "editedAt": v.edited_at.isoformat()} for v in versions
        ],
        "approvedBy": _user_doc(approver),
        "approvedAt": n.approved_at.isoformat() if n.approved_at else None,
        "submission": sub,
        "generatedBy": n.generated_by,
    }


def _required(n: Notice, spec: dict, deadline: DeadlineRow | None) -> bool:
    if spec["key"] == "delay_reasons" and n.regime == "gdpr_art33":
        return bool(deadline and clock.now() > deadline.due_at)  # reasons are required once late
    return bool(spec["required"])


async def get_notice(db: AsyncSession, org_id: uuid.UUID, notice_id: Any) -> Notice:
    try:
        uid = uuid.UUID(str(notice_id))
    except ValueError:
        uid = None
    n = await db.get(Notice, uid) if uid else None
    if n is None or n.org_id != org_id:
        raise AppError(404, "not_found", "Notice not found", "Notice not found.")
    return n


async def _notice_context(db: AsyncSession, inc: Incident, regime: str, entity: dict, notice_type: str) -> dict:
    from nightjar.directory.api import assets_by_names_or_ips
    from nightjar.incidents.service import STATUS_LABELS, merge_entities

    org = await db.get(Organization, inc.org_id)
    assert org is not None
    ents = merge_entities(inc.entities, None)
    assets = await assets_by_names_or_ips(db, inc.org_id, ents["hosts"] + ents["cloudAccounts"])
    det_row = (
        await db.execute(
            select(RegimeAssessmentRow).where(
                RegimeAssessmentRow.incident_id == inc.id,
                RegimeAssessmentRow.regime == regime,
                RegimeAssessmentRow.entity_id == entity["id"],
            )
        )
    ).scalar_one_or_none()
    executed = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.incident_id == inc.id, ActionProposal.status == "executed")
        )
    ).scalars()
    from nightjar.approvals.service import KIND_LABELS as ACTION_LABELS

    actions = [f"{ACTION_LABELS.get(p.kind, p.kind).lower()} ({p.target})" for p in executed]
    oral = None
    if notice_type == "soci.written_record":
        prior = (
            await db.execute(
                select(Notice).where(
                    Notice.incident_id == inc.id, Notice.regime == "soci", Notice.status == "submitted"
                )
            )
        ).scalars()
        for p in prior:
            sub = p.submission or {}
            if sub.get("channel") == "oral":
                oral = f"Oral report made {sub.get('submittedAt')} (receipt {sub.get('receipt')})."
    services = sorted({a.business_service for a in assets if a.business_service})
    return {
        "org": org.name,
        "entity": entity,
        "incident": {
            "reference": inc.reference,
            "title": inc.title,
            "kindLabel": KIND_LABELS.get(inc.kind, inc.kind),
            "severity": inc.severity,
            "statusLabel": STATUS_LABELS.get(inc.status, inc.status),
            "firstSeen": inc.first_seen,
            "lastSeen": inc.last_seen,
            "containedAt": inc.contained_at,
            "verdictSummary": inc.verdict_summary,
            "services": services,
            "techniques": [f"{a.get('name')} ({a.get('techniqueId')})" for a in inc.attack or []],
        },
        "facts": inc.facts or {},
        "determination": (det_row.determination if det_row else None) or {},
        "actions": actions,
        "contact": regime_contact(org, regime),
        "iocs": [*ents["ips"], *ents["hashes"]][:20] if regime != "sec_8k" else [],
        "oralReport": oral,
    }


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    return value


async def draft_notice(
    db: AsyncSession, inc: Incident, *, regime: str, notice_type: str, user: User, ip: str | None
) -> Notice:
    from nightjar.incidents.service import add_timeline
    from nightjar.services.pipeline import generate_or_baseline, neutralise

    full_type = notice_type if "." in notice_type else f"{regime}.{notice_type}"
    if not full_type.startswith(regime + ".") or full_type not in rules.NOTICE_LABELS:
        raise validation_error("Unknown notice type for this regime.", [{"field": "type", "message": "Unknown type."}])
    org = await db.get(Organization, inc.org_id)
    assert org is not None
    rows = (
        await db.execute(
            select(RegimeAssessmentRow).where(
                RegimeAssessmentRow.incident_id == inc.id, RegimeAssessmentRow.regime == regime
            )
        )
    ).scalars()
    det_row = next((r for r in rows if r.determination and r.determination.get("applies")), None)
    if det_row is None:
        raise AppError(
            409,
            "clock_not_started",
            "Conflict",
            "A person with reporting authority must first determine that this regime applies.",
        )
    entity = entity_by_id(org, det_row.entity_id) or {"id": det_row.entity_id, "name": det_row.entity_id}
    deadlines = [
        d
        for d in await _deadlines(db, inc.org_id, [inc.id])
        if d.regime == regime and d.entity_id == entity["id"] and d.active
    ]
    wanted = "nis2.final_report" if full_type == "nis2.progress_report" else full_type
    deadline = next((d for d in deadlines if d.notice_type == wanted and d.submitted_at is None), None)
    if full_type == "sec_8k.amendment" and deadline is None:
        deadline = await _create_deadline(db, inc, regime, entity["id"], rules.sec_amendment(clock.now()))
    existing = (
        await db.execute(
            select(Notice).where(Notice.incident_id == inc.id, Notice.type == full_type, Notice.status != "submitted")
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    ctx = await _notice_context(db, inc, regime, entity, full_type)
    base = drafting.baseline(full_type, ctx)
    output, generated_by = await generate_or_baseline(
        "notice_draft",
        instructions=drafting.INSTRUCTIONS,
        trusted={"noticeType": full_type, "facts": _json_ready({k: v for k, v in ctx.items() if k != "contact"})},
        untrusted={"verdictSummary": neutralise(inc.verdict_summary or "", field="verdictSummary")},
        schema=drafting.NoticeDraft,
        baseline=base,
    )
    keys = {s["key"] for s in drafting.template(full_type)["sections"]}
    produced = {s["key"]: s["body"] for s in output["sections"] if s["key"] in keys}
    sections = [{"key": s["key"], "body": produced.get(s["key"], s["body"])} for s in base["sections"]]
    n = Notice(
        org_id=inc.org_id,
        incident_id=inc.id,
        regime=regime,
        entity_id=entity["id"],
        type=full_type,
        status="draft",
        version=1,
        sections=sections,
        deadline_id=deadline.id if deadline else None,
        generated_by=generated_by,
    )
    db.add(n)
    await db.flush()
    if deadline:
        deadline.notice_id = n.id
    db.add(
        NoticeVersion(
            notice_id=n.id,
            org_id=n.org_id,
            version=1,
            sections=sections,
            edited_by=user.id,
            edited_by_name=user.full_name,
            edited_at=clock.now(),
        )
    )
    label = rules.notice_label(full_type)
    await audit(
        db,
        None,
        org_id=inc.org_id,
        action="notice.drafted",
        resource_type="notice",
        resource_id=n.id,
        actor_id=user.id,
        actor_name=user.full_name,
        ip=ip,
        metadata={"noticeLabel": label, "reference": inc.reference, "type": full_type},
    )
    await add_timeline(
        db,
        inc.org_id,
        inc.id,
        kind="notice",
        actor_type="user",
        actor_name=user.full_name,
        title=f"{label} drafted",
        ref_id=n.id,
    )
    await _announce_notice(db, n)
    return n


async def _announce_notice(db: AsyncSession, n: Notice) -> None:
    await emit(
        db,
        n.org_id,
        "notice.updated",
        {
            "noticeId": str(n.id),
            "incidentId": str(n.incident_id),
            "regime": n.regime,
            "type": n.type,
            "status": n.status,
        },
        permission="reporting:read",
    )


async def edit_notice(db: AsyncSession, n: Notice, *, sections: list[dict], version: int, user: User) -> Notice:
    if n.status == "submitted":
        raise conflict("already_submitted", "This notice has already been submitted.")
    if version != n.version:
        raise conflict("stale_version", "Someone else changed this notice. Reload to see their version.")
    keys = {s["key"] for s in drafting.template(n.type)["sections"]}
    unknown = [s["key"] for s in sections if s["key"] not in keys]
    if unknown:
        raise validation_error(
            "Unknown section.",
            [{"field": f"sections.{k}", "message": "Not a section of this notice."} for k in unknown],
        )
    current = {s["key"]: s["body"] for s in n.sections or []}
    for s in sections:
        current[s["key"]] = s["body"]
    n.sections = [
        {"key": k, "body": current.get(k, "")} for k in [t["key"] for t in drafting.template(n.type)["sections"]]
    ]
    n.version += 1
    if n.status == "approved":  # an edit after approval needs approving again
        n.status, n.approved_by, n.approved_at = "draft", None, None
    db.add(
        NoticeVersion(
            notice_id=n.id,
            org_id=n.org_id,
            version=n.version,
            sections=n.sections,
            edited_by=user.id,
            edited_by_name=user.full_name,
            edited_at=clock.now(),
        )
    )
    await db.flush()
    await _announce_notice(db, n)
    return n


async def approve_notice(db: AsyncSession, n: Notice, *, user: User, ip: str | None) -> Notice:
    from nightjar.incidents.service import add_timeline

    if n.status == "submitted":
        raise conflict("already_submitted", "This notice has already been submitted.")
    if n.status == "approved":
        raise conflict("already_decided", "This notice is already approved.")
    doc = await notice_doc(db, n)
    if doc["missing"]:
        titles = {s["key"]: s["title"] for s in doc["sections"]}
        raise validation_error(
            "Complete the required sections before approving.",
            [{"field": f"sections.{k}", "message": f"{titles[k]} is required."} for k in doc["missing"]],
        )
    n.status = "approved"
    n.approved_by = user.id
    n.approved_at = clock.now()
    inc = await db.get(Incident, n.incident_id)
    assert inc is not None
    label = rules.notice_label(n.type)
    await audit(
        db,
        None,
        org_id=n.org_id,
        action="notice.approved",
        resource_type="notice",
        resource_id=n.id,
        actor_id=user.id,
        actor_name=user.full_name,
        ip=ip,
        metadata={"noticeLabel": label, "reference": inc.reference, "version": n.version},
    )
    await add_timeline(
        db,
        n.org_id,
        inc.id,
        kind="notice",
        actor_type="user",
        actor_name=user.full_name,
        title=f"{label} approved",
        ref_id=n.id,
    )
    await _announce_notice(db, n)
    return n


async def submit_notice(
    db: AsyncSession, n: Notice, *, channel: str, note: str | None, user: User, ip: str | None
) -> Notice:
    from nightjar.incidents.service import add_timeline

    if n.status == "submitted":
        raise conflict("already_submitted", "This notice has already been submitted.")
    if n.status != "approved":
        raise conflict("conflict", "A notice must be approved before it is submitted.")
    if channel not in CHANNELS:
        raise validation_error("Unknown channel.", [{"field": "channel", "message": "Use portal, email or oral."}])
    if channel == "oral" and n.regime != "soci":
        raise validation_error(
            "Only SOCI reports may be made orally.", [{"field": "channel", "message": "Use portal or email."}]
        )
    now = clock.now()
    receipt = f"{rules.REGIMES[n.regime]['receipt']}-MOCK-{secrets.token_hex(4).upper()}"
    n.status = "submitted"
    n.submission = {
        "channel": channel,
        "receipt": receipt,
        "submittedById": str(user.id),
        "submittedAt": now.isoformat(),
        "note": note,
    }
    inc = await db.get(Incident, n.incident_id)
    org = await db.get(Organization, n.org_id)
    assert inc is not None
    assert org is not None
    deadline = await db.get(DeadlineRow, n.deadline_id) if n.deadline_id else None
    if deadline is not None:
        deadline.submitted_at = now
        deadline.state = "submitted"
    entity = entity_by_id(org, n.entity_id)
    for spec in rules.deadlines_after_submission(
        n.type,
        now,
        channel=channel,
        entity=entity,
        handling_ended_at=inc.resolved_at if inc.status not in OPEN else None,
    ):
        await _create_deadline(db, inc, n.regime, n.entity_id, spec)
    label = rules.notice_label(n.type)
    await audit(
        db,
        None,
        org_id=n.org_id,
        action="notice.submitted",
        resource_type="notice",
        resource_id=n.id,
        actor_id=user.id,
        actor_name=user.full_name,
        ip=ip,
        metadata={"noticeLabel": label, "reference": inc.reference, "channel": channel, "receipt": receipt},
    )
    await add_timeline(
        db,
        n.org_id,
        inc.id,
        kind="notice",
        actor_type="user",
        actor_name=user.full_name,
        title=f"{label} submitted ({'orally' if channel == 'oral' else 'by ' + channel}); receipt {receipt}",
        body=note,
        ref_id=n.id,
        executive=True,
    )
    await _announce_notice(db, n)
    return n


async def on_incident_resolved(db: AsyncSession, inc: Incident) -> None:
    """NIS2: once handling ends after a progress report, the final report is due one month later."""
    org = await db.get(Organization, inc.org_id)
    rows = await _deadlines(db, inc.org_id, [inc.id])
    for d in rows:
        if d.regime != "nis2" or d.notice_type != "nis2.final_report" or not d.active or d.submitted_at is None:
            continue
        submitted_progress = (
            await db.execute(select(Notice).where(Notice.deadline_id == d.id, Notice.type == "nis2.progress_report"))
        ).scalar_one_or_none()
        already = any(
            r.notice_type == "nis2.final_report" and r.starts_at >= d.submitted_at and r.id != d.id for r in rows
        )
        if submitted_progress and not already:
            await _create_deadline(
                db,
                inc,
                "nis2",
                d.entity_id,
                rules.nis2_final_after_handling(
                    inc.resolved_at or clock.now(), entity_by_id(org, d.entity_id) if org else None
                ),
            )


async def incident_reporting(db: AsyncSession, inc: Incident) -> dict:
    org = await db.get(Organization, inc.org_id)
    rows = await _deadlines(db, inc.org_id, [inc.id])
    now = clock.now()
    notices = list(
        (await db.execute(select(Notice).where(Notice.incident_id == inc.id).order_by(Notice.created_at))).scalars()
    )
    due = {d.id: d.due_at for d in rows}
    return {
        "assessments": await assessments_for_incident(db, inc),
        "deadlines": [
            deadline_doc(d, inc, entity_by_id(org, d.entity_id) if org else None, now) for d in rows if d.active
        ],
        "notices": [notice_summary(n, due.get(n.deadline_id) if n.deadline_id else None) for n in notices],
    }


async def sweep(db: AsyncSession) -> int:
    """Announce clock transitions (due_soon, overdue) once each. Returns the number of transitions."""
    rows = list(
        (
            await db.execute(
                select(DeadlineRow).where(DeadlineRow.active.is_(True), DeadlineRow.submitted_at.is_(None))
            )
        ).scalars()
    )
    now = clock.now()
    n = 0
    for d in rows:
        state = rules.clock_state(regime=d.regime, starts_at=d.starts_at, due_at=d.due_at, now=now, submitted=False)
        if state != d.state:
            d.state = state
            if state in ("due_soon", "overdue"):
                n += 1
                await emit(
                    db,
                    d.org_id,
                    "clock.state",
                    {"deadlineId": str(d.id), "incidentId": str(d.incident_id), "regime": d.regime, "state": state},
                    permission="reporting:read",
                )
    return n


# ── Printable document ───────────────────────────────────────────────────────
def notice_html(doc: dict, *, org_name: str, incident_reference: str) -> str:
    e = html.escape
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{e(doc['typeLabel'])} · {e(incident_reference)}</title></head>",
        "<body style=\"font-family:Georgia,'Times New Roman',serif;max-width:760px;margin:40px auto;"
        'color:#111;line-height:1.5">',
        f"<p style='font:12px/1.4 Helvetica,Arial,sans-serif;color:#555'>{e(org_name)} · "
        f"{e(doc['entity']['name'] or '')}"
        f" · internal reference {e(incident_reference)} · version {doc['version']} · {e(doc['status'])}</p>",
        f"<h1 style='font-size:24px;margin:0 0 4px'>{e(doc['typeLabel'])}</h1>",
        f"<p style='margin:0 0 24px'>To: {e(doc['recipient']['name'] or '')}</p>",
    ]
    for s in doc["sections"]:
        body = e(s["body"] or "").replace("\n", "<br>")
        parts.append(f"<h2 style='font-size:16px;margin:24px 0 6px'>{e(s['title'])}</h2><p style='margin:0'>{body}</p>")
    if doc.get("submission"):
        sub = doc["submission"]
        parts.append(
            f"<p style='margin-top:32px;font:12px Helvetica,Arial,sans-serif;color:#555'>Recorded as submitted "
            f"({e(sub.get('channel') or '')}), receipt {e(sub.get('receipt') or '')}. Submissions in Nightjar SOC are "
            "recorded for the organisation's own file; Nightjar SOC does not file with regulators.</p>"
        )
    parts.append("</body></html>")
    return "".join(parts)
