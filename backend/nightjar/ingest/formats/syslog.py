"""Syslog-style JSON (`{ "@timestamp"|"timestamp", "host", "program", "severity", "message", ... }`) from
remote-access gateways and firewalls, the mail gateway and the endpoint agent. Structured fields may be at the
top level or only inside `message` as `key=value` pairs."""

from __future__ import annotations

from typing import Any

from nightjar.contracts import EmailInfo, Endpoint, HostRef, ProcessInfo, UserRef
from nightjar.ingest.common import (
    Draft,
    clean_ip,
    country,
    first,
    kebab,
    kv_pairs,
    outcome_of,
    parse_ts,
    short_host,
    split_user,
    to_int,
)

VPN_PROGRAMS = {"fortigate", "netscaler", "vpn", "openvpn", "ipsec", "anyconnect", "firewall", "fw", "citrix"}
MAIL_PROGRAMS = {"mailgw", "mail-gateway", "postfix", "proofpoint", "mimecast", "exchange-edge"}
EDR_PROGRAMS = {"edr", "defender", "endpoint", "av"}
_SEVERITY = {"emerg": 0, "alert": 1, "crit": 2, "err": 3, "error": 3, "warning": 4, "warn": 4, "notice": 5}
_SEVERITY |= {"info": 6, "informational": 6, "debug": 7}


def _sev(value: Any) -> int | None:
    if value is None:
        return None
    n = to_int(value)
    if n is None:
        n = _SEVERITY.get(str(value).lower())
    if n is None:
        return None
    return max(0, min(100, int((7 - min(max(n, 0), 7)) * 100 / 7)))


def _list(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value if v)
    return tuple(v.strip() for v in str(value).replace(";", ",").split(",") if v.strip())


def parse(p: dict[str, Any]) -> Draft:
    message = str(first(p, "message", "msg") or "")
    fields = {**kv_pairs(message), **{k: v for k, v in p.items() if k not in ("message", "msg")}}
    program = str(first(fields, "program", "app_name", "ident") or "").lower()
    ts = parse_ts(first(fields, "@timestamp", "timestamp", "time"))
    host_name = short_host(first(fields, "host", "hostname", "devname"))
    host = HostRef(name=host_name) if host_name else None
    sev = _sev(first(fields, "severity", "level", "pri"))
    uname, udom = split_user(first(fields, "user", "username", "usrname", "srcuser"))
    src_ip = clean_ip(first(fields, "src_ip", "srcip", "src", "client_ip"))
    dst_ip = clean_ip(first(fields, "dst_ip", "dstip", "dst"))
    src = (
        Endpoint(
            ip=src_ip,
            port=to_int(first(fields, "src_port", "srcport")),
            geo_country=country(first(fields, "src_country", "srccountry", "country")),
            as_org=first(fields, "src_as_org", "as_org"),
        )
        if src_ip
        else None
    )
    dst = (
        Endpoint(
            ip=dst_ip,
            port=to_int(first(fields, "dst_port", "dstport")),
            geo_country=country(first(fields, "dstcountry")),
        )
        if dst_ip
        else None
    )
    act = str(first(fields, "action", "event", "status") or "").lower()

    if program in MAIL_PROGRAMS or first(fields, "subject", "sender"):
        email = EmailInfo(
            sender=first(fields, "sender", "from"),
            recipients=_list(first(fields, "recipients", "to", "rcpt")),
            subject=first(fields, "subject"),
            attachment_names=_list(first(fields, "attachments")),
            urls=_list(first(fields, "urls")),
            verdict=first(fields, "verdict", "disposition"),
        )
        rcpt_user = split_user(email.recipients[0])[0] if email.recipients else None
        draft = Draft(
            timestamp=ts,
            dataset="mail.gateway",
            action="email-delivered" if act in ("", "deliver", "delivered", "pass") else f"email-{kebab(act)}",
            category=("email",),
            type=("info",),
            outcome="success" if act in ("", "deliver", "delivered", "pass") else outcome_of(act),
            message=message or f"Email from {email.sender}",
            severity=sev,
            host=host,
            user=UserRef(name=rcpt_user, email=email.recipients[0]) if rcpt_user else None,
            source=src,
            email=email,
            url=email.urls[0] if email.urls else None,
        )
        if email.subject:
            draft.free_text["email.subject"] = str(email.subject)
        for i, name in enumerate(email.attachment_names):
            draft.free_text[f"email.attachments.{i}"] = name
        for i, u in enumerate(email.urls):
            draft.free_text[f"email.urls.{i}"] = u
        return draft

    if program in EDR_PROGRAMS:
        exe = first(fields, "process", "image", "file")
        draft = Draft(
            timestamp=ts,
            dataset="edr.alert",
            kind="alert",
            action=kebab(act or "alert"),
            category=("malware", "intrusion_detection"),
            type=("info",),
            outcome="unknown",
            message=message,
            severity=sev,
            host=host,
            user=UserRef(name=uname, domain=udom) if uname else None,
            process=ProcessInfo(name=exe.split("\\")[-1] if exe else None, executable=exe) if exe else None,
            file_hash_sha256=first(fields, "sha256"),
        )
        return draft

    # remote access gateways / firewalls (default)
    if act in ("login", "logon", "auth-success", "tunnel-up", "ssl-login-success", "success"):
        action, cat, outcome = "logon", ("authentication",), "success"
    elif act in ("login-failed", "logon-failed", "auth-failure", "ssl-login-fail", "failed", "failure", "deny-login"):
        action, cat, outcome = "logon-failed", ("authentication",), "failure"
    elif act in ("logout", "tunnel-down"):
        action, cat, outcome = "logoff", ("authentication",), "success"
    elif act in ("traffic", "accept", "close", "allow", "flow", "session"):
        action, cat, outcome = "network-flow", ("network",), "success"
    elif act in ("deny", "drop", "block", "blocked"):
        action, cat, outcome = "network-flow-denied", ("network",), "failure"
    else:
        action, cat, outcome = kebab(act or program or "syslog"), ("network",), outcome_of(act)
    draft = Draft(
        timestamp=ts,
        dataset="vpn.syslog",
        action=action,
        category=cat,
        type=("start",) if action == "logon" else ("connection",) if "flow" in action else ("info",),
        outcome=outcome,
        message=message,
        severity=sev,
        host=host,
        user=UserRef(name=uname, domain=udom) if uname else None,
        source=src,
        destination=dst,
        url=first(fields, "dst_host", "hostname_dst", "url"),
    )
    for key in ("sentbyte", "sent_bytes", "bytes_out"):
        if first(fields, key) is not None:
            draft.free_text.setdefault("network.bytes_out", str(first(fields, key)))
    return draft


def bytes_out(raw: dict[str, Any]) -> int:
    """Bytes sent by the client, for behaviour features (0 when absent)."""
    fields = {**kv_pairs(str(raw.get("message") or "")), **raw}
    return to_int(first(fields, "sentbyte", "sent_bytes", "bytes_out")) or 0
