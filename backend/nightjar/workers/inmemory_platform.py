"""An in-memory implementation of the platform interfaces builder B calls (incidents, investigations, approvals,
directory, ticketing; API contract Part 3), with the Halden Logistics sample inventory.

Used by the pipeline's unit tests, `make train` and `make eval`, so the pipeline, agents and tool servers can be
exercised end to end without the platform's database. `install()` swaps it in behind the real `*.api` modules
(the pipeline always calls those modules by attribute) and `uninstall()` restores them. Behaviour follows the
contract: idempotent upserts, gap-free step numbers, approval policy (responder; plus executive for crown jewels
and privileged identities), single-use signed tokens bound to org + action + target, targets bounded to the
incident's entities.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from nightjar import clock
from nightjar.contracts import (
    DESTRUCTIVE_ACTIONS,
    SEVERITY_ORDER,
    ActionResult,
    AgentStep,
    Anomaly,
    ApprovalRef,
    AssetRecord,
    CorrelationUpdate,
    Detection,
    EnrichmentResult,
    IdentityRecord,
    IncidentContext,
    IncidentRef,
    OpenIncidentIndexEntry,
    OrgProfile,
    ProposedAction,
    RunRef,
    TriageScore,
    TriageVerdict,
)
from nightjar.workers.replay.entities import HOST, PEOPLE, email_of

NS = uuid.UUID("9a7b6c5d-4e3f-4a2b-8c1d-0e9f8a7b6c5d")
HALDEN_ORG = uuid.uuid5(NS, "halden-logistics")
LEGAL_ENTITIES = (
    {
        "id": "halden-us",
        "name": "Halden Logistics, Inc.",
        "jurisdiction": "US",
        "country": "US",
        "timezone": "America/New_York",
        "sec": {"registrant": True, "filerType": "domestic", "ticker": "HLDN"},
    },
    {
        "id": "halden-eu",
        "name": "Halden Logistics NV",
        "jurisdiction": "EU",
        "country": "BE",
        "timezone": "Europe/Brussels",
        "nis2": {
            "applies": True,
            "sector": "postal_courier",
            "entityType": "important",
            "memberState": "BE",
            "authority": "ccb",
        },
        "gdpr": {"controller": True, "leadAuthority": "be-dpa"},
    },
    {
        "id": "halden-au",
        "name": "Halden Logistics Pty Ltd",
        "jurisdiction": "AU",
        "country": "AU",
        "timezone": "Australia/Melbourne",
        "soci": {"responsibleEntity": True, "assetClasses": ["freight_services"], "assets": ["mel-depot-gw-01"]},
    },
)
_SITE = {"memphis": ("Memphis hub", "US"), "antwerp": ("Antwerp DC", "EU"), "melbourne": ("Melbourne depot", "AU")}
_ASSET_EXTRA: dict[str, tuple[str, str | None, tuple[str, ...], tuple[str, ...]]] = {
    # name: (criticality, business service, tags, CVEs)
    "mem-dispatch-01": ("crown_jewel", "Dispatch platform", ("crown_jewel", "shared_platform"), ()),
    "mem-sql-01": ("high", "Dispatch database", (), ()),
    "mem-citrix-01": ("high", "Remote access gateway", ("internet_facing",), ("CVE-2023-4966",)),
    "mem-ws-031": ("low", None, (), ()),
    "mem-ws-040": ("low", None, (), ()),
    "ant-wms-01": ("crown_jewel", "Warehouse management", ("crown_jewel",), ()),
    "ant-dc-01": ("crown_jewel", "Directory", ("crown_jewel",), ()),
    "ant-fs-01": ("high", "File shares", (), ()),
    "ant-backup-01": ("high", "Backups", (), ()),
    "ant-vpn-01": ("high", "Remote access", ("internet_facing",), ("CVE-2024-21762",)),
    "ant-ws-014": ("low", None, (), ()),
    "ant-ws-022": ("low", None, (), ()),
    "mel-vpn-01": ("high", "Remote access", ("internet_facing",), ()),
    "mel-depot-gw-01": ("high", "Fleet telematics (Linehaul East)", ("soci_asset",), ()),
    "mel-ws-007": ("low", None, (), ()),
}
_PRIVILEGED = {"adm-ejanssens", "svc-dispatch", "svc-backup", "e.janssens", "c.nguyen", "h.morrison"}


def halden_assets() -> list[AssetRecord]:
    out = []
    for h in HOST.values():
        crit, svc, tags, cves = _ASSET_EXTRA.get(h.name, ("low", None, (), ()))
        site, jur = _SITE[h.site]
        kind = {"server": "server", "workstation": "workstation", "network": "network", "ot_gateway": "ot_gateway"}[
            h.kind
        ]
        out.append(
            AssetRecord(
                id=uuid.uuid5(NS, f"asset:{h.name}"),
                name=h.name,
                kind=kind,  # type: ignore[arg-type]
                ips=(h.ip,),
                os="Windows Server 2022" if kind == "server" else "Windows 11" if kind == "workstation" else None,
                site=site,
                jurisdiction=jur,  # type: ignore[arg-type]
                owner=h.owner,
                business_service=svc,
                criticality=crit,  # type: ignore[arg-type]
                tags=tags,
                isolated=False,
                vulnerabilities=cves,
            )
        )
    out.append(
        AssetRecord(
            uuid.uuid5(NS, "asset:aws-prod"),
            "aws-prod",
            "cloud_account",
            (),
            None,
            "AWS us-east-1",
            "US",
            "c.nguyen",
            "Customer portal, invoices",
            "high",
            ("cloud",),
            False,
            (),
        )
    )
    return out


def halden_identities() -> list[IdentityRecord]:
    out = []
    for p in PEOPLE:
        _, jur = _SITE[p.site]
        out.append(
            IdentityRecord(
                id=uuid.uuid5(NS, f"id:{p.username}"),
                username=p.username,
                display_name=p.name,
                email=email_of(p.username),
                department={"contractor": "Data analytics"}.get(
                    p.kind, "Operations" if p.site == "melbourne" else "Finance"
                ),
                title="Contract data analyst" if p.kind == "contractor" else "Staff",
                jurisdiction=jur,  # type: ignore[arg-type]
                manager=None,
                privileged=p.username in _PRIVILEGED,
                mfa="push",
                usual_countries=(p.country,),
                disabled=False,
                kind="contractor" if p.kind == "contractor" else "employee",
            )
        )
    for svc, jur in (("svc-dispatch", "US"), ("svc-backup", "EU"), ("ci-deploy", "US"), ("adm-ejanssens", "EU")):
        out.append(
            IdentityRecord(
                id=uuid.uuid5(NS, f"id:{svc}"),
                username=svc,
                display_name=svc,
                email=email_of(svc),
                department="IT",
                title="Service account" if svc != "adm-ejanssens" else "Privileged administrator",
                jurisdiction=jur,  # type: ignore[arg-type]
                manager=None,
                privileged=svc in _PRIVILEGED,
                mfa="none",
                usual_countries=("US",) if jur == "US" else ("BE",),
                disabled=False,
                kind="service" if svc != "adm-ejanssens" else "employee",
            )
        )
    return out


@dataclass
class _Incident:
    id: uuid.UUID
    reference: str
    org_id: uuid.UUID
    key: str
    update: CorrelationUpdate
    status: str = "new"
    detection_ids: list[uuid.UUID] = field(default_factory=list)
    verdict: TriageVerdict | None = None
    triaged_at: datetime | None = None
    first_detection_real_at: datetime | None = None
    merged_into: uuid.UUID | None = None


@dataclass
class _Run:
    ref: RunRef
    org_id: uuid.UUID
    trigger: str
    steps: list[AgentStep] = field(default_factory=list)
    status: str = "queued"
    verdict: TriageVerdict | None = None
    enrichment: EnrichmentResult | None = None
    proposals: tuple[ProposedAction, ...] = ()
    error: str | None = None
    started_real: datetime | None = None
    finished_real: datetime | None = None


@dataclass
class _Approval:
    id: uuid.UUID
    org_id: uuid.UUID
    incident_id: uuid.UUID
    run_id: uuid.UUID | None
    action: ProposedAction
    status: str
    required: tuple[str, ...]
    approvals: list[dict[str, Any]] = field(default_factory=list)
    token: str | None = None
    nonce: str | None = None
    used: bool = False
    result: ActionResult | None = None


class InMemoryPlatform:
    def __init__(self, *, reference_start: int = 1036) -> None:
        self.assets: dict[uuid.UUID, dict[str, AssetRecord]] = {}
        self.identities: dict[uuid.UUID, dict[str, IdentityRecord]] = {}
        self.orgs: dict[uuid.UUID, OrgProfile] = {}
        self.detections: dict[uuid.UUID, Detection] = {}
        self.scores: dict[uuid.UUID, TriageScore] = {}
        self.anomalies: dict[uuid.UUID, Anomaly] = {}
        self.incidents: dict[uuid.UUID, _Incident] = {}
        self.by_key: dict[tuple[uuid.UUID, str], uuid.UUID] = {}
        self.runs: dict[uuid.UUID, _Run] = {}
        self.approvals: dict[uuid.UUID, _Approval] = {}
        self.tickets: dict[uuid.UUID, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.detection_recorded_real: dict[uuid.UUID, datetime] = {}
        self._refno = itertools.count(reference_start)
        self._ticket_no = itertools.count(301)
        self.effects: list[tuple[str, str, uuid.UUID]] = []
        self.sign_in_countries: dict[str, list[str]] = {}
        self._saved: dict[str, dict[str, Any]] = {}
        self.add_org(HALDEN_ORG, "Halden Logistics", halden_assets(), halden_identities())

    # ── setup ──────────────────────────────────────────────────────────────
    def add_org(
        self,
        org_id: uuid.UUID,
        name: str,
        assets: Sequence[AssetRecord] = (),
        identities: Sequence[IdentityRecord] = (),
    ) -> None:
        self.orgs[org_id] = OrgProfile(
            org_id=org_id,
            name=name,
            timezone="Europe/Brussels",
            entities=LEGAL_ENTITIES,
            crown_jewel_tags=("crown_jewel",),
        )
        self.assets[org_id] = {a.name: a for a in assets}
        self.identities[org_id] = {i.username: i for i in identities}

    # ── incidents.api ──────────────────────────────────────────────────────
    async def record_detections(self, org_id: uuid.UUID, detections: Sequence[Detection]) -> None:
        for d in detections:
            if d.org_id != org_id:
                raise ValueError("detection belongs to another organisation")
            if d.id not in self.detections:
                self.detection_recorded_real[d.id] = clock.real_now()
            self.detections[d.id] = d
            if d.triage:
                self.scores[d.id] = d.triage

    async def record_triage_scores(self, org_id: uuid.UUID, scores: Sequence[TriageScore]) -> None:
        for s in scores:
            self.scores[s.detection_id] = s
            d = self.detections.get(s.detection_id)
            if d:
                self.detections[s.detection_id] = replace(d, triage=s)

    async def record_anomalies(self, org_id: uuid.UUID, anomalies: Sequence[Anomaly]) -> None:
        for a in anomalies:
            self.anomalies[a.id] = a

    async def upsert_incident(self, update: CorrelationUpdate) -> IncidentRef:
        key = (update.org_id, update.correlation_key)
        iid = self.by_key.get(key)
        created = False
        if iid is None:
            iid = uuid.uuid5(NS, f"incident:{update.org_id}:{update.correlation_key}")
            self.incidents[iid] = _Incident(
                iid, f"NJ-{next(self._refno)}", update.org_id, update.correlation_key, update
            )
            self.by_key[key] = iid
            created = True
            self.audit.append({"action": "incident.opened", "incidentId": str(iid)})
        inc = self.incidents[iid]
        inc.update = update
        inc.detection_ids = list(update.detection_ids)
        for other in update.merge_incident_ids:
            o = self.incidents.get(other)
            if o and o.id != iid:
                o.status, o.merged_into = "closed", iid
        if inc.first_detection_real_at is None:
            times = [self.detection_recorded_real.get(d) for d in update.detection_ids]
            inc.first_detection_real_at = min((t for t in times if t), default=clock.real_now())
        return IncidentRef(id=iid, reference=inc.reference, created=created, status=inc.status)  # type: ignore[arg-type]

    async def open_incident_index(self, org_id: uuid.UUID, *, since: datetime) -> list[OpenIncidentIndexEntry]:
        return [
            OpenIncidentIndexEntry(
                incident_id=i.id,
                correlation_key=i.key,
                entities=i.update.entities,
                attack_tactics=tuple(sorted({t for a in i.update.attack for t in a.tactics})),
                last_seen=i.update.last_seen,
                status=i.status,  # type: ignore[arg-type]
            )
            for i in self.incidents.values()
            if i.org_id == org_id and i.status not in ("resolved", "closed") and i.update.last_seen >= since
        ]

    async def get_incident_context(self, org_id: uuid.UUID, incident_id: uuid.UUID) -> IncidentContext:
        inc = self.incidents.get(incident_id)
        if inc is None or inc.org_id != org_id:
            raise KeyError(incident_id)
        u = inc.update
        dets = tuple(self.detections[d] for d in inc.detection_ids if d in self.detections)
        ent = u.entities
        assets = tuple(a for n, a in self.assets[org_id].items() if n in {h.lower() for h in ent.hosts})
        idents = tuple(i for n, i in self.identities[org_id].items() if n in {x.lower() for x in ent.users})
        anomalies = tuple(
            a
            for a in self.anomalies.values()
            if a.org_id == org_id
            and (a.entity_id in ent.users or a.entity_id in ent.hosts)
            and u.first_seen - timedelta(hours=1) <= a.window_start <= u.last_seen + timedelta(hours=1)
        )
        severity = u.severity
        if any(a.criticality == "crown_jewel" for a in assets) and severity != "critical" and not u.all_likely_noise:
            severity = {"info": "low", "low": "medium", "medium": "high", "high": "critical"}[severity]  # type: ignore[assignment]
        return IncidentContext(
            org=self.orgs[org_id],
            incident_id=inc.id,
            reference=inc.reference,
            title=u.title,
            kind=u.kind,
            severity=severity,
            status=inc.status,  # type: ignore[arg-type]
            attack=u.attack,
            entities=ent,
            detections=dets,
            anomalies=anomalies,
            assets=assets,
            identities=idents,
            first_seen=u.first_seen,
            last_seen=u.last_seen,
            prior_runs=tuple(r.ref.run_id for r in self.runs.values() if r.ref.incident_id == incident_id),
        )

    # ── investigations.api ─────────────────────────────────────────────────
    async def start_run(
        self, org_id: uuid.UUID, incident_id: uuid.UUID, *, trigger: str, requested_by: uuid.UUID | None
    ) -> RunRef:
        if incident_id not in self.incidents or self.incidents[incident_id].org_id != org_id:
            raise KeyError(incident_id)
        ref = RunRef(run_id=uuid.uuid4(), incident_id=incident_id, status="queued")
        self.runs[ref.run_id] = _Run(ref, org_id, trigger, started_real=clock.real_now())
        return ref

    async def mark_running(self, run_id: uuid.UUID) -> None:
        self.runs[run_id].status = "running"

    async def append_step(self, run_id: uuid.UUID, step: AgentStep) -> int:
        run = self.runs[run_id]
        run.steps.append(step)
        return len(run.steps)

    async def finish_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        verdict: TriageVerdict | None,
        enrichment: EnrichmentResult | None,
        proposals: tuple[ProposedAction, ...],
        error: str | None = None,
    ) -> None:
        run = self.runs[run_id]
        run.status, run.verdict, run.enrichment, run.proposals, run.error = (
            status,
            verdict,
            enrichment,
            proposals,
            error,
        )
        run.finished_real = clock.real_now()
        inc = self.incidents[run.ref.incident_id]
        if verdict is not None and inc.verdict is None:
            inc.verdict = verdict
            inc.triaged_at = clock.real_now()
            if verdict.verdict == "true_positive":
                inc.status = "investigating"
            elif verdict.verdict in ("benign", "false_positive"):
                inc.status = "resolved"
            else:
                inc.status = "triage"

    def alert_to_triage_seconds(self) -> list[float]:
        return [
            (i.triaged_at - i.first_detection_real_at).total_seconds()
            for i in self.incidents.values()
            if i.triaged_at and i.first_detection_real_at
        ]

    # ── approvals.api ──────────────────────────────────────────────────────
    def _entity_pool(self, inc: _Incident, target_type: str) -> set[str]:
        e = inc.update.entities
        pools = {"host": e.hosts, "user": e.users, "ip": e.ips, "access_key": e.access_keys, "email": e.emails}
        return {x.lower() for x in pools.get(target_type, ())}

    async def request_approval(
        self, org_id: uuid.UUID, incident_id: uuid.UUID, run_id: uuid.UUID | None, action: ProposedAction
    ) -> ApprovalRef:
        inc = self.incidents.get(incident_id)
        if inc is None or inc.org_id != org_id:
            raise ValueError("incident not found in this organisation")
        if action.target_type != "ticket" and action.target.lower() not in self._entity_pool(inc, action.target_type):
            self.audit.append({"action": "action.refused", "reason": "target_not_in_incident", "target": action.target})
            raise ValueError("target_not_in_incident")
        for a in self.approvals.values():
            if (a.incident_id, a.action.kind, a.action.target) == (
                incident_id,
                action.kind,
                action.target,
            ) and a.status == "pending":
                return self._ref(a)
        destructive = action.kind in DESTRUCTIVE_ACTIONS
        required: tuple[str, ...] = ()
        if destructive:
            required = ("responder",)
            asset = self.assets[org_id].get(action.target.lower())
            ident = self.identities[org_id].get(action.target.lower())
            if (asset and asset.criticality == "crown_jewel") or (ident and ident.privileged):
                required = ("responder", "executive")
        appr = _Approval(
            uuid.uuid4(), org_id, incident_id, run_id, action, "pending" if destructive else "auto_approved", required
        )
        self.approvals[appr.id] = appr
        self.audit.append({"action": "action.proposed", "kind": action.kind, "target": action.target})
        return self._ref(appr)

    def _ref(self, a: _Approval) -> ApprovalRef:
        return ApprovalRef(
            approval_id=a.id,
            status=a.status,  # type: ignore[arg-type]
            required_roles=a.required,
            approvals=tuple(a.approvals),
            token=a.token if not a.used else None,
        )

    async def approval_target_in_incident(self, org_id: uuid.UUID, approval_id: uuid.UUID, target: str) -> bool:
        a = self.approvals.get(approval_id)
        if a is None or a.org_id != org_id or a.action.target != target:
            return False
        inc = self.incidents.get(a.incident_id)
        return inc is not None and target.lower() in self._entity_pool(inc, a.action.target_type)

    async def get_run_steps(self, run_id: uuid.UUID) -> list[dict[str, Any]]:
        from nightjar.contracts import to_doc

        return [{**to_doc(s), "seq": i + 1} for i, s in enumerate(self.runs[run_id].steps)]

    async def get_approval(self, approval_id: uuid.UUID) -> ApprovalRef:
        return self._ref(self.approvals[approval_id])

    def approve(self, approval_id: uuid.UUID, role: str, user: str = "tester") -> ApprovalRef:
        """Simulate a person approving (the API's job in production): signs the token once all roles agreed."""
        from nightjar.core.security import sign_approval_token

        a = self.approvals[approval_id]
        if a.status != "pending":
            raise ValueError("already decided")
        a.approvals.append({"userId": user, "role": role, "decidedAt": clock.now().isoformat()})
        if {x["role"] for x in a.approvals} >= set(a.required):
            a.status = "approved"
            a.token, claims = sign_approval_token(
                approval_id=str(a.id), org_id=str(a.org_id), action=a.action.kind, target=a.action.target
            )
            a.nonce = claims.nonce
        return self._ref(a)

    def reject(self, approval_id: uuid.UUID) -> None:
        self.approvals[approval_id].status = "rejected"

    async def consume(self, org_id: uuid.UUID, approval_id: uuid.UUID, nonce: str) -> bool:
        a = self.approvals.get(approval_id)
        if a is None or a.org_id != org_id or a.status != "approved" or a.used or a.nonce != nonce:
            return False
        a.used = True
        return True

    async def record_result(self, org_id: uuid.UUID, result: ActionResult) -> None:
        a = self.approvals.get(result.approval_id)
        if a is not None and a.org_id == org_id:
            a.result = result
            a.status = "executed" if result.executed else ("failed" if result.status == "failed" else a.status)
        self.audit.append(
            {"action": "action.executed" if result.executed else "action.refused", "detail": result.detail}
        )

    # ── directory.api ──────────────────────────────────────────────────────
    async def find_asset(self, org_id: uuid.UUID, name_or_ip: str) -> AssetRecord | None:
        q = (name_or_ip or "").lower()
        for a in self.assets.get(org_id, {}).values():
            if a.name == q or q in a.ips:
                return a
        return None

    async def search_assets(self, org_id: uuid.UUID, query: str, *, limit: int = 20) -> list[AssetRecord]:
        q = (query or "").lower()
        return [
            a for a in self.assets.get(org_id, {}).values() if q in a.name or q in (a.business_service or "").lower()
        ][:limit]

    async def find_identity(self, org_id: uuid.UUID, username_or_email: str) -> IdentityRecord | None:
        q = (username_or_email or "").lower()
        for i in self.identities.get(org_id, {}).values():
            if i.username == q or i.email.lower() == q:
                return i
        return None

    async def search_identities(self, org_id: uuid.UUID, query: str, *, limit: int = 20) -> list[IdentityRecord]:
        q = (query or "").lower()
        return [i for i in self.identities.get(org_id, {}).values() if q in i.username or q in i.display_name.lower()][
            :limit
        ]

    async def recent_sign_in_countries(self, org_id: uuid.UUID, username: str, *, days: int = 90) -> list[str]:
        ident = await self.find_identity(org_id, username)
        return list(self.sign_in_countries.get(username, ident.usual_countries if ident else ()))

    def _effect(self, kind: str, target: str, approval_id: uuid.UUID) -> None:
        a = self.approvals.get(approval_id)
        if a is None or not a.used:
            raise PermissionError("effector called without a consumed approval")
        self.effects.append((kind, target, approval_id))

    async def set_host_isolation(
        self, org_id: uuid.UUID, asset_name: str, *, isolated: bool, approval_id: uuid.UUID
    ) -> None:
        a = self.assets[org_id].get(asset_name.lower())
        if a is None:
            raise KeyError(asset_name)
        self._effect("isolate_host" if isolated else "release_host", asset_name, approval_id)
        self.assets[org_id][a.name] = replace(a, isolated=isolated)

    async def set_account_disabled(
        self, org_id: uuid.UUID, username: str, *, disabled: bool, approval_id: uuid.UUID
    ) -> None:
        i = self.identities[org_id].get(username.lower())
        if i is None:
            raise KeyError(username)
        self._effect("disable_account" if disabled else "enable_account", username, approval_id)
        self.identities[org_id][i.username] = replace(i, disabled=disabled)

    async def revoke_access_key(self, org_id: uuid.UUID, access_key_id: str, *, approval_id: uuid.UUID) -> None:
        self._effect("revoke_access_key", access_key_id, approval_id)

    async def set_ip_block(self, org_id: uuid.UUID, ip: str, *, blocked: bool, approval_id: uuid.UUID) -> None:
        self._effect("block_ip" if blocked else "unblock_ip", ip, approval_id)

    async def revoke_identity_sessions(self, org_id: uuid.UUID, username: str, *, approval_id: uuid.UUID) -> None:
        self._effect("revoke_sessions", username, approval_id)

    async def reset_identity_password(self, org_id: uuid.UUID, username: str, *, approval_id: uuid.UUID) -> None:
        self._effect("reset_password", username, approval_id)

    async def quarantine_message(self, org_id: uuid.UUID, message_id: str, *, approval_id: uuid.UUID) -> None:
        self._effect("quarantine_email", message_id, approval_id)

    # ── ticketing.api ──────────────────────────────────────────────────────
    async def create_ticket(
        self, org_id: uuid.UUID, incident_id: uuid.UUID, *, title: str, body: str, priority: str, assignee_team: str
    ) -> dict[str, Any]:
        for t in self.tickets.values():
            if t["incidentId"] == str(incident_id) and t["title"] == title:
                return t
        tid = uuid.uuid4()
        t = {
            "id": str(tid),
            "key": f"OPS-{next(self._ticket_no)}",
            "incidentId": str(incident_id),
            "title": title,
            "body": body,
            "priority": priority,
            "assigneeTeam": assignee_team,
            "status": "open",
            "createdBy": {"type": "agent", "name": "Response agent"},
            "createdAt": clock.now().isoformat(),
            "comments": [],
            "orgId": str(org_id),
        }
        self.tickets[tid] = t
        return t

    async def add_comment(self, org_id: uuid.UUID, ticket_id: uuid.UUID, *, body: str, author: str) -> dict[str, Any]:
        t = self.tickets[ticket_id]
        if t["orgId"] != str(org_id):
            raise KeyError(ticket_id)
        t["comments"].append({"author": author, "body": body, "at": clock.now().isoformat()})
        return t

    async def get_ticket(self, org_id: uuid.UUID, ticket_id: uuid.UUID) -> dict[str, Any] | None:
        t = self.tickets.get(ticket_id)
        return t if t and t["orgId"] == str(org_id) else None

    # ── install / uninstall ────────────────────────────────────────────────
    _BINDINGS = {
        "nightjar.incidents.api": (
            "record_detections",
            "record_triage_scores",
            "record_anomalies",
            "upsert_incident",
            "open_incident_index",
            "get_incident_context",
        ),
        "nightjar.investigations.api": ("start_run", "mark_running", "append_step", "finish_run", "get_run_steps"),
        "nightjar.approvals.api": (
            "request_approval",
            "get_approval",
            "consume",
            "record_result",
            "approval_target_in_incident",
        ),
        "nightjar.directory.api": (
            "find_asset",
            "search_assets",
            "find_identity",
            "search_identities",
            "recent_sign_in_countries",
            "set_host_isolation",
            "set_account_disabled",
            "revoke_access_key",
            "set_ip_block",
            "revoke_identity_sessions",
            "reset_identity_password",
            "quarantine_message",
        ),
        "nightjar.ticketing.api": ("create_ticket", "add_comment", "get_ticket"),
    }

    def install(self) -> InMemoryPlatform:
        import importlib

        for mod_name, names in self._BINDINGS.items():
            mod = importlib.import_module(mod_name)
            saved = self._saved.setdefault(mod_name, {})
            for n in names:
                if n not in saved:
                    saved[n] = getattr(mod, n, None)
                setattr(mod, n, getattr(self, n))
        return self

    def uninstall(self) -> None:
        import importlib

        for mod_name, saved in self._saved.items():
            mod = importlib.import_module(mod_name)
            for n, fn in saved.items():
                if fn is None:
                    if hasattr(mod, n):
                        delattr(mod, n)
                else:
                    setattr(mod, n, fn)
        self._saved.clear()

    # ── helpers for eval/tests ─────────────────────────────────────────────
    def incidents_sorted(self) -> list[_Incident]:
        return sorted((i for i in self.incidents.values() if i.merged_into is None), key=lambda i: int(i.reference[3:]))

    def severity_of(self, incident_id: uuid.UUID) -> str:
        u = self.incidents[incident_id].update
        return max(
            (self.detections[d].severity for d in u.detection_ids if d in self.detections),
            key=lambda s: SEVERITY_ORDER[s],
            default=u.severity,
        )
