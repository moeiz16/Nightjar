"""Normalisation: raw payload (any supported format) → `NormalizedEvent` (the ECS-like schema of API contract
§1.6). Pure and deterministic, used by the normaliser worker and by A's file-upload preview.

Layer 1 of the injection defence lives here: every free-text value (message, command lines, email subject,
attachment names, URLs, file paths, user agents, script blocks) goes through `safety.neutralise()`; fields
with instruction-like content are listed in `untrusted_flags` and later raise the low-severity detection
"Instruction-like text in a log field".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any

import orjson

from nightjar import clock
from nightjar.contracts import HostRef, NormalizedEvent, ProcessInfo, RawEvent, UntrustedText, UserRef
from nightjar.core.metrics import INJECTION_FLAGS
from nightjar.ingest.common import Draft, is_loopback
from nightjar.ingest.formats import cloud, ecs, identity, syslog, windows
from nightjar.safety import FLAG_BASE64, neutralise

MAX_EVENT_BYTES = 256 * 1024
INSTRUCTION_FLAGS = frozenset({"imperative_to_agent", "role_marker", "tool_syntax", "hidden_text", "external_link"})


def sniff(payload: dict[str, Any]) -> str:
    """Best-effort format detection when the sender gave no `format` hint."""
    keys = set(payload)
    if keys & {"EventID", "EventId"} or isinstance(payload.get("winlog"), dict) or "EventData" in keys:
        return "sysmon" if windows.channel_kind(payload) == "sysmon" else "winlog"
    if "eventSource" in keys and "eventName" in keys:
        return "cloudtrail"
    if "Event Name" in keys and ("Source IP" in keys or "Request ID" in keys):
        return "cloudtrail"
    if "eventType" in keys and ("actor" in keys or "published" in keys):
        return "okta"
    if "Operation" in keys and ({"Workload", "RecordType", "UserId"} & keys):
        return "m365"
    if "program" in keys or ("message" in keys and ("host" in keys or "hostname" in keys) and "event" not in keys):
        return "syslog_json"
    if isinstance(payload.get("event"), dict) and "@timestamp" in keys:
        return "ecs"
    if "dataset" in keys and "action" in keys:
        return "nightjar"
    raise ValueError("unrecognised event format; pass ?format=")


def _parse(fmt: str, payload: dict[str, Any]) -> Draft:
    if fmt in ("winlog", "sysmon"):
        return windows.parse(payload)
    if fmt == "cloudtrail":
        if "Event Name" in payload:
            return cloud.parse_s3_access(payload)
        return cloud.parse_cloudtrail(payload)
    if fmt == "okta":
        return identity.parse_okta(payload)
    if fmt == "m365":
        return identity.parse_m365(payload)
    if fmt == "syslog_json":
        return syslog.parse(payload)
    if fmt == "ecs":
        return ecs.parse_ecs(payload)
    if fmt == "nightjar":
        return ecs.parse_nightjar(payload)
    raise ValueError(f"unsupported format {fmt!r}")


def event_id_for(org_id: object, dataset: str, timestamp_iso: str, payload: dict[str, Any]) -> str:
    """Stable 32-hex content hash: replays and redeliveries of the same payload dedupe."""
    body = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS, default=str)
    h = hashlib.sha256(f"{org_id}|{dataset}|{timestamp_iso}|".encode() + body)
    return h.hexdigest()[:32]


def _uniq(values: list[str | None]) -> tuple[str, ...]:
    out: list[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return tuple(out)


def normalize(raw: RawEvent) -> NormalizedEvent:
    payload = raw.payload
    if not isinstance(payload, dict) or not payload:
        raise ValueError("event must be a non-empty JSON object")
    if len(orjson.dumps(payload, default=str)) > MAX_EVENT_BYTES:
        raise ValueError("event larger than 256 KB")
    fmt = raw.format or sniff(payload)
    draft = _parse(fmt, payload)

    flagged: set[str] = set()

    def clean(value: str | None, field: str, max_len: int) -> tuple[str | None, UntrustedText | None]:
        if value is None:
            return None, None
        u = neutralise(value, field=field, max_len=max_len)
        if set(u.flags) & INSTRUCTION_FLAGS:
            flagged.add(field)
            INJECTION_FLAGS.labels(field=field).inc()
        return u.text, u

    message_u = neutralise(draft.message, field="message", max_len=2000)
    if set(message_u.flags) & INSTRUCTION_FLAGS:
        flagged.add("message")
        INJECTION_FLAGS.labels(field="message").inc()

    proc = draft.process
    if proc is not None:
        cmd, _ = clean(proc.command_line, "process.command_line", 8000)
        proc = replace(proc, command_line=cmd)
    email = draft.email
    if email is not None:
        subject, _ = clean(email.subject, "email.subject", 500)
        names = tuple(clean(n, "email.attachment_names", 260)[0] or "" for n in email.attachment_names)
        urls = tuple(clean(u, "email.urls", 2000)[0] or "" for u in email.urls)
        email = replace(email, subject=subject, attachment_names=names, urls=urls)
    url, _ = clean(draft.url, "url", 2000)
    file_path, _ = clean(draft.file_path, "file.path", 1000)
    for field, text in draft.free_text.items():
        u = neutralise(text, field=field, max_len=8000)
        if set(u.flags) & INSTRUCTION_FLAGS and u.flags != (FLAG_BASE64,):
            flagged.add(field)
            INJECTION_FLAGS.labels(field=field.split(".")[0]).inc()

    host = draft.host if draft.host and draft.host.name else None
    user = draft.user if draft.user and draft.user.name else None
    related_ips = _uniq(
        [
            draft.source.ip if draft.source else None,
            draft.destination.ip if draft.destination else None,
            *(host.ip if host else ()),
            *draft.related_ips,
        ]
    )
    related_ips = tuple(ip for ip in related_ips if not is_loopback(ip))
    related_users = _uniq([user.name if user else None, *draft.related_users])
    related_hosts = _uniq(
        [host.name if host else None, *draft.related_hosts, *unc_hosts(proc.command_line if proc else None)]
    )
    ts_iso = draft.timestamp.isoformat()
    labels = {"ingest": raw.source}
    if raw.source == "replay" and raw.source_ref:
        labels |= {"replay": raw.source_ref, "sample": "true"}
    elif raw.source_ref:
        labels["sourceRef"] = raw.source_ref[:64]
    return NormalizedEvent(
        org_id=raw.org_id,
        event_id=event_id_for(raw.org_id, draft.dataset, ts_iso, payload),
        timestamp=draft.timestamp,
        ingested_at=raw.received_at or clock.now(),
        dataset=draft.dataset,
        kind=draft.kind if draft.kind in ("event", "alert", "signal") else "event",  # type: ignore[arg-type]
        category=tuple(draft.category),
        type=tuple(draft.type),
        action=draft.action,
        outcome=draft.outcome if draft.outcome in ("success", "failure", "unknown") else "unknown",  # type: ignore[arg-type]
        message=message_u,
        severity=draft.severity,
        host=HostRef(name=host.name, ip=tuple(host.ip), os=host.os, id=host.id) if host else None,
        user=UserRef(name=user.name, domain=user.domain, email=user.email, id=user.id) if user else None,
        source=draft.source,
        destination=draft.destination,
        process=proc if proc and (proc.name or proc.executable or proc.command_line) else None,
        cloud=draft.cloud,
        email=email,
        url=url,
        file_path=file_path,
        file_hash_sha256=draft.file_hash_sha256,
        related_ips=related_ips,
        related_users=related_users,
        related_hosts=related_hosts,
        labels=labels,
        raw=payload,
        untrusted_flags=tuple(sorted(flagged)),
    )


_UNC = re.compile(r"\\\\([A-Za-z0-9][A-Za-z0-9.-]{1,62})(?=\\|\s|$)")


def unc_hosts(command_line: str | None) -> list[str]:
    """Remote computers named in a command line (`PsExec64.exe \\\\ant-wms-01 …`): lateral-movement targets."""
    if not command_line:
        return []
    out = []
    for m in _UNC.finditer(command_line):
        name = m.group(1).lower().split(".")[0]
        if name not in ("localhost", "127", "*") and not name.isdigit() and name not in out:
            out.append(name)
    return out[:5]


def preview_process(proc: ProcessInfo | None) -> str:
    if not proc:
        return ""
    return proc.command_line or proc.executable or proc.name or ""
