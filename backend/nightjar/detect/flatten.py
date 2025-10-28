"""Event → Sigma matcher input (ADR 0003).

The matcher looks fields up by exact name in a flat dict and does not iterate lists, so this adapter:
* writes the normalised event as dotted ECS-like keys (`process.command_line`, `source.ip`, …);
* copies the original payload's native fields (Sysmon/Security event data, CloudTrail, Okta, M365) so rules
  written against vendor names work unmapped;
* expands every list into indexed keys (`related.ip.0`, `related.ip.1`) **and** a comma-joined key
  (`related.ip`) so both `contains` and exact matching work;
* adds a few derived fields Nightjar's own rules use (`threat.indicator.verdict`, `email.external_recipients`,
  `network.bytes_out`, `untrusted.count`);
* picks the Sigma logsource (category/product/service) from the dataset and event code.
"""

from __future__ import annotations

from typing import Any

from nightjar.contracts import NormalizedEvent
from nightjar.ingest.common import first, to_int
from nightjar.intel.api import lookup_indicator

LIST_LIMIT = 16

SYSMON_CATEGORY = {
    1: "process_creation",
    3: "network_connection",
    5: "process_termination",
    7: "image_load",
    8: "create_remote_thread",
    10: "process_access",
    11: "file_event",
    12: "registry_event",
    13: "registry_set",
    15: "create_stream_hash",
    17: "pipe_created",
    18: "pipe_created",
    22: "dns_query",
    23: "file_delete",
}
DATASET_LOGSOURCE: dict[str, tuple[str | None, str | None, str | None]] = {
    "aws.cloudtrail": (None, "aws", "cloudtrail"),
    "aws.s3access": (None, "aws", "s3access"),
    "okta.system": (None, "okta", "okta"),
    "m365.audit": (None, "m365", "audit"),
    "mail.gateway": (None, "nightjar", "mail"),
    "vpn.syslog": (None, "nightjar", "remote_access"),
    "edr.alert": (None, "nightjar", "edr"),
    "nightjar.synthetic": (None, "nightjar", "synthetic"),
}
UNTRUSTED_LOGSOURCE = (None, "nightjar", "untrusted_text")


def logsource(event: NormalizedEvent) -> tuple[str | None, str | None, str | None]:
    code = to_int(first(event.raw, "EventID", "EventId")) if event.raw else None
    if event.dataset == "windows.sysmon":
        cat = SYSMON_CATEGORY.get(code or -1)
        return (cat, "windows", None) if cat else (None, "windows", "sysmon")
    if event.dataset == "windows.security":
        if code == 4688:
            return ("process_creation", "windows", None)
        return (None, "windows", "security")
    if event.dataset == "windows.powershell":
        if code == 4104:
            return ("ps_script", "windows", None)
        return (None, "windows", "powershell")
    return DATASET_LOGSOURCE.get(event.dataset, (None, "nightjar", event.dataset.replace(".", "_")))


def _put(out: dict[str, Any], key: str, value: Any) -> None:
    if value is None or value == "" or value == ():
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _put(out, f"{key}.{k}", v)
        return
    if isinstance(value, list | tuple):
        items = [v for v in value if v not in (None, "")]
        if not items:
            return
        if all(not isinstance(v, dict | list | tuple) for v in items):
            out.setdefault(key, ",".join(str(v) for v in items))
            for i, v in enumerate(items[:LIST_LIMIT]):
                out.setdefault(f"{key}.{i}", v)
        else:
            for i, v in enumerate(items[:LIST_LIMIT]):
                _put(out, f"{key}.{i}", v)
        return
    out.setdefault(key, value)


def _domain(addr: str | None) -> str | None:
    if not addr or "@" not in addr:
        return None
    return addr.rsplit("@", 1)[1].lower().strip(">").strip()


def flatten(event: NormalizedEvent) -> dict[str, Any]:
    out: dict[str, Any] = {}
    code = to_int(first(event.raw, "EventID", "EventId")) if event.raw else None
    _put(out, "event.code", code)
    _put(out, "event.action", event.action)
    _put(out, "event.dataset", event.dataset)
    _put(out, "event.outcome", event.outcome)
    _put(out, "event.kind", event.kind)
    _put(out, "event.category", list(event.category))
    _put(out, "message", event.message.text)
    if event.host:
        _put(out, "host.name", event.host.name)
        _put(out, "host.ip", list(event.host.ip))
    if event.user:
        _put(out, "user.name", event.user.name)
        _put(out, "user.domain", event.user.domain)
        _put(out, "user.email", event.user.email)
    for side, ep in (("source", event.source), ("destination", event.destination)):
        if ep:
            _put(out, f"{side}.ip", ep.ip)
            _put(out, f"{side}.port", ep.port)
            _put(out, f"{side}.geo.country_iso_code", ep.geo_country)
            _put(out, f"{side}.as.organization.name", ep.as_org)
    p = event.process
    if p:
        _put(out, "process.executable", p.executable)
        _put(out, "process.name", p.name)
        _put(out, "process.pid", p.pid)
        _put(out, "process.command_line", p.command_line)
        _put(out, "process.parent.executable", p.parent_executable)
        _put(out, "process.parent.name", p.parent_name)
        _put(out, "process.hash.sha256", p.hash_sha256)
    raw = event.raw or {}
    _put(out, "process.pe.original_file_name", first(raw, "OriginalFileName"))
    _put(out, "process.parent.command_line", first(raw, "ParentCommandLine"))
    _put(out, "target.process.executable", first(raw, "TargetImage"))
    _put(out, "destination.domain", first(raw, "DestinationHostname", "QueryName", "dst_host"))
    c = event.cloud
    if c:
        _put(out, "cloud.provider", c.provider)
        _put(out, "cloud.account.id", c.account_id)
        _put(out, "cloud.region", c.region)
        _put(out, "cloud.service.name", c.service)
        _put(out, "cloud.api", c.api)
        _put(out, "cloud.access_key_id", c.access_key_id)
    e = event.email
    external = 0
    if e:
        _put(out, "email.from", e.sender)
        _put(out, "email.to", list(e.recipients))
        _put(out, "email.subject", e.subject)
        _put(out, "email.attachments", list(e.attachment_names))
        _put(out, "email.urls", list(e.urls))
        _put(out, "email.verdict", e.verdict)
        own = _domain(event.user.email if event.user else None) or _domain(e.recipients[0] if e.recipients else None)
        if event.dataset == "m365.audit" and event.user and event.user.email:
            own = _domain(event.user.email)
        external = sum(1 for r in e.recipients if own and _domain(r) and _domain(r) != own)
        if event.dataset == "m365.audit":
            _put(out, "email.external_recipients", external)
    _put(out, "url.original", event.url)
    _put(out, "file.path", event.file_path)
    _put(out, "file.hash.sha256", event.file_hash_sha256)
    _put(out, "related.ip", list(event.related_ips))
    _put(out, "related.user", list(event.related_users))
    _put(out, "related.hosts", list(event.related_hosts))
    out["untrusted.count"] = len(event.untrusted_flags)
    if event.untrusted_flags:
        _put(out, "untrusted.fields", list(event.untrusted_flags))
    bytes_out = to_int(first(raw, "sentbyte", "sent_bytes", "bytes_out"))
    if bytes_out is not None:
        out["network.bytes_out"] = bytes_out
    count = to_int(first(raw, "OperationCount"))
    if count is not None:
        out["m365.operation_count"] = count
    verdict = indicator_verdict(event)
    if verdict:
        out["threat.indicator.verdict"] = verdict
    # native payload fields last (normalised keys win on a clash)
    for k, v in raw.items():
        if k in ("Message", "message"):
            continue
        if isinstance(v, dict):
            _put(out, k, v)
        elif isinstance(v, list | tuple):
            _put(out, k, list(v))
        elif k not in out:
            out[k] = v
    return out


def indicator_verdict(event: NormalizedEvent) -> str | None:
    """Worst bundled-intel verdict for the event's addresses, URLs, domains and hashes (None if no match)."""
    candidates: list[tuple[str, str]] = []
    for ep in (event.source, event.destination):
        if ep and ep.ip:
            candidates.append(("ip", ep.ip))
    if event.url:
        candidates.append(("url", event.url))
    if event.email:
        candidates += [("url", u) for u in event.email.urls]
    dest = first(event.raw or {}, "DestinationHostname", "QueryName", "dst_host")
    if dest:
        candidates.append(("domain", str(dest)))
    if event.process and event.process.hash_sha256:
        candidates.append(("hash", event.process.hash_sha256))
    worst: str | None = None
    for kind, value in candidates:
        v = lookup_indicator(kind, value).verdict
        if v == "malicious":
            return v
        if v == "suspicious":
            worst = v
    return worst
