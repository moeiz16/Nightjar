# ruff: noqa: S311  (seeded pseudo-random numbers make the demo data reproducible; nothing secret)
"""Seed phases 2 and 3: last night's incidents NJ-1036…NJ-1041 and the human decisions made on them.

Phase 2 runs the bundled `halden-night` replay through builder B's real pipeline in-process
(`nightjar.workers.inline.run_scenario`) when it is available; otherwise it inserts fixture incidents with the same
shape through the platform functions the pipeline calls. Phase 3 then applies the people's decisions at their
back-dated times (approvals, executions, determinations, notices), whichever way phase 2 ran.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from nightjar import clock
from nightjar.approvals import api as approvals
from nightjar.approvals.service import consume_token, decide, request
from nightjar.contracts import (
    ActionResult,
    AgentStep,
    Anomaly,
    AttackRef,
    CorrelationUpdate,
    Detection,
    Entities,
    EventSnapshot,
    Reason,
    ReportableFacts,
    TriageScore,
    TriageVerdict,
)
from nightjar.core.permissions import permissions_for
from nightjar.db.models.platform import User
from nightjar.db.models.soc import ActionProposal, Incident, Notice
from nightjar.db.session import session_scope
from nightjar.directory import api as directory
from nightjar.incidents import api as incidents
from nightjar.incidents.service import add_timeline, announce_updated, apply_status
from nightjar.investigations import api as investigations
from nightjar.regimes import service as clock_svc
from nightjar.ticketing.api import create_ticket

log = structlog.get_logger(__name__)
BRUSSELS = ZoneInfo("Europe/Brussels")

TECH = {
    "T1190": ("Exploit Public-Facing Application", "initial-access"),
    "T1078.002": ("Valid Accounts: Domain Accounts", "defense-evasion"),
    "T1218.011": ("System Binary Proxy Execution: Rundll32", "defense-evasion"),
    "T1071.001": ("Application Layer Protocol: Web Protocols", "command-and-control"),
    "T1003.001": ("OS Credential Dumping: LSASS Memory", "credential-access"),
    "T1087.002": ("Account Discovery: Domain Account", "discovery"),
    "T1482": ("Domain Trust Discovery", "discovery"),
    "T1021.002": ("Remote Services: SMB/Windows Admin Shares", "lateral-movement"),
    "T1569.002": ("System Services: Service Execution", "execution"),
    "T1053.005": ("Scheduled Task/Job: Scheduled Task", "persistence"),
    "T1490": ("Inhibit System Recovery", "impact"),
    "T1566.002": ("Phishing: Spearphishing Link", "initial-access"),
    "T1078.004": ("Valid Accounts: Cloud Accounts", "initial-access"),
    "T1621": ("Multi-Factor Authentication Request Generation", "credential-access"),
    "T1114.003": ("Email Collection: Email Forwarding Rule", "collection"),
    "T1114.002": ("Email Collection: Remote Email Collection", "collection"),
    "T1552.001": ("Unsecured Credentials: Credentials In Files", "credential-access"),
    "T1530": ("Data from Cloud Storage", "collection"),
    "T1136.003": ("Create Account: Cloud Account", "persistence"),
    "T1110.003": ("Brute Force: Password Spraying", "credential-access"),
    "T1567.002": ("Exfiltration Over Web Service: Exfiltration to Cloud Storage", "exfiltration"),
    "T1047": ("Windows Management Instrumentation", "execution"),
    "T1059.001": ("Command and Scripting Interpreter: PowerShell", "execution"),
}
PHISH_MSG = "<a7c41e09.halden-invoice@mail.example>"


def night_start(now: datetime | None = None) -> datetime:
    """The most recent 00:00 Europe/Brussels such that now ≥ night + 09:00 (Part 6)."""
    local = (now or clock.now()).astimezone(BRUSSELS)
    night = datetime.combine(local.date(), datetime.min.time(), tzinfo=BRUSSELS)
    if local < night + timedelta(hours=9):
        night -= timedelta(days=1)
    return night


def at(night: datetime, day: int, hh: int, mm: int, tz: ZoneInfo = BRUSSELS) -> datetime:
    """Wall-clock time `hh:mm` in `tz` on night + `day` days (Brussels calendar)."""
    d = (night + timedelta(days=day)).date()
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)


@dataclass
class Fixture:
    reference: str
    key: str
    kind: str
    title: str
    severity: str
    start: tuple[int, int, int]
    end: tuple[int, int, int]
    entities: Entities
    techniques: list[str]
    detections: list[tuple[str, str]]  # (title, severity)
    verdict: str
    confidence: float
    probability: float
    summary: str
    facts: ReportableFacts | None
    proposals: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, target, rationale)
    noise: bool = False
    anomaly: tuple[str, float] | None = None


FIXTURES = [
    Fixture(
        "NJ-1036",
        "corr:halden-night-ransomware",
        "ransomware_precursor",
        "Ransomware precursor on the shared dispatch platform",
        "critical",
        (-1, 4, 10),
        (-1, 4, 53),
        Entities(
            hosts=("mem-citrix-01", "mem-dispatch-01", "ant-wms-01", "mem-sql-01"),
            users=("svc-dispatch",),
            ips=("203.0.113.66",),
        ),
        [
            "T1190",
            "T1078.002",
            "T1218.011",
            "T1071.001",
            "T1003.001",
            "T1087.002",
            "T1482",
            "T1021.002",
            "T1569.002",
            "T1053.005",
            "T1490",
        ],
        [
            ("Remote-access gateway session reused from a new address", "high"),
            ("Service account signed in to the dispatch server interactively", "high"),
            ("Signed Windows program used to run downloaded code", "high"),
            ("Repeated connections to a known bad address", "high"),
            ("Sign-in secrets read from memory with a built-in DLL", "critical"),
            ("Directory and domain trust look-ups from a server", "medium"),
            ("Remote program launch to two servers", "high"),
            ("New scheduled task on the dispatch server", "medium"),
            ("Attempt to delete backup copies blocked", "critical"),
        ],
        "true_positive",
        0.96,
        0.97,
        "A known-exploited flaw in the Memphis remote-access gateway let an attacker reuse a session, sign in to the "
        "dispatch platform as its service account, read sign-in secrets and move to the Antwerp warehouse and dispatch "
        "database servers. The attempt to delete backup copies matches ransomware preparation.",
        ReportableFacts(
            True,
            ("EU", "US", "AU"),
            ("Halden Logistics NV", "Halden Logistics, Inc.", "Halden Logistics Pty Ltd"),
            "outage",
            9.0,
            "relevant",
            False,
            None,
            True,
            1_200_000.0,
        ),
        [
            (
                "isolate_host",
                "mem-dispatch-01",
                "Stop the attacker using the dispatch platform to reach other servers.",
            ),
            ("isolate_host", "ant-wms-01", "The attacker reached the warehouse server; cut it off before encryption."),
            ("disable_account", "svc-dispatch", "The service account's password was read from memory."),
            ("block_ip", "203.0.113.66", "The beacon talks to this address."),
        ],
    ),
    Fixture(
        "NJ-1037",
        "corr:halden-night-phish",
        "credential_misuse",
        "Phishing led to misuse of Joy Okafor's account",
        "high",
        (0, 3, 5),
        (0, 3, 40),
        Entities(users=("j.okafor",), hosts=("ant-ws-014",), ips=("198.51.100.77",), emails=(PHISH_MSG,)),
        ["T1566.002", "T1078.004", "T1621", "T1114.003", "T1114.002"],
        [
            ("Link to a look-alike sign-in page delivered", "medium"),
            ("Sign-in from a new country on a hosting network", "high"),
            ("Five refused sign-in prompts, then one approved", "high"),
            ("Mailbox rule forwarding email outside the company", "high"),
            ("140 mailbox items read in ten minutes", "medium"),
        ],
        "true_positive",
        0.94,
        0.95,
        "Joy Okafor approved a sign-in prompt after refusing five; the sign-in came from a hosting network in "
        "Romania minutes after a phishing link arrived. A forwarding rule was added and 140 items were read.",
        ReportableFacts(True, ("EU",), ("Halden Logistics NV",), "none", None, "none", True, 140, False, None),
        [
            ("revoke_sessions", "j.okafor", "End the attacker's session."),
            ("disable_account", "j.okafor", "Stop new sign-ins until the password is reset."),
            ("reset_password", "j.okafor", "The password was captured by the phishing page."),
        ],
    ),
    Fixture(
        "NJ-1038",
        "corr:halden-night-cloudkey",
        "cloud_credential_exposure",
        "Cloud access key used from an unfamiliar network",
        "high",
        (0, 1, 40),
        (0, 2, 5),
        Entities(
            users=("ci-deploy",),
            access_keys=("AKIAEXAMPLEHALDEN01",),
            cloud_accounts=("aws-prod",),
            ips=("198.51.100.23",),
        ),
        ["T1552.001", "T1078.004", "T1530", "T1136.003"],
        [
            ("Deployment key exposed in a public code repository", "high"),
            ("Deployment key used from an unfamiliar network", "high"),
            ("Bucket listing and 38 invoice files read", "high"),
            ("Attempt to create a new cloud user denied", "medium"),
        ],
        "true_positive",
        0.92,
        0.93,
        "The CI deployment key appeared in a public repository alert and was used 20 minutes later from an unfamiliar "
        "network to list buckets and read 38 invoice files; creating a new user was refused.",
        ReportableFacts(True, ("US",), ("Halden Logistics, Inc.",), "none", None, "none", False, None, False, None),
        [("revoke_access_key", "AKIAEXAMPLEHALDEN01", "The key is in the hands of someone outside the company.")],
    ),
    Fixture(
        "NJ-1039",
        "corr:halden-night-backup",
        "benign_activity",
        "Backup software update caused 146 alerts",
        "low",
        (0, 1, 0),
        (0, 1, 25),
        Entities(
            users=("svc-backup",),
            hosts=(
                "ant-backup-01",
                "ant-fs-01",
                "ant-dc-01",
                "ant-wms-01",
                "mem-sql-01",
                "mem-dispatch-01",
                "ant-ws-014",
                "ant-ws-022",
                "mem-ws-031",
                "mem-ws-040",
                "mel-ws-007",
                "mel-depot-gw-01",
            ),
        ),
        ["T1047", "T1059.001", "T1490"],
        [
            ("Shadow-copy listing by the backup service", "low"),
            ("Encoded PowerShell from the backup agent", "low"),
            ("Remote WMI from the backup server", "low"),
        ],
        "benign",
        0.97,
        0.04,
        "All 146 alerts came from the backup agent's scheduled update rolling out from ant-backup-01 under the backup "
        "service account; the same pattern ran in previous update windows.",
        ReportableFacts(False, ("EU", "US", "AU"), (), "none", None, "none", False, None, False, None),
        [],
        noise=True,
    ),
    Fixture(
        "NJ-1040",
        "corr:halden-night-spray",
        "brute_force",
        "Password guessing against the Melbourne remote-access gateway",
        "medium",
        (0, 2, 20),
        (0, 2, 50),
        Entities(
            hosts=("mel-vpn-01",),
            ips=("203.0.113.50", "203.0.113.51", "203.0.113.52"),
            users=("n.kelly", "b.singh", "h.morrison", "w.chen"),
        ),
        ["T1110.003"],
        [("412 failed sign-ins for 63 accounts from three addresses", "medium")],
        "true_positive",
        0.9,
        0.88,
        "Three addresses tried common passwords against 63 accounts on the Melbourne gateway at lunchtime local time; "
        "none succeeded.",
        ReportableFacts(True, ("AU",), ("Halden Logistics Pty Ltd",), "none", None, "none", False, None, False, None),
        [
            ("block_ip", "203.0.113.50", "Source of the password guessing."),
            ("block_ip", "203.0.113.51", "Source of the password guessing."),
            ("block_ip", "203.0.113.52", "Source of the password guessing."),
        ],
    ),
    Fixture(
        "NJ-1041",
        "corr:halden-night-exfil",
        "data_exfiltration",
        "Unusual data transfer by a contractor account",
        "high",
        (0, 2, 40),
        (0, 3, 30),
        Entities(users=("t.reyes",), hosts=("mem-ws-040", "ant-fs-01")),
        ["T1567.002"],
        [("File-sync tool sent 3.2 GB to personal cloud storage", "high")],
        "needs_review",
        0.58,
        0.61,
        "The contractor account opened four file shares it had never used and sent 3.2 GB to personal storage at "
        "19:40 Memphis time. The pattern is unusual, but the account had a data-migration task open; a person "
        "should confirm with the manager.",
        ReportableFacts(False, ("US",), ("Halden Logistics, Inc.",), "none", None, "none", True, None, False, None),
        [],
        anomaly=("t.reyes", 0.97),
    ),
]


def _attack(ids: list[str]) -> tuple[AttackRef, ...]:
    return tuple(AttackRef(t, TECH[t][0], (TECH[t][1],)) for t in ids)


def _uid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128))


async def fixture_night(org_id: uuid.UUID, night: datetime) -> list[Any]:
    """Phase 2 without the pipeline: same incidents, detections, agent runs and proposals the replay produces."""
    from nightjar.hunting.api import append_events
    from nightjar.seed.events import events_for_detection

    rng = random.Random(1041)
    refs = []
    for fx in FIXTURES:
        start, end = at(night, *fx.start), at(night, *fx.end)
        span = (end - start) / max(1, (146 if fx.noise else len(fx.detections)))
        dets, events = [], []
        titles = fx.detections * (146 // len(fx.detections) + 1) if fx.noise else fx.detections
        for i, (title, sev) in enumerate(titles[: 146 if fx.noise else len(fx.detections)]):
            t = start + span * i
            did = _uid(rng)
            evs = events_for_detection(org_id, fx, title, t, rng)
            events.extend(evs)
            p = (
                round(rng.uniform(0.02, 0.09), 3)
                if fx.noise
                else round(min(0.99, fx.probability + rng.uniform(-0.05, 0.03)), 3)
            )
            dets.append(
                Detection(
                    id=did,
                    org_id=org_id,
                    source="ueba" if fx.anomaly and i == 0 else "sigma",
                    rule_id=f"nightjar-{fx.kind}-{i % len(fx.detections)}",
                    rule_title=title,
                    severity=sev,  # type: ignore[arg-type]
                    confidence=0.8,
                    attack=_attack(fx.techniques[i % len(fx.techniques) :][:2] or fx.techniques[:1]),
                    entities=fx.entities,
                    first_seen=t,
                    last_seen=t + span,
                    event_ids=tuple(e.event_id for e in evs),
                    evidence=tuple(
                        EventSnapshot(
                            e.event_id,
                            e.timestamp,
                            e.dataset,
                            e.action,
                            e.message,
                            e.host.name if e.host else None,
                            e.user.name if e.user else None,
                            {},
                        )
                        for e in evs[:20]
                    ),
                    summary=title + ".",
                    fields={"messageId": PHISH_MSG} if fx.reference == "NJ-1037" and i == 0 else {},
                    triage=TriageScore(
                        did,
                        p,
                        "likely_noise" if fx.noise else "likely_real",
                        0.5,
                        (Reason("rule_fidelity", 0.8, 0.3, "This rule is rarely wrong at this company"),),
                        "triage-1.0",
                        fx.noise,
                    ),
                )
            )
        await append_events(events)
        with clock.travel(start):
            await incidents.record_detections(org_id, dets)
        if fx.anomaly:
            entity, score = fx.anomaly
            with clock.travel(end):
                await incidents.record_anomalies(
                    org_id,
                    [
                        Anomaly(
                            _uid(rng),
                            org_id,
                            "user",
                            entity,
                            start - timedelta(minutes=5),
                            end,
                            score,
                            0.71,
                            {"bytes_out": 11.2, "distinct_shares": 6.0, "off_hours": 4.1},
                            {"bytes_out": 3.2e9, "distinct_shares": 4.0, "off_hours": 1.0},
                            (
                                Reason("bytes_out", 3.2e9, 0.41, "Sent about 3.2 GB; usually under 50 MB a day", 4.6e7),
                                Reason("distinct_shares", 4, 0.22, "Opened four file shares never used before", 0),
                                Reason("off_hours", 1, 0.18, "Active at 19:40 Memphis time; usual hours 08–17", 0),
                            ),
                            "ueba-1.0",
                            dets[0].id,
                        )
                    ],
                )
        with clock.travel(start + timedelta(minutes=1)):  # the correlator opens the incident on the first alerts
            ref = await incidents.upsert_incident(
                CorrelationUpdate(
                    org_id=org_id,
                    correlation_key=fx.key,
                    detection_ids=tuple(d.id for d in dets),
                    kind=fx.kind,  # type: ignore[arg-type]
                    title=fx.title,
                    severity=fx.severity,  # type: ignore[arg-type]
                    attack=_attack(fx.techniques),
                    entities=fx.entities,
                    first_seen=start,
                    last_seen=end,
                    summary=fx.summary,
                    max_triage_probability=fx.probability,
                    all_likely_noise=fx.noise,
                )
            )
        refs.append(ref)
        verdict_at = at(night, -1, 4, 42) if fx.reference == "NJ-1036" else start + timedelta(minutes=3, seconds=24)
        await _agent_run(org_id, ref.id, fx, verdict_at, rng)
    return refs


async def _agent_run(
    org_id: uuid.UUID, incident_id: uuid.UUID, fx: Fixture, verdict_at: datetime, rng: random.Random
) -> None:
    started = verdict_at - timedelta(seconds=40)
    with clock.travel(started):
        run = await investigations.start_run(org_id, incident_id, trigger="auto", requested_by=None)
    steps = [
        ("triage", "plan", "Planned the checks: sign-in history, computer details, known threats", None),
        (
            "triage",
            "tool_call",
            "Checked the people involved against their usual sign-in countries",
            "identity.sign_in_profile",
        ),
        ("triage", "tool_call", "Looked up the affected computers in the asset inventory", "assets.get_asset"),
        ("triage", "handoff_out", "Handed the addresses and techniques to the enrichment colleague", None),
        ("enrichment", "handoff_in", "Picked up the enrichment request", None),
        ("enrichment", "tool_call", "Checked the addresses against known bad addresses", "intel.lookup_indicator"),
        (
            "enrichment",
            "tool_call",
            "Checked known exploited vulnerabilities on the affected computers",
            "intel.lookup_kev",
        ),
        ("enrichment", "finding", "Reported what is known about the addresses and techniques", None),
        ("triage", "verdict", "Weighed rule fidelity, the triage score and the enrichment findings", None),
    ]
    if fx.proposals:
        steps += [
            ("triage", "handoff_out", "Asked the response colleague for a containment plan", None),
            ("response", "handoff_in", "Picked up the containment request", None),
            ("response", "proposal", "Proposed containment steps for people to approve", "response.propose_action"),
        ]
    for i, (agent, kind, title, tool) in enumerate(steps):
        with clock.travel(started + timedelta(seconds=3 * i)):
            await investigations.append_step(
                run.run_id,
                AgentStep(
                    agent=agent,  # type: ignore[arg-type]
                    kind=kind,  # type: ignore[arg-type]
                    title=title,
                    started_at=clock.now(),
                    duration_ms=rng.randint(150, 2400),
                    tool=tool,
                    a2a_task_id=f"task-{rng.getrandbits(40):x}" if kind.startswith("handoff") else None,
                    model="offline-fake" if kind == "verdict" else None,
                    input_hash=f"{rng.getrandbits(256):064x}" if tool or kind == "verdict" else None,
                    detail={"incident": fx.reference},
                ),
            )
    with clock.travel(verdict_at):
        refs = []
        for kind, target, rationale in fx.proposals:
            refs.append(
                await approvals.request_approval(org_id, incident_id, run.run_id, _action(kind, target, rationale))
            )
        await investigations.finish_run(
            run.run_id,
            status="completed",
            verdict=TriageVerdict(fx.verdict, fx.confidence, fx.summary, (), fx.probability, fx.facts),  # type: ignore[arg-type]
            enrichment=None,
            proposals=tuple(_action(k, t, r) for k, t, r in fx.proposals),
        )
        if fx.reference in ("NJ-1036", "NJ-1037", "NJ-1038"):
            await create_ticket(
                org_id,
                incident_id,
                title=f"{fx.reference}: {fx.title}",
                body=fx.summary,
                priority="p1" if fx.severity == "critical" else "p2",
                assignee_team="Service desk" if fx.reference != "NJ-1038" else "Cloud platform",
            )


def _action(kind: str, target: str, rationale: str) -> Any:
    from nightjar.approvals.service import TARGET_TYPES
    from nightjar.contracts import ProposedAction

    effects = {
        "isolate_host": "The computer can only talk to the security tools until released.",
        "disable_account": "Nobody can sign in with this account until it is turned back on.",
        "revoke_sessions": "Every open session for this person ends now.",
        "reset_password": "The person must choose a new password at next sign-in.",
        "revoke_access_key": "The key stops working everywhere immediately.",
        "block_ip": "Traffic to and from this address is dropped at the edge.",
        "release_host": "The computer reconnects to the network.",
    }
    return ProposedAction(
        kind,  # type: ignore[arg-type]
        target,
        TARGET_TYPES.get(kind, "ticket"),  # type: ignore[arg-type]
        rationale,
        effects.get(kind, ""),
        kind != "reset_password",
    )


# ── Phase 3: human decisions ─────────────────────────────────────────────────
@dataclass
class People:
    by_email: dict[str, User]

    def __getitem__(self, first: str) -> User:
        return next(u for e, u in self.by_email.items() if e.startswith(first))


async def _incident(org_id: uuid.UUID, reference: str) -> Incident | None:
    async with session_scope() as db:
        return (
            await db.execute(select(Incident).where(Incident.org_id == org_id, Incident.reference == reference))
        ).scalar_one_or_none()


async def _approve_and_execute(
    org_id: uuid.UUID,
    inc: Incident,
    kind: str,
    target: str,
    *,
    people: People,
    approvers: list[tuple[str, datetime]],
    executed_at: datetime,
    proposer: str | None = None,
    rationale: str = "",
    effector: Any = None,
) -> None:
    async with session_scope() as db:
        p = (
            (
                await db.execute(
                    select(ActionProposal).where(
                        ActionProposal.incident_id == inc.id,
                        ActionProposal.kind == kind,
                        ActionProposal.target == target,
                    )
                )
            )
            .scalars()
            .first()
        )
        if p is None:
            who = people[proposer or "lukas"]
            first_time = approvers[0][1] - timedelta(minutes=4)
            with clock.travel(first_time):
                p, _ = await request(
                    db,
                    await db.get(Incident, inc.id),  # type: ignore[arg-type]
                    kind=kind,
                    target=target,
                    target_type=None,
                    rationale=rationale or "Containment agreed on the bridge call.",
                    proposed_by=await db.merge(who),
                )
        if p.status not in ("pending",):
            return
        for name, when in approvers:
            user = people[name]
            role = "executive" if name == "ingrid" else "responder"
            with clock.travel(when):
                await decide(
                    db, p, user=await db.merge(user), permissions=set(permissions_for(role)), approve=True, note=None
                )
        if p.status != "approved":
            return
        await consume_token(db, org_id, p.id, p.token_nonce or "")
        pid = p.id
    if effector is not None:
        await effector(pid)
    with clock.travel(executed_at):
        await approvals.record_result(
            org_id,
            ActionResult(
                pid,
                kind,  # type: ignore[arg-type]
                target,
                True,
                "executed",
                _done_text(kind, target),
                executed_at,
                f"response.{kind}",
            ),
        )


def _done_text(kind: str, target: str) -> str:
    return {
        "isolate_host": f"{target} isolated; only the security tools can reach it.",
        "release_host": f"{target} reconnected to the network.",
        "disable_account": f"Account {target} turned off.",
        "revoke_sessions": f"All sessions for {target} ended.",
        "reset_password": f"{target} must choose a new password at next sign-in.",
        "block_ip": f"{target} blocked at the edge firewalls.",
        "revoke_access_key": f"Access key {target} withdrawn.",
    }.get(kind, "Done.")


async def _status(
    org_id: uuid.UUID,
    inc: Incident,
    status: str,
    when: datetime,
    who: User,
    *,
    resolution: str | None = None,
    note: str | None = None,
    assignee: User | None = None,
) -> None:
    async with session_scope() as db:
        row = await db.get(Incident, inc.id)
        assert row is not None
        with clock.travel(when):
            changed = []
            if assignee is not None:
                row.assignee_id = assignee.id
                changed.append("assignee")
                await add_timeline(
                    db,
                    org_id,
                    row.id,
                    kind="status",
                    actor_type="user",
                    actor_name=who.full_name,
                    title=f"Assigned to {assignee.full_name}",
                    body=note,
                )
            if status != row.status:
                old = row.status
                apply_status(row, status)
                if resolution:
                    row.resolution = {"reason": resolution, "note": note, "by": who.full_name, "at": when.isoformat()}
                changed.append("status")
                await add_timeline(
                    db,
                    org_id,
                    row.id,
                    kind="status",
                    actor_type="user",
                    actor_name=who.full_name,
                    title=f"Status changed from {old} to {status}",
                    body=note,
                    executive=status in ("resolved", "monitoring"),
                )
            row.version += 1
            await announce_updated(db, row, changed or ["status"])


async def _assess(org_id: uuid.UUID, inc: Incident, regime: str, body: dict, user: User, when: datetime) -> None:
    async with session_scope() as db:
        row = await db.get(Incident, inc.id)
        with clock.travel(when):
            await clock_svc.assess(db, row, regime, body, user=await db.merge(user), ip=None)  # type: ignore[arg-type]


async def _notice(
    org_id: uuid.UUID,
    inc: Incident,
    regime: str,
    ntype: str,
    *,
    drafter: User,
    drafted: datetime,
    fills: dict[str, str],
    approver: User | None = None,
    approved: datetime | None = None,
    submitted: datetime | None = None,
    channel: str = "portal",
) -> None:
    async with session_scope() as db:
        row = await db.get(Incident, inc.id)
        assert row is not None
        with clock.travel(drafted):
            n = await clock_svc.draft_notice(
                db, row, regime=regime, notice_type=ntype, user=await db.merge(drafter), ip=None
            )
        if fills:
            with clock.travel(drafted + timedelta(minutes=20)):
                await clock_svc.edit_notice(
                    db,
                    n,
                    sections=[{"key": k, "body": v} for k, v in fills.items()],
                    version=n.version,
                    user=await db.merge(drafter),
                )
        if approver and approved:
            with clock.travel(approved):
                await clock_svc.approve_notice(db, n, user=await db.merge(approver), ip=None)
        if approver and submitted:
            with clock.travel(submitted):
                await clock_svc.submit_notice(db, n, channel=channel, note=None, user=await db.merge(approver), ip=None)


async def human_decisions(org_id: uuid.UUID, night: datetime, people: People) -> None:
    marcus, ingrid, lukas, aiko = people["marcus"], people["ingrid"], people["lukas"], people["aiko"]
    n36 = await _incident(org_id, "NJ-1036")
    if n36:

        async def isolate(host: str, on: bool):
            async def run(pid: uuid.UUID) -> None:
                await directory.set_host_isolation(org_id, host, isolated=on, approval_id=pid)

            return run

        two = [("marcus", at(night, -1, 4, 46)), ("ingrid", at(night, -1, 4, 51))]
        for host in ("mem-dispatch-01", "ant-wms-01"):
            await _approve_and_execute(
                org_id,
                n36,
                "isolate_host",
                host,
                people=people,
                approvers=two,
                executed_at=at(night, -1, 4, 52),
                effector=await isolate(host, True),
            )

        async def disable_svc(pid: uuid.UUID) -> None:
            await directory.set_account_disabled(org_id, "svc-dispatch", disabled=True, approval_id=pid)

        async def block_beacon(pid: uuid.UUID) -> None:
            await directory.set_ip_block(org_id, "203.0.113.66", blocked=True, approval_id=pid)

        await _approve_and_execute(
            org_id,
            n36,
            "disable_account",
            "svc-dispatch",
            people=people,
            approvers=two,
            executed_at=at(night, -1, 4, 52),
            effector=disable_svc,
        )
        await _approve_and_execute(
            org_id,
            n36,
            "block_ip",
            "203.0.113.66",
            people=people,
            approvers=[("marcus", at(night, -1, 4, 47))],
            executed_at=at(night, -1, 4, 48),
            effector=block_beacon,
        )
        release = [("marcus", at(night, -1, 13, 48)), ("ingrid", at(night, -1, 13, 50))]
        for host in ("mem-dispatch-01", "ant-wms-01"):
            await _approve_and_execute(
                org_id,
                n36,
                "release_host",
                host,
                people=people,
                approvers=release,
                executed_at=at(night, -1, 13, 52),
                proposer="lukas",
                rationale="Servers rebuilt from clean images and checked; dispatch can return from manual working.",
                effector=await isolate(host, False),
            )
        async with session_scope() as db:  # the outage and its reach are known after recovery, not at triage
            row = await db.get(Incident, n36.id)
            if row is not None:
                row.facts = {
                    **(row.facts or {}),
                    "maliciousSuspected": True,
                    "affectedJurisdictions": ["EU", "US", "AU"],
                    "serviceDisruption": "outage",
                    "disruptionHours": 9.0,
                    "criticalAssetImpact": "relevant",
                    "crossBorder": True,
                }
                with clock.travel(at(night, -1, 9, 20)):
                    await clock_svc.propose_for_incident(db, row)
        aware = at(night, -1, 4, 52)
        rationale = (
            "Dispatch ran by hand for about nine hours across EU, US and AU depots; the attacker "
            "reached the Antwerp warehouse server."
        )
        await _assess(
            org_id,
            n36,
            "nis2",
            {"applies": True, "rationale": rationale, "entityId": "halden-eu", "awareAt": aware},
            ingrid,
            at(night, -1, 9, 30),
        )
        await _assess(
            org_id,
            n36,
            "soci",
            {
                "applies": True,
                "rationale": "Linehaul East scheduling ran on manual "
                "working; a relevant impact on the availability of the asset.",
                "entityId": "halden-au",
                "impactLevel": "relevant",
                "awareAt": aware,
            },
            ingrid,
            at(night, -1, 9, 40),
        )
        ny = ZoneInfo("America/New_York")
        await _assess(
            org_id,
            n36,
            "sec_8k",
            {
                "applies": True,
                "rationale": "The disclosure committee determined the "
                "incident is material given the nine-hour dispatch outage and recovery costs.",
                "entityId": "halden-us",
                "materialityDeterminedAt": at(night, -1, 16, 0, ny),
            },
            ingrid,
            at(night, -1, 16, 5, ny),
        )
        contact = "Ingrid Vos, CISO and NIS2 contact point, ingrid.vos@halden.example, +32 3 555 01 20"
        await _notice(
            org_id,
            n36,
            "nis2",
            "early_warning",
            drafter=lukas,
            drafted=at(night, -1, 10, 15),
            fills={"contact": contact},
            approver=ingrid,
            approved=at(night, -1, 15, 5),
            submitted=at(night, -1, 15, 20),
        )
        await _notice(
            org_id,
            n36,
            "nis2",
            "incident_notification",
            drafter=lukas,
            drafted=at(night, -1, 17, 30),
            fills={
                "indicators": "- 203.0.113.66 (command and control)\n- Gateway session reuse on the "
                "Memphis remote-access gateway (CVE-2023-4966)",
                "contact": contact,
            },
        )
        await _notice(
            org_id,
            n36,
            "soci",
            "other_report",
            drafter=aiko,
            drafted=at(night, -1, 18, 10),
            fills={"contact": "Nathan Kelly, depot manager, nathan.kelly@halden.example, +61 3 5550 1180"},
        )
        await _status(
            org_id,
            n36,
            "monitoring",
            at(night, -1, 14, 30),
            marcus,
            note="Servers rebuilt and reconnected; watching for any return of the attacker.",
        )
    n37 = await _incident(org_id, "NJ-1037")
    if n37:
        for kind, minute in (("revoke_sessions", 41), ("disable_account", 42), ("reset_password", 44)):

            async def eff(pid: uuid.UUID, kind: str = kind) -> None:
                if kind == "revoke_sessions":
                    await directory.revoke_identity_sessions(org_id, "j.okafor", approval_id=pid)
                elif kind == "disable_account":
                    await directory.set_account_disabled(org_id, "j.okafor", disabled=True, approval_id=pid)
                else:
                    await directory.reset_identity_password(org_id, "j.okafor", approval_id=pid)

            await _approve_and_execute(
                org_id,
                n37,
                kind,
                "j.okafor",
                people=people,
                approvers=[("marcus", at(night, 0, 3, minute))],
                executed_at=at(
                    night,
                    0,
                    3,
                    minute,
                )
                + timedelta(seconds=40),
                effector=eff,
            )
        # Joy's mailbox was redirecting supplier invoice payments: Ingrid treats it as a significant incident under
        # NIS2 (possible considerable financial loss to others), aware from the first sign-in prompt refusal.
        await _assess(
            org_id,
            n37,
            "nis2",
            {
                "applies": True,
                "rationale": "Supplier invoices were being forwarded from the accounts payable mailbox to an outside "
                "address; customers and suppliers could suffer a considerable financial loss.",
                "entityId": "halden-eu",
                "awareAt": at(night, 0, 3, 13) + timedelta(seconds=40),
            },
            ingrid,
            at(night, 0, 3, 50),
        )
        await _notice(
            org_id,
            n37,
            "nis2",
            "early_warning",
            drafter=aiko,
            drafted=at(night, 0, 3, 58),
            # whether suppliers abroad were among the redirected invoices is not known yet: left for the CISO
            fills={
                "contact": "Ingrid Vos, CISO and NIS2 contact point, ingrid.vos@halden.example, +32 3 555 01 20",
                "cross_border": "[to be confirmed]",
            },
        )
        await _status(
            org_id,
            n37,
            "monitoring",
            at(night, 0, 4, 5),
            marcus,
            note="Account contained; forwarding rule removed by the service desk.",
        )
    n38 = await _incident(org_id, "NJ-1038")
    if n38:
        await _status(
            org_id,
            n38,
            "containing",
            at(night, 0, 2, 20),
            marcus,
            note="Waiting for approval to withdraw the deployment key.",
        )
    n40 = await _incident(org_id, "NJ-1040")
    if n40:
        for i, ip in enumerate(("203.0.113.50", "203.0.113.51", "203.0.113.52")):

            async def blk(pid: uuid.UUID, ip: str = ip) -> None:
                await directory.set_ip_block(org_id, ip, blocked=True, approval_id=pid)

            await _approve_and_execute(
                org_id,
                n40,
                "block_ip",
                ip,
                people=people,
                approvers=[("marcus", at(night, 0, 2, 54 + i))],
                executed_at=at(night, 0, 2, 55 + i),
                effector=blk,
            )
        await _status(
            org_id,
            n40,
            "resolved",
            at(night, 0, 3, 30),
            marcus,
            resolution="contained",
            note="All three addresses blocked; no sign-in succeeded.",
        )
    n41 = await _incident(org_id, "NJ-1041")
    if n41:
        await _status(
            org_id,
            n41,
            "investigating",
            at(night, 0, 8, 10),
            lukas,
            assignee=aiko,
            note="Aiko to confirm the data-migration task with Dana Harper.",
        )


async def human_decisions_safe(org_id: uuid.UUID, night: datetime, people: People) -> None:
    try:
        await human_decisions(org_id, night, people)
    except Exception:
        log.exception("seed_decisions_failed")
        raise


async def notices_count(org_id: uuid.UUID) -> int:
    async with session_scope() as db:
        return len(list((await db.execute(select(Notice.id).where(Notice.org_id == org_id))).scalars()))
