# ruff: noqa: S608  (SQL is assembled from whitelisted column names; every value is a bound parameter)
"""Public interface of the hunting store (builder A, ADR 0004).

The DuckDB file has ONE read-write owner: the API process, which runs the `store-writer` durable consumer on
`nj.events.normalized.>` (B's `stream.api.subscribe_normalized`) and appends in batches. Nothing else opens
the file while the API runs; offline jobs (`make train`, eval) open it read-only when the API is stopped or
read Parquet snapshots exported by `export_snapshot`. The seed runs while the API is stopped.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nightjar.contracts import NormalizedEvent
from nightjar.core.config import get_settings
from nightjar.hunting.store import (
    EVENT_COLUMNS,
    HuntingStore,
    InvalidQueryError,
    compile_search,
    compile_where,
    flatten,
)

__all__ = [
    "HuntQuery",
    "HuntResult",
    "InvalidQueryError",
    "SearchTimeoutError",
    "append_events",
    "close_store",
    "export_snapshot",
    "get_event",
    "get_store",
    "list_events",
    "search",
]


@dataclass(frozen=True, slots=True)
class HuntQuery:
    """A structured search; never raw SQL from the client (§1.13)."""

    org_id: uuid.UUID
    time_from: datetime
    time_to: datetime
    filters: tuple[tuple[str, str, Any], ...]  # (field, op, value); op ∈ eq, ne, in, contains, prefix, cidr, gt, lt
    group_by: tuple[str, ...] = ()
    metric: str = "count"  # count | count_distinct:<field>
    bucket: str | None = None  # "5m" | "1h" | "1d" time buckets
    limit: int = 200


@dataclass(frozen=True, slots=True)
class HuntResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    total_matched: int
    elapsed_ms: float
    truncated: bool


class SearchTimeoutError(Exception):
    pass


_store: HuntingStore | None = None


def get_store() -> HuntingStore:
    global _store
    path = str(get_settings().duckdb_path)
    if _store is None or _store.path != path:
        if _store is not None:
            _store.close()
        _store = HuntingStore(path)
    return _store


def close_store() -> None:
    global _store
    if _store is not None:
        _store.close()
        _store = None


async def append_events(events: Sequence[NormalizedEvent]) -> int:
    """Batch append (DataFrame insert; ~1M rows/s), never per-row executemany. Returns rows inserted
    (duplicates on (org, event id) are ignored, so redelivery is safe)."""
    if not events:
        return 0
    rows = [flatten(e) for e in events]
    return await asyncio.to_thread(get_store().append, rows)


async def _run(sql: str, params: Sequence[Any]) -> tuple[list[str], list[tuple[Any, ...]]]:
    store = get_store()
    timeout = get_settings().hunting_query_timeout_s
    try:
        return await asyncio.wait_for(asyncio.to_thread(store.query, sql, params), timeout=timeout)
    except TimeoutError as exc:
        store.interrupt()
        raise SearchTimeoutError("search took too long") from exc


def _cell(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


async def search(query: HuntQuery) -> HuntResult:
    limit = max(1, min(int(query.limit), get_settings().hunting_max_rows))
    t0 = time.perf_counter()
    sql, params, count_sql, count_params, names = compile_search(
        str(query.org_id),
        query.time_from,
        query.time_to,
        query.filters,
        group_by=query.group_by,
        metric=query.metric,
        bucket=query.bucket,
        limit=limit,
    )
    _cols, rows = await _run(sql, params)
    _c, total = await _run(count_sql, count_params)
    # event rows end with the message: untrusted text, returned as plain text only (the web app never renders it)
    out_rows = [tuple(_cell(v) for v in r) for r in rows]
    return HuntResult(
        columns=tuple(names),
        rows=tuple(out_rows),
        total_matched=int(total[0][0]) if total else 0,
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        truncated=len(rows) >= limit,
    )


async def list_events(
    org_id: uuid.UUID,
    *,
    time_from: datetime,
    time_to: datetime,
    filters: Sequence[tuple[str, str, Any]],
    q: str | None,
    offset: int,
    limit: int,
) -> tuple[list[dict], int]:
    where, params = compile_where(str(org_id), time_from, time_to, filters)
    if q:
        where += (
            " AND (contains(lower(message), lower(?)) OR contains(lower(coalesce(process_command_line, '')), lower(?)))"
        )
        params += [q, q]
    _c, total = await _run(f"SELECT count(*) FROM events WHERE {where}", params)
    _c, rows = await _run(
        f"SELECT doc FROM events WHERE {where} ORDER BY ts DESC, event_id LIMIT {int(limit)} OFFSET {int(offset)}",
        params,
    )
    return [json.loads(r[0]) for r in rows], int(total[0][0])


async def get_event(org_id: uuid.UUID, event_id: str) -> dict | None:
    _c, rows = await _run("SELECT doc, raw FROM events WHERE org_id = ? AND event_id = ?", [str(org_id), event_id])
    if not rows:
        return None
    doc = json.loads(rows[0][0])
    doc["raw"] = json.loads(rows[0][1] or "{}")
    return doc


async def recent_rows(org_id: uuid.UUID, *, after: tuple[datetime, str] | None, limit: int) -> list[dict[str, Any]]:
    cols = (
        "event_id, ts, dataset, action, outcome, category, severity, host_name, user_name, source_ip, source_geo, "
        "destination_ip, process_name, cloud_api, cloud_access_key_id"
    )
    sql = f"SELECT {cols} FROM events WHERE org_id = ?"
    params: list[Any] = [str(org_id)]
    if after:
        sql += " AND (ts > ? OR (ts = ? AND event_id > ?))"
        params += [after[0], after[0], after[1]]
    sql += f" ORDER BY ts DESC, event_id DESC LIMIT {int(limit)}"
    names, rows = await _run(sql, params)
    return [dict(zip(names, r, strict=True)) for r in rows]


async def event_position(org_id: uuid.UUID, event_id: str) -> tuple[datetime, str] | None:
    _c, rows = await _run("SELECT ts, event_id FROM events WHERE org_id = ? AND event_id = ?", [str(org_id), event_id])
    return (rows[0][0], rows[0][1]) if rows else None


async def events_per_minute(org_id: uuid.UUID, since: datetime, minutes: float) -> float:
    _c, rows = await _run("SELECT count(*) FROM events WHERE org_id = ? AND ingested_at >= ?", [str(org_id), since])
    return round((rows[0][0] if rows else 0) / max(minutes, 1.0), 1)


async def count_events(org_id: uuid.UUID, time_from: datetime, time_to: datetime) -> int:
    _c, rows = await _run(
        "SELECT count(*) FROM events WHERE org_id = ? AND ts >= ? AND ts < ?", [str(org_id), time_from, time_to]
    )
    return int(rows[0][0]) if rows else 0


async def export_snapshot(org_id: uuid.UUID, dest: Path, *, since: datetime) -> Path:
    """Parquet snapshot for model training and eval (read by B's `make train`)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = str(dest).replace("'", "''")
    await _run(
        f"COPY (SELECT * EXCLUDE (doc) FROM events WHERE org_id = ? AND ts >= ? ORDER BY ts) TO '{target}' "
        "(FORMAT PARQUET)",
        [str(org_id), since],
    )
    return dest


EVENT_COLUMN_NAMES = [n for n, _c in EVENT_COLUMNS]


async def event_row(org_id: uuid.UUID, event_id: str) -> dict[str, Any] | None:
    """The river columns of one event (for templated, plain-language lines), or None."""
    cols = (
        "event_id, ts, dataset, action, outcome, category, severity, host_name, user_name, source_ip, source_geo, "
        "destination_ip, process_name, cloud_api, cloud_access_key_id"
    )
    names, rows = await _run(f"SELECT {cols} FROM events WHERE org_id = ? AND event_id = ?", [str(org_id), event_id])
    return dict(zip(names, rows[0], strict=True)) if rows else None


async def minute_totals(org_id: uuid.UUID, time_from: datetime, time_to: datetime) -> list[tuple[datetime, int]]:
    """Events per minute of event time in [from, to): the river's per-minute totals."""
    _c, rows = await _run(
        "SELECT date_trunc('minute', ts) AS m, count(*) FROM events WHERE org_id = ? AND ts >= ? AND ts < ?"
        " GROUP BY m ORDER BY m",
        [str(org_id), time_from, time_to],
    )
    return [(r[0], int(r[1])) for r in rows]


async def hourly_activity(
    org_id: uuid.UUID, column: str, values: Sequence[str], since: datetime, until: datetime
) -> dict[str, dict[datetime, int]]:
    """Events per UTC hour for each user or host in `values` (column `user_name` or `host_name`): the behaviour
    page's rhythm strips."""
    if column not in ("user_name", "host_name") or not values:
        return {}
    marks = ", ".join("?" for _ in values)
    _c, rows = await _run(
        f"SELECT lower({column}), date_trunc('hour', ts) AS h, count(*) FROM events WHERE org_id = ? AND ts >= ?"
        f" AND ts < ? AND lower({column}) IN ({marks}) GROUP BY 1, 2",
        [str(org_id), since, until, *[v.lower() for v in values]],
    )
    out: dict[str, dict[datetime, int]] = {}
    for value, hour, n in rows:
        out.setdefault(value, {})[hour] = int(n)
    return out
