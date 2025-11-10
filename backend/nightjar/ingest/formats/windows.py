"""Windows Security, Sysmon and PowerShell events in the flat form used by OTRF datasets and Winlogbeat-style
exports (`EventID`, `Channel`, `Hostname`/`Computer`, event-data fields at the top level or under
`EventData`/`winlog.event_data`)."""

from __future__ import annotations

from typing import Any

from nightjar.contracts import Endpoint, HostRef, ProcessInfo, UserRef
from nightjar.ingest.common import (
    Draft,
    basename,
    clean_ip,
    first,
    is_loopback,
    parse_ts,
    short_host,
    split_user,
    to_int,
)

SECURITY_ACTIONS: dict[int, tuple[str, tuple[str, ...], tuple[str, ...], str]] = {
    # EventID: (action, category, type, outcome)
    4624: ("logon", ("authentication",), ("start",), "success"),
    4625: ("logon-failed", ("authentication",), ("start",), "failure"),
    4634: ("logoff", ("authentication",), ("end",), "success"),
    4647: ("logoff", ("authentication",), ("end",), "success"),
    4648: ("logon-explicit-credentials", ("authentication",), ("start",), "success"),
    4672: ("special-privileges-assigned", ("iam",), ("admin",), "success"),
    4688: ("process-started", ("process",), ("start",), "success"),
    4689: ("process-ended", ("process",), ("end",), "success"),
    4697: ("service-installed", ("configuration",), ("creation",), "success"),
    4698: ("scheduled-task-created", ("configuration",), ("creation",), "success"),
    4699: ("scheduled-task-deleted", ("configuration",), ("deletion",), "success"),
    4702: ("scheduled-task-updated", ("configuration",), ("change",), "success"),
    4720: ("user-account-created", ("iam",), ("user", "creation"), "success"),
    4728: ("user-added-to-group", ("iam",), ("group", "change"), "success"),
    4732: ("user-added-to-group", ("iam",), ("group", "change"), "success"),
    4740: ("account-locked", ("iam",), ("user", "change"), "failure"),
    4768: ("kerberos-tgt-requested", ("authentication",), ("start",), "success"),
    4769: ("kerberos-service-ticket-requested", ("authentication",), ("start",), "success"),
    4771: ("kerberos-preauth-failed", ("authentication",), ("start",), "failure"),
    4776: ("credential-validated", ("authentication",), ("start",), "success"),
    4662: ("directory-object-accessed", ("iam",), ("access",), "success"),
    4656: ("object-handle-requested", ("file",), ("access",), "success"),
    4658: ("object-handle-closed", ("file",), ("access",), "success"),
    4663: ("object-accessed", ("file",), ("access",), "success"),
    4661: ("object-handle-requested", ("iam",), ("access",), "success"),
    5140: ("share-accessed", ("file", "network"), ("access",), "success"),
    5145: ("share-object-checked", ("file", "network"), ("access",), "success"),
    5156: ("network-connection-allowed", ("network",), ("allowed",), "success"),
    5158: ("network-bind-allowed", ("network",), ("allowed",), "success"),
    1102: ("audit-log-cleared", ("configuration",), ("deletion",), "success"),
    7045: ("service-installed", ("configuration",), ("creation",), "success"),
}
SYSMON_ACTIONS: dict[int, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    1: ("process-started", ("process",), ("start",)),
    3: ("network-connection", ("network",), ("connection", "start")),
    5: ("process-ended", ("process",), ("end",)),
    7: ("image-loaded", ("process",), ("change",)),
    8: ("remote-thread-created", ("process",), ("change",)),
    10: ("process-accessed", ("process",), ("access",)),
    11: ("file-created", ("file",), ("creation",)),
    12: ("registry-object-changed", ("configuration",), ("change",)),
    13: ("registry-value-set", ("configuration",), ("change",)),
    15: ("file-stream-created", ("file",), ("creation",)),
    17: ("pipe-created", ("process",), ("creation",)),
    18: ("pipe-connected", ("process",), ("access",)),
    22: ("dns-query", ("network",), ("protocol", "info")),
    23: ("file-deleted", ("file",), ("deletion",)),
}


def event_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten Winlogbeat (`winlog.event_data`) and XML-style (`EventData`) shapes onto the top level."""
    data = dict(payload)
    for nested in (payload.get("EventData"), (payload.get("winlog") or {}).get("event_data")):
        if isinstance(nested, dict):
            for k, v in nested.items():
                data.setdefault(k, v)
    wl = payload.get("winlog")
    if isinstance(wl, dict):
        data.setdefault("EventID", wl.get("event_id"))
        data.setdefault("Channel", wl.get("channel"))
        data.setdefault("Computer", wl.get("computer_name"))
    return data


def channel_kind(payload: dict[str, Any]) -> str:
    ch = str(first(event_data(payload), "Channel", "channel", "SourceName") or "").lower()
    if "sysmon" in ch:
        return "sysmon"
    if "powershell" in ch:
        return "powershell"
    return "security"


def _sha256(hashes: Any) -> str | None:
    if not hashes:
        return None
    for part in str(hashes).split(","):
        if part.upper().startswith("SHA256="):
            return part.split("=", 1)[1].lower()
    return None


def parse(payload: dict[str, Any]) -> Draft:
    d = event_data(payload)
    eid = to_int(first(d, "EventID", "EventId", "event_id"))
    if eid is None:
        raise ValueError("missing EventID")
    ts = parse_ts(first(d, "@timestamp", "UtcTime", "TimeCreated", "TimeGenerated", "timestamp"))
    host_name = short_host(first(d, "Hostname", "Computer", "host"))
    host = HostRef(name=host_name) if host_name else None
    kind = channel_kind(payload)
    message = str(first(d, "Message", "message") or "")
    free: dict[str, str] = {}
    if kind == "sysmon":
        action, cat, typ = SYSMON_ACTIONS.get(eid, (f"sysmon-event-{eid}", ("host",), ("info",)))
        user_name, user_dom = split_user(first(d, "User"))
        exe = first(d, "Image", "SourceImage")
        proc = ProcessInfo(
            name=basename(exe),
            executable=exe,
            pid=to_int(first(d, "ProcessId", "SourceProcessId")),
            command_line=first(d, "CommandLine"),
            parent_name=basename(first(d, "ParentImage")),
            parent_executable=first(d, "ParentImage"),
            hash_sha256=_sha256(first(d, "Hashes", "Hash")),
        )
        src = dst = None
        if eid == 3:
            src = Endpoint(ip=clean_ip(first(d, "SourceIp")), port=to_int(first(d, "SourcePort")))
            dst = Endpoint(ip=clean_ip(first(d, "DestinationIp")), port=to_int(first(d, "DestinationPort")))
        if first(d, "ParentCommandLine"):
            free["process.parent.command_line"] = str(first(d, "ParentCommandLine"))
        draft = Draft(
            timestamp=ts,
            dataset="windows.sysmon",
            action=action,
            category=cat,
            type=typ,
            outcome="success",
            message=message,
            host=host,
            user=UserRef(name=user_name, domain=user_dom) if user_name else None,
            source=src,
            destination=dst,
            process=proc,
            file_path=first(d, "TargetFilename", "TargetObject"),
            url=first(d, "QueryName", "DestinationHostname"),
            file_hash_sha256=_sha256(first(d, "Hashes")),
        )
        if eid == 10 and first(d, "TargetImage"):
            free["target.process.executable"] = str(first(d, "TargetImage"))
        draft.free_text.update(free)
        return draft

    if kind == "powershell":
        script = str(first(d, "ScriptBlockText", "Payload", "ContextInfo") or "")
        user_name, user_dom = split_user(first(d, "UserID", "User", "SubjectUserName"))
        draft = Draft(
            timestamp=ts,
            dataset="windows.powershell",
            action="script-block-logged" if eid == 4104 else f"powershell-event-{eid}",
            category=("process",),
            type=("info",),
            outcome="success",
            message=message or script[:500],
            host=host,
            user=UserRef(name=user_name, domain=user_dom) if user_name else None,
            process=ProcessInfo(name="powershell.exe"),
        )
        if script:
            draft.free_text["powershell.script_block_text"] = script
        return draft

    action, cat, typ, outcome = SECURITY_ACTIONS.get(eid, (f"windows-event-{eid}", ("host",), ("info",), "unknown"))
    if eid in (4624, 4625, 4648, 4768, 4769, 4771, 4776, 4740, 4720):
        uname, udom = split_user(first(d, "TargetUserName"))
        udom = udom or first(d, "TargetDomainName")
    else:
        uname, udom = split_user(first(d, "SubjectUserName"))
        udom = udom or first(d, "SubjectDomainName")
    related_users = []
    target_user, _ = split_user(first(d, "TargetUserName"))
    if target_user and target_user != uname:
        related_users.append(target_user)
    src_ip = clean_ip(first(d, "IpAddress", "SourceAddress", "ClientAddress"))
    if is_loopback(src_ip):
        src_ip = None
    src = Endpoint(ip=src_ip, port=to_int(first(d, "IpPort", "SourcePort"))) if src_ip else None
    dst = None
    if first(d, "DestAddress"):
        dst = Endpoint(ip=clean_ip(first(d, "DestAddress")), port=to_int(first(d, "DestPort")))
    sec_proc: ProcessInfo | None = None
    exe = first(d, "NewProcessName", "ProcessName", "Application")
    if exe or first(d, "CommandLine"):
        sec_proc = ProcessInfo(
            name=basename(exe),
            executable=exe,
            pid=to_int(first(d, "NewProcessId", "ProcessId")),
            command_line=first(d, "CommandLine"),
            parent_name=basename(first(d, "ParentProcessName")),
            parent_executable=first(d, "ParentProcessName"),
        )
    draft = Draft(
        timestamp=ts,
        dataset="windows.security",
        action=action,
        category=cat,
        type=typ,
        outcome=outcome,
        message=message,
        host=host,
        user=UserRef(name=uname, domain=udom) if uname else None,
        source=src,
        destination=dst,
        process=sec_proc,
        file_path=first(d, "RelativeTargetName", "ObjectName", "ServiceFileName", "ImagePath"),
        related_users=related_users,
    )
    if eid == 4625 or (eid == 4776 and str(first(d, "Status") or "0x0").lower() != "0x0"):
        draft.outcome = "failure"
        if eid == 4776:
            draft.action = "credential-validation-failed"
    for key in ("TaskContent", "ServiceFileName", "ShareName", "WorkstationName"):
        if first(d, key):
            draft.free_text[f"winlog.event_data.{key}"] = str(first(d, key))
    ws = short_host(first(d, "WorkstationName"))
    if ws:
        draft.related_hosts.append(ws)
    return draft
