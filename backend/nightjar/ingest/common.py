"""Shared helpers for the format parsers: timestamps, IPs, names, and the `Draft` every parser fills in."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nightjar.contracts import CloudInfo, EmailInfo, Endpoint, HostRef, ProcessInfo, UserRef


@dataclass
class Draft:
    """What a parser extracts; the normaliser adds ids, neutralises free text and builds the event."""

    timestamp: datetime
    dataset: str
    action: str
    kind: str = "event"
    category: tuple[str, ...] = ()
    type: tuple[str, ...] = ("info",)
    outcome: str = "unknown"
    message: str = ""
    severity: int | None = None
    host: HostRef | None = None
    user: UserRef | None = None
    source: Endpoint | None = None
    destination: Endpoint | None = None
    process: ProcessInfo | None = None
    cloud: CloudInfo | None = None
    email: EmailInfo | None = None
    url: str | None = None
    file_path: str | None = None
    file_hash_sha256: str | None = None
    related_users: list[str] = field(default_factory=list)
    related_hosts: list[str] = field(default_factory=list)
    related_ips: list[str] = field(default_factory=list)
    # extra free-text values (field → text) that must be neutralised and checked for instruction-like content
    free_text: dict[str, str] = field(default_factory=dict)


_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%d/%b/%Y:%H:%M:%S %z",
)


def parse_ts(value: Any) -> datetime:
    """ISO-8601 (with or without zone), Windows `2020-10-18 07:50:06.220`, epoch seconds/millis → aware UTC."""
    if value is None or value == "":
        raise ValueError("missing timestamp")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        v = float(value)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=UTC)
    text = str(value).strip()
    if text.isdigit():
        return parse_ts(int(text))
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"unreadable timestamp {text[:40]!r}")


def first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, "", "-"):
            return d[k]
    return None


def dig(d: Any, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def clean_ip(value: Any) -> str | None:
    if value in (None, "", "-"):
        return None
    text = str(value).strip().removeprefix("::ffff:")
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def is_loopback(ip: str | None) -> bool:
    if not ip:
        return True
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return True


def to_int(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        text = str(value).strip()
        return int(text, 16) if text.lower().startswith("0x") else int(float(text))
    except (TypeError, ValueError):
        return None


def basename(path: str | None) -> str | None:
    if not path:
        return None
    return re.split(r"[\\/]", str(path))[-1] or None


def short_host(name: Any) -> str | None:
    if not name:
        return None
    text = str(name).strip().lower()
    if not text or text == "-":
        return None
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", text):
        return text
    return text.split(".")[0]


def split_user(value: Any) -> tuple[str | None, str | None]:
    """`HALDEN\\svc-dispatch` → ("svc-dispatch", "HALDEN"); `j.okafor@halden.example` → ("j.okafor", None)."""
    if not value or value in ("-", "N/A"):
        return None, None
    text = str(value).strip()
    if "\\" in text:
        dom, name = text.split("\\", 1)
        return name.lower(), dom
    if "@" in text:
        return text.split("@", 1)[0].lower(), None
    return text.lower(), None


def kebab(name: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", str(name))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text)
    return text.strip("-").lower() or "event"


_COUNTRIES = {
    "belgium": "BE",
    "united states": "US",
    "australia": "AU",
    "netherlands": "NL",
    "germany": "DE",
    "france": "FR",
    "nigeria": "NG",
    "romania": "RO",
    "vietnam": "VN",
    "brazil": "BR",
    "russia": "RU",
    "united kingdom": "GB",
    "singapore": "SG",
    "india": "IN",
    "canada": "CA",
}


def country(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return _COUNTRIES.get(text.lower())


def outcome_of(value: Any) -> str:
    v = str(value or "").strip().lower()
    if v in ("success", "succeeded", "allow", "allowed", "ok", "true", "pass", "passed", "0x0"):
        return "success"
    if v in ("failure", "failed", "fail", "deny", "denied", "blocked", "false", "rejected", "error"):
        return "failure"
    return "unknown"


def kv_pairs(message: str) -> dict[str, str]:
    """`user=a.ng src=203.0.113.50 action="login failed"` → dict (syslog messages without structure)."""
    out: dict[str, str] = {}
    for m in re.finditer(r'(\w[\w.-]*)=("([^"]*)"|\S+)', message or ""):
        out[m.group(1)] = m.group(3) if m.group(3) is not None else m.group(2)
    return out
