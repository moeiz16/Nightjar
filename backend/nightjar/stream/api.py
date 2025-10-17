"""Public interface of the event backbone (builder B). Called by A's ingest endpoints, hunting writer,
health endpoint and job starters. Subjects and streams: API contract Part 4."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from nightjar.contracts import IngestAck, NormalizedEvent, PipelineHealth, RawEvent, ScenarioInfo


class BackpressureError(Exception):
    """The raw stream is at its limit (discard=new) or the bus is offline. A answers 503 + Retry-After."""

    def __init__(self, retry_after_s: int = 15) -> None:
        super().__init__("event intake is temporarily full")
        self.retry_after_s = retry_after_s


class Subscription(Protocol):
    async def stop(self) -> None: ...


async def publish_raw(events: Sequence[RawEvent]) -> IngestAck:
    """Publish to `nj.raw.<org>.<source>` with `Nats-Msg-Id = dedupe_key or sha256(payload)`. Validates size
    (≤ 256 KB per event) and shape; never blocks longer than 2 s. Raises BackpressureError."""
    raise NotImplementedError


def normalize(raw: RawEvent) -> NormalizedEvent:
    """Pure function used by the normaliser worker and by A's file-upload preview. Raises ValueError with a
    plain reason for events that cannot be normalised (they go to the DLQ in the pipeline)."""
    raise NotImplementedError


async def subscribe_normalized(
    durable: str,
    handler: Callable[[Sequence[NormalizedEvent]], Awaitable[None]],
    *,
    batch: int = 500,
    max_wait_s: float = 1.0,
) -> Subscription:
    """Durable pull consumer on `nj.events.normalized.>`: the handler gets batches; the batch is acked after the
    handler returns, nak'ed with backoff if it raises. Used by A's hunting-store writer (`store-writer`)."""
    raise NotImplementedError


async def pipeline_health() -> PipelineHealth:
    raise NotImplementedError


def list_scenarios() -> list[ScenarioInfo]:
    """Bundled synthetic replays (`halden-night`, …) plus downloaded OTRF scenarios when present."""
    raise NotImplementedError


async def start_replay(org_id: uuid.UUID, scenario_id: str, *, speed: float, job_id: uuid.UUID) -> None:
    """Enqueue a replay job (`nj.jobs.replay`); the worker re-times events relative to `clock.now()` and
    publishes them as `source="replay"`. Progress via A's job row + SSE `job.updated`."""
    raise NotImplementedError


async def enqueue_job(kind: str, job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Publish `nj.jobs.<kind>` (kinds: replay, train, weekly_report, reindex)."""
    raise NotImplementedError
