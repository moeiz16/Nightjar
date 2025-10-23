# ruff: noqa: S608  (SQL is assembled from whitelisted column names; every value is a bound parameter)
"""DuckDB hunting store (ADR 0004): one read-write connection owned by the API process.

Events are stored flattened (one column per searchable ECS field) plus the wire document (`doc`, without `raw`)
and the raw payload. List-valued fields are stored as `|a|b|` strings so membership tests stay simple and
type-stable. Inserts go through a DataFrame (`INSERT OR IGNORE ... BY NAME`), deduplicated on (org_id, event_id).

Searches are compiled from a structured `HuntQuery` to parameterised SQL with a mandatory org predicate, a row
cap and a timeout; clients never send SQL.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from nightjar.contracts import NormalizedEvent, to_doc

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  org_id VARCHAR NOT NULL,
  event_id VARCHAR NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  ingested_at TIMESTAMPTZ,
  dataset VARCHAR, kind VARCHAR, category VARCHAR, type VARCHAR, action VARCHAR, outcome VARCHAR,
  severity INTEGER, message VARCHAR, message_flags VARCHAR,
  host_name VARCHAR, host_ips VARCHAR, host_os VARCHAR,
  user_name VARCHAR, user_domain VARCHAR, user_email VARCHAR,
  source_ip VARCHAR, source_port INTEGER, source_geo VARCHAR, source_as_org VARCHAR,
  destination_ip VARCHAR, destination_port INTEGER, destination_geo VARCHAR,
  process_name VARCHAR, process_executable VARCHAR, process_command_line VARCHAR, process_parent_name VARCHAR,
  process_hash VARCHAR,
  cloud_provider VARCHAR, cloud_account_id VARCHAR, cloud_region VARCHAR, cloud_service VARCHAR, cloud_api VARCHAR,
  cloud_access_key_id VARCHAR,
  email_sender VARCHAR, email_subject VARCHAR, email_recipients VARCHAR,
  url VARCHAR, file_path VARCHAR, file_hash VARCHAR,
  related_ips VARCHAR, related_users VARCHAR, related_hosts VARCHAR, labels VARCHAR, untrusted_flags VARCHAR,
  doc VARCHAR, raw VARCHAR,
  PRIMARY KEY (org_id, event_id)
);
"""


@dataclass(frozen=True, slots=True)
class FieldSpec:
    column: str
    label: str
    type: str  # string | number | ip | time
    is_list: bool = False

    @property
    def ops(self) -> list[str]:
        if self.type == "time":
            return ["gt", "lt"]
        if self.type == "number":
            return ["eq", "ne", "in", "gt", "lt"]
        if self.type == "ip":
            return ["eq", "ne", "in", "cidr", "prefix"]
        if self.is_list:
            return ["eq", "in", "contains"]
        return ["eq", "ne", "in", "contains", "prefix"]


FIELDS: dict[str, FieldSpec] = {
    "timestamp": FieldSpec("ts", "Time", "time"),
    "dataset": FieldSpec("dataset", "Source of the event", "string"),
    "action": FieldSpec("action", "What happened", "string"),
    "outcome": FieldSpec("outcome", "Outcome", "string"),
    "category": FieldSpec("category", "Category", "string", True),
    "severity": FieldSpec("severity", "Reported severity", "number"),
    "message": FieldSpec("message", "Message text", "string"),
    "host.name": FieldSpec("host_name", "Computer", "string"),
    "host.ip": FieldSpec("host_ips", "Computer address", "ip", True),
    "user.name": FieldSpec("user_name", "Person or account", "string"),
    "user.email": FieldSpec("user_email", "Email address", "string"),
    "source.ip": FieldSpec("source_ip", "From address", "ip"),
    "source.geoCountry": FieldSpec("source_geo", "From country", "string"),
    "source.asOrg": FieldSpec("source_as_org", "From network", "string"),
    "destination.ip": FieldSpec("destination_ip", "To address", "ip"),
    "destination.port": FieldSpec("destination_port", "To port", "number"),
    "process.name": FieldSpec("process_name", "Program", "string"),
    "process.commandLine": FieldSpec("process_command_line", "Program command", "string"),
    "process.parentName": FieldSpec("process_parent_name", "Started by program", "string"),
    "process.hashSha256": FieldSpec("process_hash", "Program fingerprint", "string"),
    "cloud.provider": FieldSpec("cloud_provider", "Cloud provider", "string"),
    "cloud.accountId": FieldSpec("cloud_account_id", "Cloud account", "string"),
    "cloud.region": FieldSpec("cloud_region", "Cloud region", "string"),
    "cloud.service": FieldSpec("cloud_service", "Cloud service", "string"),
    "cloud.api": FieldSpec("cloud_api", "Cloud operation", "string"),
    "cloud.accessKeyId": FieldSpec("cloud_access_key_id", "Cloud access key", "string"),
    "email.sender": FieldSpec("email_sender", "Email sender", "string"),
    "email.subject": FieldSpec("email_subject", "Email subject", "string"),
    "url": FieldSpec("url", "Web address", "string"),
    "filePath": FieldSpec("file_path", "File", "string"),
    "fileHashSha256": FieldSpec("file_hash", "File fingerprint", "string"),
    "relatedIps": FieldSpec("related_ips", "Any address", "ip", True),
    "relatedUsers": FieldSpec("related_users", "Any person or account", "string", True),
    "relatedHosts": FieldSpec("related_hosts", "Any computer", "string", True),
    "untrustedFlags": FieldSpec("untrusted_flags", "Instruction-like text found in", "string", True),
}
OPS = ("eq", "ne", "in", "contains", "prefix", "cidr", "gt", "lt")
BUCKETS = {"5m": "5 minutes", "1h": "1 hour", "1d": "1 day"}


class InvalidQueryError(ValueError):
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def _pipe(values: Sequence[str] | None) -> str | None:
    vals = [str(v) for v in values or () if v not in (None, "")]
    return "|" + "|".join(vals) + "|" if vals else None


def flatten(e: NormalizedEvent) -> dict[str, Any]:
    doc = to_doc(e)
    raw = doc.pop("raw", {}) or {}
    h, u, s, d, p, c, m = e.host, e.user, e.source, e.destination, e.process, e.cloud, e.email
    return {
        "org_id": str(e.org_id),
        "event_id": e.event_id,
        "ts": e.timestamp,
        "ingested_at": e.ingested_at,
        "dataset": e.dataset,
        "kind": e.kind,
        "category": _pipe(e.category),
        "type": _pipe(e.type),
        "action": e.action,
        "outcome": e.outcome,
        "severity": e.severity,
        "message": e.message.text,
        "message_flags": _pipe(e.message.flags),
        "host_name": h.name if h else None,
        "host_ips": _pipe(h.ip) if h else None,
        "host_os": h.os if h else None,
        "user_name": u.name if u else None,
        "user_domain": u.domain if u else None,
        "user_email": u.email if u else None,
        "source_ip": s.ip if s else None,
        "source_port": s.port if s else None,
        "source_geo": s.geo_country if s else None,
        "source_as_org": s.as_org if s else None,
        "destination_ip": d.ip if d else None,
        "destination_port": d.port if d else None,
        "destination_geo": d.geo_country if d else None,
        "process_name": p.name if p else None,
        "process_executable": p.executable if p else None,
        "process_command_line": p.command_line if p else None,
        "process_parent_name": p.parent_name if p else None,
        "process_hash": p.hash_sha256 if p else None,
        "cloud_provider": c.provider if c else None,
        "cloud_account_id": c.account_id if c else None,
        "cloud_region": c.region if c else None,
        "cloud_service": c.service if c else None,
        "cloud_api": c.api if c else None,
        "cloud_access_key_id": c.access_key_id if c else None,
        "email_sender": m.sender if m else None,
        "email_subject": m.subject if m else None,
        "email_recipients": _pipe(m.recipients) if m else None,
        "url": e.url,
        "file_path": e.file_path,
        "file_hash": e.file_hash_sha256,
        "related_ips": _pipe(e.related_ips),
        "related_users": _pipe(e.related_users),
        "related_hosts": _pipe(e.related_hosts),
        "labels": json.dumps(e.labels, separators=(",", ":")),
        "untrusted_flags": _pipe(e.untrusted_flags),
        "doc": json.dumps(doc, separators=(",", ":"), default=str),
        "raw": json.dumps(raw, separators=(",", ":"), default=str),
    }


_INT_COLS = ("severity", "source_port", "destination_port")


def _in_cidr(value: str | None, cidr: str | None) -> bool:
    if not value or not cidr:
        return False
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    for part in value.strip("|").split("|"):
        try:
            if ipaddress.ip_address(part) in net:
                return True
        except ValueError:
            continue
    return False


class HuntingStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._con: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            con = duckdb.connect(self.path)
            con.execute("SET TimeZone='UTC'")
            con.execute(SCHEMA)
            con.execute("CREATE INDEX IF NOT EXISTS ix_events_org_ts ON events (org_id, ts)")
            con.create_function("nj_cidr", _in_cidr, ["VARCHAR", "VARCHAR"], "BOOLEAN", null_handling="special")  # type: ignore[call-overload]
            self._con = con
        return self._con

    def append(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df["ingested_at"] = pd.to_datetime(df["ingested_at"], utc=True)
        for col in _INT_COLS:
            df[col] = pd.array(df[col], dtype="Int64")
        with self._lock:
            con = self._connect()
            con.register("nj_batch", df)
            try:
                res = con.execute("INSERT OR IGNORE INTO events BY NAME SELECT * FROM nj_batch").fetchone()
            finally:
                con.unregister("nj_batch")
        return int(res[0]) if res else 0

    def query(self, sql: str, params: Sequence[Any]) -> tuple[list[str], list[tuple[Any, ...]]]:
        with self._lock:
            cur = self._connect().execute(sql, list(params))
            cols = [d[0] for d in cur.description or []]
            return cols, cur.fetchall()

    def interrupt(self) -> None:
        if self._con is not None:
            self._con.interrupt()

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None


# ── Query compiler ───────────────────────────────────────────────────────────
def _spec(field: str) -> FieldSpec:
    spec = FIELDS.get(field)
    if spec is None:
        raise InvalidQueryError(f"Unknown field {field!r}.", field)
    return spec


def _condition(field: str, op: str, value: Any) -> tuple[str, list[Any]]:
    spec = _spec(field)
    col = spec.column
    if op not in spec.ops:
        raise InvalidQueryError(f"{spec.label} cannot be searched with {op!r}.", field)
    if op == "in":
        values = value if isinstance(value, list) else [value]
        values = [v for v in values if v not in (None, "")]
        if not values or len(values) > 100:
            raise InvalidQueryError("Give between 1 and 100 values.", field)
        if spec.is_list:
            return "(" + " OR ".join(f"contains(lower({col}), '|' || lower(?) || '|')" for _ in values) + ")", [
                str(v) for v in values
            ]
        if spec.type == "number":
            return f"{col} IN ({', '.join('?' for _ in values)})", [_num(v, field) for v in values]
        return f"lower({col}) IN ({', '.join('lower(?)' for _ in values)})", [str(v) for v in values]
    if isinstance(value, list):
        raise InvalidQueryError("Use the 'in' operator for several values.", field)
    if spec.type == "time":
        return f"{col} {'>' if op == 'gt' else '<'} ?", [_dt(value, field)]
    if spec.type == "number":
        sym = {"eq": "=", "ne": "<>", "gt": ">", "lt": "<"}[op]
        return f"{col} {sym} ?", [_num(value, field)]
    text = str(value)
    if not text or len(text) > 500:
        raise InvalidQueryError("Give a value of 1 to 500 characters.", field)
    if op == "cidr":
        try:
            ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise InvalidQueryError("Not a valid address range (for example 203.0.113.0/24).", field) from exc
        return f"nj_cidr({col}, ?)", [text]
    if spec.is_list:
        if op == "eq":
            return f"contains(lower({col}), '|' || lower(?) || '|')", [text]
        return f"contains(lower({col}), lower(?))", [text]
    if op == "eq":
        return f"lower({col}) = lower(?)", [text]
    if op == "ne":
        return f"({col} IS NULL OR lower({col}) <> lower(?))", [text]
    if op == "contains":
        return f"contains(lower({col}), lower(?))", [text]
    if op == "prefix":
        return f"starts_with(lower({col}), lower(?))", [text]
    raise InvalidQueryError(f"Unsupported operator {op!r}.", field)


def _num(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidQueryError("Expected a number.", field) from exc


def _dt(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise InvalidQueryError("Expected a date and time.", field) from exc


def compile_where(
    org_id: str, time_from: datetime, time_to: datetime, filters: Sequence[tuple[str, str, Any]]
) -> tuple[str, list[Any]]:
    if time_to <= time_from:
        raise InvalidQueryError("The end of the time range must be after its start.", "to")
    if len(filters) > 20:
        raise InvalidQueryError("Use at most 20 conditions.")
    parts, params = ["org_id = ?", "ts >= ?", "ts < ?"], [org_id, time_from, time_to]
    for field, op, value in filters:
        if op not in OPS:
            raise InvalidQueryError(f"Unknown operator {op!r}.", field)
        sql, p = _condition(field, op, value)
        parts.append(sql)
        params.extend(p)
    return " AND ".join(parts), params


EVENT_COLUMNS = (
    ("timestamp", "ts"),
    ("eventId", "event_id"),
    ("dataset", "dataset"),
    ("action", "action"),
    ("outcome", "outcome"),
    ("host.name", "host_name"),
    ("user.name", "user_name"),
    ("source.ip", "source_ip"),
    ("destination.ip", "destination_ip"),
)


def compile_search(
    org_id: str,
    time_from: datetime,
    time_to: datetime,
    filters: Sequence[tuple[str, str, Any]],
    *,
    group_by: Sequence[str],
    metric: str,
    bucket: str | None,
    limit: int,
) -> tuple[str, list[Any], str, list[Any], list[str]]:
    """(select sql, params, count sql, count params, column names)."""
    where, params = compile_where(org_id, time_from, time_to, filters)
    count_sql = f"SELECT count(*) FROM events WHERE {where}"
    if len(group_by) > 3:
        raise InvalidQueryError("Group by at most three fields.")
    for g in group_by:
        if _spec(g).is_list or _spec(g).type == "time":
            raise InvalidQueryError(f"{_spec(g).label} cannot be grouped on.", g)
    metric_sql, metric_name = "count(*)", "count"
    if metric != "count":
        if not metric.startswith("count_distinct:"):
            raise InvalidQueryError("Metric must be count or count_distinct:<field>.", "metric")
        mf = metric.split(":", 1)[1]
        if _spec(mf).is_list:
            raise InvalidQueryError(f"{_spec(mf).label} cannot be counted distinctly.", "metric")
        metric_sql, metric_name = f"count(DISTINCT {_spec(mf).column})", f"distinct {mf}"
    if bucket is not None and bucket not in BUCKETS:
        raise InvalidQueryError("Bucket must be 5m, 1h or 1d.", "bucket")
    if not group_by and bucket is None and metric == "count":
        cols = ", ".join(c for _n, c in EVENT_COLUMNS) + ", message"
        sql = f"SELECT {cols} FROM events WHERE {where} ORDER BY ts DESC, event_id LIMIT {int(limit)}"
        return sql, params, count_sql, list(params), [n for n, _c in EVENT_COLUMNS] + ["summary"]
    select_parts, names = [], []
    if bucket:
        select_parts.append(f"time_bucket(INTERVAL '{BUCKETS[bucket]}', ts) AS bucket")
        names.append("bucket")
    for g in group_by:
        select_parts.append(f'{_spec(g).column} AS "{g}"')
        names.append(g)
    select_parts.append(f"{metric_sql} AS metric")
    names.append(metric_name)
    group_sql = ", ".join(str(i + 1) for i in range(len(select_parts) - 1))
    order = "1 ASC" if bucket else "metric DESC"
    sql = (
        f"SELECT {', '.join(select_parts)} FROM events WHERE {where}"
        + (f" GROUP BY {group_sql}" if group_sql else "")
        + f" ORDER BY {order} LIMIT {int(limit)}"
    )
    return sql, params, count_sql, list(params), names


def timed(fn):
    def wrapper(*a, **kw):
        t0 = time.perf_counter()
        out = fn(*a, **kw)
        return out, (time.perf_counter() - t0) * 1000

    return wrapper
