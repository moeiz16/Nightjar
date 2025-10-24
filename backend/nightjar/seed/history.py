# ruff: noqa: S311  (seeded pseudo-random numbers make the demo data reproducible; nothing secret)
"""Seed phase 1b: 35 closed historical incidents over the previous 45 days (NJ-1001…NJ-1035), created through the
same platform functions the pipeline uses, at their back-dated times. Triage gets faster over the weeks (the
executive trend), alerts outnumber incidents (alert reduction), and every response was approved by a person."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from nightjar import clock
from nightjar.approvals import api as approvals
from nightjar.approvals.service import consume_token, decide, request
from nightjar.contracts import (
    ActionResult,
    AgentStep,
    AttackRef,
    CorrelationUpdate,
    Detection,
    Entities,
    EventSnapshot,
    TriageScore,
    TriageVerdict,
    UntrustedText,
)
from nightjar.core.permissions import permissions_for
from nightjar.db.models.platform import User
from nightjar.db.models.soc import Incident
from nightjar.db.session import session_scope
from nightjar.incidents import api as incidents
from nightjar.incidents.service import add_timeline, announce_updated
from nightjar.investigations import api as investigations

TECH = {
    "T1566.002": ("Phishing: Spearphishing Link", "initial-access"),
    "T1566.001": ("Phishing: Spearphishing Attachment", "initial-access"),
    "T1078.004": ("Valid Accounts: Cloud Accounts", "initial-access"),
    "T1110.003": ("Brute Force: Password Spraying", "credential-access"),
    "T1204.002": ("User Execution: Malicious File", "execution"),
    "T1059.001": ("Command and Scripting Interpreter: PowerShell", "execution"),
    "T1567.002": ("Exfiltration Over Web Service: Exfiltration to Cloud Storage", "exfiltration"),
    "T1530": ("Data from Cloud Storage", "collection"),
    "T1047": ("Windows Management Instrumentation", "execution"),
}

# kind, count, title, severity, techniques, verdict
KINDS = [
    ("phishing", 8, "Phishing email with a fake invoice link sent to {who}", "medium", ["T1566.002"], "true_positive"),
    ("credential_misuse", 5, "Sign-in from an unusual country for {who}", "high", ["T1078.004"], "true_positive"),
    ("brute_force", 5, "Password guessing against {gw}", "medium", ["T1110.003"], "true_positive"),
    (
        "malware",
        3,
        "Malicious attachment opened on {ws}",
        "high",
        ["T1566.001", "T1204.002", "T1059.001"],
        "true_positive",
    ),
    (
        "benign_activity",
        8,
        "Software update triggered alerts on {n} computers",
        "low",
        ["T1047", "T1059.001"],
        "benign",
    ),
    ("other", 3, "Administration tool flagged on {ws}", "low", ["T1059.001"], "false_positive"),
    (
        "cloud_credential_exposure",
        2,
        "Cloud access key used from a new network",
        "high",
        ["T1078.004", "T1530"],
        "true_positive",
    ),
    ("data_exfiltration", 1, "Large upload to personal storage by {who}", "high", ["T1567.002"], "true_positive"),
]
PEOPLE = ["j.okafor", "k.wouters", "r.dubois", "d.harper", "o.fernandez", "w.chen", "b.singh", "g.lambert", "n.kelly"]
WORKSTATIONS = {"j.okafor": "ant-ws-014", "l.peeters": "ant-ws-022", "m.bell": "mem-ws-031", "a.tanaka": "mel-ws-007"}
GATEWAYS = ["mem-citrix-01", "ant-vpn-01", "mel-vpn-01"]
SERVERS = ["ant-fs-01", "ant-backup-01", "mem-sql-01", "ant-dc-01", "mem-dispatch-01", "ant-wms-01"]


def _attack(ids: list[str]) -> tuple[AttackRef, ...]:
    return tuple(AttackRef(t, TECH[t][0], (TECH[t][1],)) for t in ids)


def _detection(
    org_id: uuid.UUID,
    title: str,
    sev: str,
    ents: Entities,
    techs: list[str],
    at: datetime,
    *,
    noise: bool,
    rng: random.Random,
) -> Detection:
    did = uuid.UUID(int=rng.getrandbits(128))
    p = round(rng.uniform(0.02, 0.12), 3) if noise else round(rng.uniform(0.72, 0.97), 3)
    band = "likely_noise" if noise else "likely_real"
    return Detection(
        id=did,
        org_id=org_id,
        source="sigma",
        rule_id=f"hist-{techs[0]}",
        rule_title=title,
        severity=sev,  # type: ignore[arg-type]
        confidence=0.7,
        attack=_attack(techs),
        entities=ents,
        first_seen=at,
        last_seen=at + timedelta(minutes=1),
        event_ids=(uuid.UUID(int=rng.getrandbits(128)).hex,),
        evidence=(
            EventSnapshot(
                uuid.UUID(int=rng.getrandbits(128)).hex,
                at,
                "windows.security",
                "logon",
                UntrustedText("Sample event (history)", "message"),
            ),
        ),
        summary=f"{title}.",
        fields={},
        triage=TriageScore(did, p, band, 0.5, (), "triage-1.0", noise),  # type: ignore[arg-type]
    )


def _plan(rng: random.Random, today_start: datetime) -> list[tuple[datetime, tuple]]:
    """35 (first_seen, kind spec) pairs over the previous 45 days, in time order."""
    specs = [k for k in KINDS for _ in range(k[1])]
    rng.shuffle(specs)
    times = sorted(
        today_start - timedelta(days=rng.randint(2, 45), hours=rng.randint(-12, 10), minutes=rng.randint(0, 59))
        for _ in specs
    )
    return list(zip(times, specs, strict=True))


async def seed_history(org_id: uuid.UUID, people: dict[str, User], *, today_start: datetime, seed: int = 1036) -> int:
    rng = random.Random(seed)
    marcus = people["marcus.bell@halden.example"]
    ingrid = people["ingrid.vos@halden.example"]
    count = 0
    for at, (kind, _n, title_t, sev, techs, verdict) in _plan(rng, today_start):
        who = rng.choice(PEOPLE)
        ws = WORKSTATIONS.get(who) or rng.choice(list(WORKSTATIONS.values()))
        gw = rng.choice(GATEWAYS)
        n_noise = rng.randint(6, 18)
        title = title_t.format(who=who, ws=ws, gw=gw, n=n_noise)
        ents, action = Entities(), None
        if kind == "phishing":
            msg = f"<{rng.getrandbits(48):x}@mail.halden.example>"
            ents, action = Entities(users=(who,), hosts=(ws,), emails=(msg,)), ("quarantine_email", msg)
        elif kind == "credential_misuse":
            ip = f"198.51.100.{rng.randint(100, 199)}"
            ents, action = Entities(users=(who,), ips=(ip,)), ("revoke_sessions", who)
        elif kind == "brute_force":
            ips = tuple(f"203.0.113.{rng.randint(100, 199)}" for _ in range(2))
            ents, action = Entities(hosts=(gw,), ips=ips), ("block_ip", ips[0])
        elif kind == "malware":
            ents, action = Entities(users=(who,), hosts=(ws,)), ("isolate_host", ws)
        elif kind == "benign_activity":
            ents = Entities(users=("svc-backup",), hosts=tuple(rng.sample(SERVERS, 3)))
        elif kind == "other":
            ents = Entities(users=("e.janssens",), hosts=(ws,))
        elif kind == "cloud_credential_exposure":
            ents = Entities(
                users=("c.nguyen",),
                access_keys=("AKIAEXAMPLEHALDEN02",),
                cloud_accounts=("aws-prod",),
                ips=(f"198.51.100.{rng.randint(10, 60)}",),
            )
            action = ("revoke_access_key", "AKIAEXAMPLEHALDEN02")
        elif kind == "data_exfiltration":
            ents, action = Entities(users=(who,), hosts=(ws,)), ("disable_account", who)
        noise = verdict != "true_positive"
        n_det = n_noise if kind == "benign_activity" else rng.randint(2, 6)
        with clock.travel(at):
            dets = [
                _detection(org_id, title, sev, ents, techs, at + timedelta(minutes=i), noise=noise, rng=rng)
                for i in range(n_det)
            ]
            await incidents.record_detections(org_id, dets)
            ref = await incidents.upsert_incident(
                CorrelationUpdate(
                    org_id=org_id,
                    correlation_key=f"hist:{rng.getrandbits(64):x}",
                    detection_ids=tuple(d.id for d in dets),
                    kind=kind,
                    title=title,
                    severity=sev,
                    attack=_attack(techs),
                    entities=ents,
                    first_seen=at,
                    last_seen=at + timedelta(minutes=n_det + 3),
                    summary=f"{title}.",
                    max_triage_probability=max(d.triage.probability for d in dets if d.triage),
                    all_likely_noise=noise,
                )
            )
        # triage gets faster over the weeks (agents tuned, playbooks settled)
        age = (today_start - at).days
        tri = timedelta(
            minutes=rng.uniform(25, 45) if age > 21 else rng.uniform(10, 22) if age > 7 else rng.uniform(2, 6)
        )
        with clock.travel(at + tri - timedelta(minutes=1)):
            run = await investigations.start_run(org_id, ref.id, trigger="auto", requested_by=None)
            for i, (agent, step_kind, step_title) in enumerate(
                (
                    ("triage", "plan", "Planned the checks for this incident"),
                    ("triage", "handoff_out", "Asked the enrichment colleague to check addresses and techniques"),
                    ("enrichment", "tool_call", "Checked the addresses and techniques against known threats"),
                    ("triage", "verdict", "Weighed the evidence"),
                )
            ):
                await investigations.append_step(
                    run.run_id,
                    AgentStep(
                        agent=agent,  # type: ignore[arg-type]
                        kind=step_kind,  # type: ignore[arg-type]
                        title=step_title,
                        started_at=clock.now() + timedelta(seconds=i * 8),
                        duration_ms=rng.randint(200, 3000),
                        model="offline-fake" if step_kind == "verdict" else None,
                        tool="intel.lookup_indicator" if step_kind == "tool_call" else None,
                        input_hash=f"{rng.getrandbits(256):064x}" if step_kind in ("tool_call", "verdict") else None,
                    ),
                )
        with clock.travel(at + tri):
            await investigations.finish_run(
                run.run_id,
                status="completed",
                verdict=TriageVerdict(
                    verdict,
                    round(rng.uniform(0.82, 0.97), 2),
                    _verdict_text(kind, verdict),
                    (),
                    dets[0].triage.probability if dets[0].triage else None,
                    None,
                ),
                enrichment=None,
                proposals=(),
            )
        contained = at + tri + timedelta(minutes=rng.uniform(8, 50))
        if action is not None:
            async with session_scope() as db:
                inc = await db.get(Incident, ref.id)
                assert inc is not None
                with clock.travel(at + tri + timedelta(minutes=1)):
                    p, _ = await request(
                        db,
                        inc,
                        kind=action[0],
                        target=action[1],
                        target_type=None,
                        rationale="Contain the attack before it spreads.",
                        run_id=run.run_id,
                    )
                with clock.travel(contained - timedelta(minutes=2)):
                    await decide(
                        db,
                        p,
                        user=await db.merge(marcus),
                        permissions=set(permissions_for("responder")),
                        approve=True,
                        note=None,
                    )
                    if p.status == "pending":
                        await decide(
                            db,
                            p,
                            user=await db.merge(ingrid),
                            permissions=set(permissions_for("executive")),
                            approve=True,
                            note=None,
                        )
                    await consume_token(db, org_id, p.id, p.token_nonce or "")
                pid, pkind, ptarget = p.id, p.kind, p.target
            with clock.travel(contained):
                await approvals.record_result(
                    org_id,
                    ActionResult(
                        pid,
                        pkind,  # type: ignore[arg-type]
                        ptarget,
                        True,
                        "executed",
                        "Done (history).",
                        contained,
                        f"response.{pkind}",
                    ),
                )
        resolved = contained + timedelta(hours=rng.uniform(1, 6))
        async with session_scope() as db:
            inc = await db.get(Incident, ref.id)
            assert inc is not None
            if inc.status != "resolved":
                inc.resolved_at = resolved
                inc.resolution = {
                    "reason": "contained",
                    "note": "Contained and verified.",
                    "by": "Marcus Bell",
                    "at": resolved.isoformat(),
                }
                with clock.travel(resolved):
                    await add_timeline(
                        db,
                        org_id,
                        inc.id,
                        kind="status",
                        actor_type="user",
                        actor_name="Marcus Bell",
                        title="Resolved: contained",
                        at=resolved,
                        executive=True,
                    )
            inc.status, inc.stage = "closed", "contain" if action else inc.stage
            closed = (inc.resolved_at or resolved) + timedelta(hours=24)
            with clock.travel(closed):
                await add_timeline(
                    db, org_id, inc.id, kind="status", title="Closed 24 hours after resolution", at=closed
                )
                inc.version += 1
                await announce_updated(db, inc, ["status"])
        count += 1
    return count


def _verdict_text(kind: str, verdict: str) -> str:
    if verdict == "benign":
        return "The alerts match a scheduled software update run by the backup service account; nothing else changed."
    if verdict == "false_positive":
        return "An administrator used a remote support tool during a booked maintenance window."
    return {
        "phishing": "The link led to a look-alike sign-in page; the message reached one mailbox.",
        "credential_misuse": "The sign-in came from a hosting network in a country this person never uses.",
        "brute_force": "Many accounts were tried from two addresses; none of the guesses succeeded.",
        "malware": "The attachment started PowerShell that tried to download a second program.",
        "cloud_credential_exposure": "The key was used from a new network minutes after a public exposure alert.",
        "data_exfiltration": "Gigabytes left for a personal storage service outside working hours.",
    }.get(kind, "The evidence points to a real attack.")
