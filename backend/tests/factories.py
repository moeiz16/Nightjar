"""Test factories: the Halden and Brightline organisations, detections and incidents created through the same
platform functions builder B's pipeline calls (so those are exercised too)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from nightjar import clock
from nightjar.contracts import (
    AgentStep,
    AttackRef,
    CorrelationUpdate,
    Detection,
    Entities,
    EventSnapshot,
    IncidentRef,
    ReportableFacts,
    TriageScore,
    TriageVerdict,
    UntrustedText,
)
from nightjar.db.session import session_scope
from nightjar.seed import data
from nightjar.seed.builders import add_inventory, create_org
from tests.conftest import DEMO_PASSWORD

HALDEN = {role: email for email, _n, role, _t in data.HALDEN_USERS if role != "analyst"} | {
    "analyst": "aiko.tanaka@halden.example",
    "analyst2": "lukas.peeters@halden.example",
}
OLU = "olu.adebayo@brightline.example"


async def halden(*, profile: bool = True) -> dict[str, Any]:
    async with session_scope() as db:
        org, people = await create_org(
            db,
            name="Halden Logistics",
            slug="halden-logistics",
            timezone="Europe/Brussels",
            profile=data.HALDEN_PROFILE if profile else {},
            users=data.HALDEN_USERS,
            settings={"regimeContacts": data.regime_contacts()},
        )
        await add_inventory(
            db,
            org.id,
            assets=data.HALDEN_ASSETS,
            identities=data.HALDEN_IDENTITIES,
            domain="halden.example",
            access_keys=data.HALDEN_ACCESS_KEYS,
        )
        return {"org_id": org.id, "users": {e: u.id for e, u in people.items()}}


async def brightline() -> dict[str, Any]:
    async with session_scope() as db:
        org, people = await create_org(
            db,
            name="Brightline Couriers",
            slug="brightline-couriers",
            timezone="Europe/Amsterdam",
            profile=data.BRIGHTLINE_PROFILE,
            users=data.BRIGHTLINE_USERS,
        )
        await add_inventory(
            db,
            org.id,
            assets=data.BRIGHTLINE_ASSETS,
            identities=data.BRIGHTLINE_IDENTITIES,
            domain="brightline.example",
        )
        return {"org_id": org.id, "users": {e: u.id for e, u in people.items()}}


async def sign_in(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    await client.get("/api/v1/auth/csrf")
    headers = {"X-CSRF-Token": client.cookies.get("nj_csrf") or ""}
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD}, headers=headers)
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": client.cookies.get("nj_csrf") or ""}


def attack(*ids: str) -> tuple[AttackRef, ...]:
    tactic = {
        "T1566.002": "initial-access",
        "T1078.004": "initial-access",
        "T1621": "credential-access",
        "T1003.001": "credential-access",
        "T1021.002": "lateral-movement",
        "T1110.003": "credential-access",
        "T1530": "collection",
        "T1567.002": "exfiltration",
        "T1190": "initial-access",
    }
    return tuple(AttackRef(i, f"Technique {i}", (tactic.get(i, "execution"),)) for i in ids)


def detection(
    org_id: uuid.UUID,
    *,
    title: str = "Suspicious sign-in",
    severity: str = "high",
    hosts: tuple[str, ...] = (),
    users: tuple[str, ...] = (),
    ips: tuple[str, ...] = (),
    access_keys: tuple[str, ...] = (),
    emails: tuple[str, ...] = (),
    techniques: tuple[str, ...] = ("T1078.004",),
    at: datetime | None = None,
    band: str | None = "likely_real",
    source: str = "sigma",
    fields: dict | None = None,
    event_ids: tuple[str, ...] = (),
) -> Detection:
    at = at or clock.now() - timedelta(minutes=30)
    did = uuid.uuid4()
    return Detection(
        id=did,
        org_id=org_id,
        source=source,
        rule_id=f"rule-{title[:10]}",
        rule_title=title,  # type: ignore[arg-type]
        severity=severity,
        confidence=0.8,
        attack=attack(*techniques),  # type: ignore[arg-type]
        entities=Entities(hosts=hosts, users=users, ips=ips, access_keys=access_keys, emails=emails),
        first_seen=at,
        last_seen=at + timedelta(minutes=2),
        event_ids=event_ids or (uuid.uuid4().hex,),
        evidence=(
            EventSnapshot(
                uuid.uuid4().hex,
                at,
                "windows.security",
                "logon",
                UntrustedText("sign-in", "message"),
                hosts[0] if hosts else None,
                users[0] if users else None,
                {},
            ),
        ),
        summary=f"{title} (test)",
        fields=fields or {},
        triage=TriageScore(
            did, 0.9 if band == "likely_real" else 0.05, band, 0.5, (), "triage-test", band == "likely_noise"
        )  # type: ignore[arg-type]
        if band
        else None,
    )


async def incident(
    org_id: uuid.UUID,
    *,
    key: str | None = None,
    title: str = "Unusual sign-in for j.okafor",
    kind: str = "credential_misuse",
    severity: str = "high",
    hosts: tuple[str, ...] = (),
    users: tuple[str, ...] = ("j.okafor",),
    ips: tuple[str, ...] = (),
    access_keys: tuple[str, ...] = (),
    emails: tuple[str, ...] = (),
    techniques: tuple[str, ...] = ("T1078.004",),
    detections: int = 2,
    noise: bool = False,
    at: datetime | None = None,
) -> IncidentRef:
    from nightjar.incidents import api as incidents

    at = at or clock.now() - timedelta(hours=1)
    dets = [
        detection(
            org_id,
            title=f"{title} #{i}",
            severity=severity,
            hosts=hosts,
            users=users,
            ips=ips,
            access_keys=access_keys,
            emails=emails,
            techniques=techniques,
            at=at + timedelta(minutes=i),
            band="likely_noise" if noise else "likely_real",
        )
        for i in range(detections)
    ]
    await incidents.record_detections(org_id, dets)
    return await incidents.upsert_incident(
        CorrelationUpdate(
            org_id=org_id,
            correlation_key=key or f"corr:{uuid.uuid4().hex[:24]}",
            detection_ids=tuple(d.id for d in dets),
            kind=kind,
            title=title,  # type: ignore[arg-type]
            severity=severity,
            attack=attack(*techniques),  # type: ignore[arg-type]
            entities=Entities(hosts=hosts, users=users, ips=ips, access_keys=access_keys, emails=emails),
            first_seen=at,
            last_seen=at + timedelta(minutes=detections + 2),
            summary=f"{title}.",
            max_triage_probability=0.05 if noise else 0.9,
            all_likely_noise=noise,
        )
    )


def facts(**kw: Any) -> ReportableFacts:
    base: dict[str, Any] = {
        "malicious_suspected": True,
        "affected_jurisdictions": ("EU",),
        "affected_entities": ("Halden Logistics NV",),
        "service_disruption": "outage",
        "disruption_hours": 9.0,
        "critical_asset_impact": "relevant",
        "personal_data_involved": False,
        "records_affected_estimate": None,
        "cross_border": True,
        "financial_impact_estimate_eur": 750_000.0,
    }
    base.update(kw)
    return ReportableFacts(**base)


async def triage(
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    verdict: str = "true_positive",
    confidence: float = 0.93,
    with_facts: ReportableFacts | None = None,
    steps: int = 3,
) -> uuid.UUID:
    from nightjar.investigations import api as inv

    run = await inv.start_run(org_id, incident_id, trigger="auto", requested_by=None)
    await inv.mark_running(run.run_id)
    for i in range(steps):
        await inv.append_step(
            run.run_id,
            AgentStep(
                agent="triage" if i != 1 else "enrichment",
                kind="tool_call" if i != 1 else "handoff_out",
                title=f"Step {i + 1}",
                started_at=clock.now(),
                duration_ms=12,
                tool="intel.lookup_kev",
                input_hash=f"{i:064d}",
                model=None,
                detail={"input": {"i": i}},
            ),
        )
    await inv.finish_run(
        run.run_id,
        status="completed",
        verdict=TriageVerdict(verdict, confidence, "Evidence points one way.", (), 0.91, with_facts),  # type: ignore[arg-type]
        enrichment=None,
        proposals=(),
    )
    return run.run_id


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def event(
    org_id: uuid.UUID,
    *,
    action: str = "logon",
    outcome: str = "success",
    at: datetime | None = None,
    user: str | None = "j.okafor",
    host: str | None = "ant-ws-014",
    src: str | None = "198.51.100.77",
    country: str | None = "RO",
    dataset: str = "windows.security",
    category: tuple[str, ...] = ("authentication",),
    process: str | None = None,
    cmd: str | None = None,
    cloud_api: str | None = None,
    message: str = "An account was logged on",
    event_id: str | None = None,
) -> Any:
    from nightjar.contracts import CloudInfo, Endpoint, HostRef, NormalizedEvent, ProcessInfo, UserRef

    at = at or clock.now() - timedelta(minutes=10)
    return NormalizedEvent(
        org_id=org_id,
        event_id=event_id or uuid.uuid4().hex,
        timestamp=at,
        ingested_at=clock.now(),
        dataset=dataset,
        kind="event",
        category=category,
        type=("start",),
        action=action,
        outcome=outcome,  # type: ignore[arg-type]
        message=UntrustedText(message, "message"),
        severity=None,
        host=HostRef(host, ("10.20.20.14",)) if host else None,
        user=UserRef(user) if user else None,
        source=Endpoint(ip=src, geo_country=country, as_org="Example Hosting VPS SRL") if src else None,
        process=ProcessInfo(name=process, command_line=cmd) if process else None,
        cloud=CloudInfo(provider="aws", api=cloud_api, access_key_id="AKIAEXAMPLEHALDEN01") if cloud_api else None,
        related_ips=tuple(i for i in (src,) if i),
        related_users=tuple(u for u in (user,) if u),
        related_hosts=tuple(h for h in (host,) if h),
        raw={"EventID": 4624},
    )
