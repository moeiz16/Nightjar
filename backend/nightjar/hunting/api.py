"""Public interface of the hunting store (builder A, ADR 0004).

The DuckDB file has ONE read-write owner: the API process, which runs the `store-writer` durable consumer on
`nj.events.normalized.>` (B's `stream.api.subscribe_normalized`) and appends in batches. Nothing else opens
the file while the API runs; offline jobs (`make train`, eval) open it read-only when the API is stopped or
read Parquet snapshots exported by `export_snapshot`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nightjar.contracts import NormalizedEvent


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


async def append_events(events: Sequence[NormalizedEvent]) -> int:
    """Batch append (DataFrame/Arrow insert; ~1M rows/s) — never per-row executemany (~2k rows/s)."""
    raise NotImplementedError


async def search(query: HuntQuery) -> HuntResult:
    raise NotImplementedError


async def export_snapshot(org_id: uuid.UUID, dest: Path, *, since: datetime) -> Path:
    """Parquet snapshot for model training and eval (read by B's `make train`)."""
    raise NotImplementedError
