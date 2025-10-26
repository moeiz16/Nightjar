"""Public interface of the incident store (builder A). Called by B's pipeline workers and agents.

All functions open their own unit of work (`session_scope()`), commit, append audit rows where stated and
emit the SSE events listed in API contract §1.17. They are idempotent where noted so NATS redelivery is safe.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from nightjar import clock
from nightjar.contracts import (
    Anomaly,
    AttackRef,
    CorrelationUpdate,
    Detection,
    EventSnapshot,
    IncidentContext,
    IncidentRef,
    OpenIncidentIndexEntry,
    OrgProfile,
    Reason,
    TriageScore,
    UntrustedText,
    to_doc,
)
from nightjar.contracts import Entities as EntitiesC
from nightjar.core.events import emit
from nightjar.db.models.platform import Organization
from nightjar.db.models.soc import AnomalyRow, DetectionRow, Incident, InvestigationRun, TriageScoreRow
from nightjar.db.session import session_scope
from nightjar.directory.api import (
    assets_by_names_or_ips,
    identities_by_keys,
    to_asset_record,
    to_identity_record,
)
from nightjar.incidents.service import (
    OPEN_STATUSES,
    add_timeline,
    announce_updated,
    bump,
    involves_crown_jewel,
    max_severity,
    merge_attack,
    merge_entities,
    next_reference,
    tactics_of,
)
from nightjar.services.audit import audit

CORRELATOR = "Correlation"


def _score_values(s: TriageScore) -> dict:
    return {
        "probability": float(s.probability),
        "band": s.band,
        "threshold": float(s.threshold),
        "reasons": to_doc(list(s.reasons)),
        "model_version": s.model_version,
        "suppressed": bool(s.suppressed),
    }


async def record_detections(org_id: uuid.UUID, detections: Sequence[Detection]) -> None:
    """Persist detections with their evidence snapshots and triage scores. Idempotent on `Detection.id`.
    Emits `detection.created` (permission `detections:read`) once per new detection."""
    if not detections:
        return
    async with session_scope() as db:
        ids = [d.id for d in detections]
        existing = set((await db.execute(select(DetectionRow.id).where(DetectionRow.id.in_(ids)))).scalars())
        for d in detections:
            if d.org_id != org_id:
                raise ValueError("detection belongs to another organisation")
            if d.id in existing:
                if d.triage is not None:
                    await _upsert_score(db, org_id, d.triage)
                continue
            db.add(
                DetectionRow(
                    id=d.id,
                    org_id=org_id,
                    source=d.source,
                    rule_id=d.rule_id,
                    rule_title=d.rule_title[:300],
                    severity=d.severity,
                    confidence=float(d.confidence),
                    attack=to_doc(list(d.attack)),
                    entities=to_doc(d.entities),
                    first_seen=d.first_seen,
                    last_seen=d.last_seen,
                    event_ids=list(d.event_ids),
                    evidence=to_doc(list(d.evidence[:20])),
                    summary=d.summary,
                    fields=dict(d.fields),
                )
            )
            existing.add(d.id)
            if d.triage is not None:
                await _upsert_score(db, org_id, d.triage)
            await emit(
                db,
                org_id,
                "detection.created",
                {
                    "detectionId": str(d.id),
                    "severity": d.severity,
                    "title": d.rule_title,
                    "band": d.triage.band if d.triage else None,
                    "incidentId": None,
                },
                permission="detections:read",
            )


async def _upsert_score(db, org_id: uuid.UUID, s: TriageScore) -> None:
    row = await db.get(TriageScoreRow, s.detection_id)
    if row is None:
        db.add(TriageScoreRow(detection_id=s.detection_id, org_id=org_id, **_score_values(s)))
    elif row.org_id == org_id:
        for k, v in _score_values(s).items():
            setattr(row, k, v)
    await db.flush()


async def record_triage_scores(org_id: uuid.UUID, scores: Sequence[TriageScore]) -> None:
    """Attach (or replace) the triage classifier's score on existing detections. Idempotent."""
    async with session_scope() as db:
        for s in scores:
            det = await db.get(DetectionRow, s.detection_id)
            if det is None or det.org_id != org_id:
                continue
            await _upsert_score(db, org_id, s)


async def record_anomalies(org_id: uuid.UUID, anomalies: Sequence[Anomaly]) -> None:
    """Persist behaviour anomalies (the UEBA view). Idempotent on `Anomaly.id`. Emits `anomaly.created`."""
    if not anomalies:
        return
    async with session_scope() as db:
        existing = set(
            (await db.execute(select(AnomalyRow.id).where(AnomalyRow.id.in_([a.id for a in anomalies])))).scalars()
        )
        for a in anomalies:
            if a.org_id != org_id:
                raise ValueError("anomaly belongs to another organisation")
            if a.id in existing:
                continue
            db.add(
                AnomalyRow(
                    id=a.id,
                    org_id=org_id,
                    entity_type=a.entity_type,
                    entity_id=a.entity_id,
                    window_start=a.window_start,
                    window_end=a.window_end,
                    score=float(a.score),
                    isolation_score=float(a.isolation_score),
                    robust_z={k: float(v) for k, v in a.robust_z.items()},
                    features={k: float(v) for k, v in a.features.items()},
                    reasons=to_doc(list(a.reasons)),
                    model_version=a.model_version,
                    detection_id=a.detection_id,
                )
            )
            existing.add(a.id)
            await emit(
                db,
                org_id,
                "anomaly.created",
                {"anomalyId": str(a.id), "entityType": a.entity_type, "entityId": a.entity_id, "score": a.score},
                permission="ueba:read",
            )


async def upsert_incident(update_: CorrelationUpdate) -> IncidentRef:
    """Create or extend the incident for `update.correlation_key` (idempotent). Recomputes severity (max of
    detections, raised one level when a crown-jewel asset is involved), ATT&CK, entities and the timeline.
    New incident: status `new`, stage `triage`, audit `incident.opened`, SSE `incident.created`.
    Existing: SSE `incident.updated`. `merge_incident_ids` are closed as merged into this one."""
    for attempt in range(3):
        try:
            return await _upsert(update_)
        except IntegrityError:  # a concurrent writer took the same reference number; retry
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


async def _upsert(u: CorrelationUpdate) -> IncidentRef:
    org_id = u.org_id
    async with session_scope() as db:
        dets = (
            (
                await db.execute(
                    select(DetectionRow).where(DetectionRow.org_id == org_id, DetectionRow.id.in_(u.detection_ids))
                )
            )
            .scalars()
            .all()
        )
        entities = merge_entities(to_doc(u.entities), None)
        attack = to_doc(list(u.attack))
        for d in dets:
            entities = merge_entities(entities, d.entities)
            attack = merge_attack(attack, d.attack)
        if u.all_likely_noise:  # a storm of likely-noise alerts keeps the correlator's (low) severity
            severity = u.severity
        else:
            severity = max_severity([u.severity, *(d.severity for d in dets)])
            if await involves_crown_jewel(db, org_id, entities):
                severity = bump(severity)
        inc = (
            await db.execute(
                select(Incident).where(Incident.org_id == org_id, Incident.correlation_key == u.correlation_key)
            )
        ).scalar_one_or_none()
        created = inc is None
        changed: list[str] = []
        if inc is None:
            number, reference = await next_reference(db, org_id)
            inc = Incident(
                org_id=org_id,
                number=number,
                reference=reference,
                correlation_key=u.correlation_key,
                version=1,
                title=u.title[:300],
                kind=u.kind,
                severity=severity,
                status="new",
                stage="triage",
                summary=u.summary,
                attack=attack,
                entities=entities,
                first_seen=u.first_seen,
                last_seen=u.last_seen,
                likely_benign=u.all_likely_noise,
                max_triage_probability=u.max_triage_probability,
            )
            db.add(inc)
            await db.flush()
            await add_timeline(
                db,
                org_id,
                inc.id,
                kind="status",
                title=f"Incident {reference} opened: {inc.title}",
                actor_name=CORRELATOR,
                at=u.first_seen,
                executive=True,
                body="Grouped as likely noise: every alert scored as unlikely to be real."
                if u.all_likely_noise
                else None,
            )
            await audit(
                db,
                None,
                org_id=org_id,
                action="incident.opened",
                resource_type="incident",
                resource_id=inc.id,
                actor_type="system",
                actor_name=CORRELATOR,
                metadata={"reference": reference, "severity": severity, "kind": u.kind},
            )
            await emit(
                db,
                org_id,
                "incident.created",
                {
                    "incidentId": str(inc.id),
                    "reference": reference,
                    "title": inc.title,
                    "severity": severity,
                    "kind": inc.kind,
                },
                permission="incidents:read",
            )
        else:
            merged = merge_entities(inc.entities, entities)
            if merged != merge_entities(inc.entities, None):
                changed.append("entities")
            inc.entities = merged
            new_attack = merge_attack(inc.attack, attack)
            if new_attack != (inc.attack or []):
                changed.append("attack")
            inc.attack = new_attack
            sev = severity if inc.likely_benign and u.all_likely_noise else max_severity([inc.severity, severity])
            if sev != inc.severity:
                changed.append("severity")
                inc.severity = sev
            if u.last_seen > inc.last_seen:
                inc.last_seen = u.last_seen
                changed.append("lastSeen")
            if u.first_seen < inc.first_seen:
                inc.first_seen = u.first_seen
            if inc.likely_benign and not u.all_likely_noise:
                inc.likely_benign = False
                changed.append("likelyBenign")
            if u.max_triage_probability is not None:
                inc.max_triage_probability = max(inc.max_triage_probability or 0.0, u.max_triage_probability)
            if u.summary and u.summary != inc.summary:
                inc.summary = u.summary
        # attach detections + timeline rows for new ones
        new_dets = [d for d in dets if d.incident_id != inc.id]
        for d in sorted(new_dets, key=lambda x: x.first_seen):
            d.incident_id = inc.id
            await add_timeline(
                db,
                org_id,
                inc.id,
                kind="anomaly" if d.source == "ueba" else "detection",
                title=d.rule_title,
                body=d.summary or None,
                ref_id=d.id,
                at=d.first_seen,
            )
        if new_dets and not created:
            changed.append("detections")
        # merges
        for other_id in u.merge_incident_ids:
            if other_id == inc.id:
                continue
            other = await db.get(Incident, other_id)
            if other is None or other.org_id != org_id or other.status == "closed":
                continue
            await _merge_into(db, inc, other, actor_name=CORRELATOR)
            changed.append("merged")
        if not created and changed:
            inc.version += 1
            await announce_updated(db, inc, sorted(set(changed)))
        await db.flush()
        return IncidentRef(id=inc.id, reference=inc.reference, created=created, status=inc.status)  # type: ignore[arg-type]


async def _merge_into(db, target: Incident, other: Incident, *, actor_name: str, actor_type: str = "system") -> None:
    await db.execute(
        update(DetectionRow)
        .where(DetectionRow.org_id == target.org_id, DetectionRow.incident_id == other.id)
        .values(incident_id=target.id)
    )
    target.entities = merge_entities(target.entities, other.entities)
    target.attack = merge_attack(target.attack, other.attack)
    target.severity = max_severity([target.severity, other.severity])
    target.first_seen = min(target.first_seen, other.first_seen)
    target.last_seen = max(target.last_seen, other.last_seen)
    other.status = "closed"
    other.merged_into_id = target.id
    other.resolved_at = other.resolved_at or clock.now()
    other.resolution = {
        "reason": "merged",
        "note": f"Merged into {target.reference}.",
        "by": actor_name,
        "at": clock.now().isoformat(),
    }
    other.version += 1
    await add_timeline(
        db,
        other.org_id,
        other.id,
        kind="status",
        title=f"Merged into {target.reference}",
        actor_type=actor_type,
        actor_name=actor_name,
        ref_id=target.id,
    )
    await add_timeline(
        db,
        target.org_id,
        target.id,
        kind="status",
        title=f"{other.reference} merged into this incident",
        actor_type=actor_type,
        actor_name=actor_name,
        ref_id=other.id,
    )
    await announce_updated(db, other, ["status", "mergedInto"])


async def open_incident_index(org_id: uuid.UUID, *, since: datetime) -> list[OpenIncidentIndexEntry]:
    """Open incidents with activity since `since` (correlator warm start after a restart)."""
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(Incident)
                    .where(Incident.org_id == org_id, Incident.status.in_(OPEN_STATUSES), Incident.last_seen >= since)
                    .order_by(Incident.last_seen.desc())
                )
            )
            .scalars()
            .all()
        )
        return [
            OpenIncidentIndexEntry(
                incident_id=r.id,
                correlation_key=r.correlation_key,
                entities=_entities(r.entities),
                attack_tactics=tuple(tactics_of(r.attack or [])),
                last_seen=r.last_seen,
                status=r.status,  # type: ignore[arg-type]
            )
            for r in rows
        ]


def _entities(doc: dict | None) -> EntitiesC:
    d = merge_entities(doc, None)
    return EntitiesC(
        hosts=tuple(d["hosts"]),
        users=tuple(d["users"]),
        ips=tuple(d["ips"]),
        cloud_accounts=tuple(d["cloudAccounts"]),
        access_keys=tuple(d["accessKeys"]),
        hashes=tuple(d["hashes"]),
        emails=tuple(d["emails"]),
    )


def _attack(docs: list[dict] | None) -> tuple[AttackRef, ...]:
    return tuple(AttackRef(a["techniqueId"], a.get("name", ""), tuple(a.get("tactics") or ())) for a in docs or [])


def _reasons(docs: list[dict] | None) -> tuple[Reason, ...]:
    return tuple(
        Reason(
            feature=r.get("feature", ""),
            value=r.get("value"),
            contribution=float(r.get("contribution") or 0.0),
            text=r.get("text", ""),
            baseline=r.get("baseline"),
        )
        for r in docs or []
    )


def _parse_dt(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _snapshot(doc: dict) -> EventSnapshot:
    summary = doc.get("summary") or {}
    return EventSnapshot(
        event_id=doc.get("eventId", ""),
        timestamp=_parse_dt(doc["timestamp"]),
        dataset=doc.get("dataset", ""),
        action=doc.get("action", ""),
        summary=UntrustedText(
            text=summary.get("text", ""),
            field=summary.get("field", "message"),
            flags=tuple(summary.get("flags") or ()),
            truncated=bool(summary.get("truncated")),
        ),
        host=doc.get("host"),
        user=doc.get("user"),
        fields=doc.get("fields") or {},
    )


def _detection(d: DetectionRow, s: TriageScoreRow | None) -> Detection:
    return Detection(
        id=d.id,
        org_id=d.org_id,
        source=d.source,  # type: ignore[arg-type]
        rule_id=d.rule_id,
        rule_title=d.rule_title,
        severity=d.severity,  # type: ignore[arg-type]
        confidence=d.confidence,
        attack=_attack(d.attack),
        entities=_entities(d.entities),
        first_seen=d.first_seen,
        last_seen=d.last_seen,
        event_ids=tuple(d.event_ids or ()),
        evidence=tuple(_snapshot(e) for e in d.evidence or []),
        summary=d.summary,
        fields=d.fields or {},
        triage=TriageScore(
            detection_id=s.detection_id,
            probability=s.probability,
            band=s.band,  # type: ignore[arg-type]
            threshold=s.threshold,
            reasons=_reasons(s.reasons),
            model_version=s.model_version,
            suppressed=s.suppressed,
        )
        if s
        else None,
    )


async def get_incident_context(org_id: uuid.UUID, incident_id: uuid.UUID) -> IncidentContext:
    """Everything an agent may read for an investigation (from SQLite only). Raises KeyError if the incident
    is not in this org (agents never see other tenants)."""
    async with session_scope() as db:
        inc = await db.get(Incident, incident_id)
        if inc is None or inc.org_id != org_id:
            raise KeyError(f"incident {incident_id} not found in this organisation")
        org = await db.get(Organization, org_id)
        assert org is not None
        dets = (
            (
                await db.execute(
                    select(DetectionRow)
                    .where(DetectionRow.org_id == org_id, DetectionRow.incident_id == inc.id)
                    .order_by(DetectionRow.first_seen)
                )
            )
            .scalars()
            .all()
        )
        scores = {
            s.detection_id: s
            for s in (
                await db.execute(select(TriageScoreRow).where(TriageScoreRow.detection_id.in_([d.id for d in dets])))
            ).scalars()
        }
        anomalies = (
            (
                await db.execute(
                    select(AnomalyRow).where(
                        AnomalyRow.org_id == org_id, AnomalyRow.detection_id.in_([d.id for d in dets])
                    )
                )
            )
            .scalars()
            .all()
        )
        ents = merge_entities(inc.entities, None)
        assets = await assets_by_names_or_ips(db, org_id, ents["hosts"] + ents["cloudAccounts"] + ents["ips"])
        idents = await identities_by_keys(db, org_id, ents["users"] + ents["emails"])
        prior = (
            (
                await db.execute(
                    select(InvestigationRun.id)
                    .where(InvestigationRun.org_id == org_id, InvestigationRun.incident_id == inc.id)
                    .order_by(InvestigationRun.started_at)
                )
            )
            .scalars()
            .all()
        )
        profile = org.profile or {}
        return IncidentContext(
            org=OrgProfile(
                org_id=org.id,
                name=org.name,
                timezone=org.timezone,
                entities=tuple(profile.get("entities") or ()),
                crown_jewel_tags=tuple(
                    (org.settings or {}).get("crownJewelAssetTags") or profile.get("crownJewelTags") or ()
                ),
            ),
            incident_id=inc.id,
            reference=inc.reference,
            title=inc.title,
            kind=inc.kind,  # type: ignore[arg-type]
            severity=inc.severity,  # type: ignore[arg-type]
            status=inc.status,  # type: ignore[arg-type]
            attack=_attack(inc.attack),
            entities=_entities(inc.entities),
            detections=tuple(_detection(d, scores.get(d.id)) for d in dets),
            anomalies=tuple(
                Anomaly(
                    id=a.id,
                    org_id=a.org_id,
                    entity_type=a.entity_type,  # type: ignore[arg-type]
                    entity_id=a.entity_id,
                    window_start=a.window_start,
                    window_end=a.window_end,
                    score=a.score,
                    isolation_score=a.isolation_score,
                    robust_z=a.robust_z or {},
                    features=a.features or {},
                    reasons=_reasons(a.reasons),
                    model_version=a.model_version,
                    detection_id=a.detection_id,
                )
                for a in anomalies
            ),
            assets=tuple(to_asset_record(a) for a in assets),
            identities=tuple(to_identity_record(i) for i in idents),
            first_seen=inc.first_seen,
            last_seen=inc.last_seen,
            prior_runs=tuple(prior),
        )


merge_into = _merge_into
