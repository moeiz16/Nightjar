"""Public interface of the event backbone (builder B). Called by A's ingest endpoints, hunting writer,
health endpoint and job starters. Subjects and streams: API contract Part 4."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import statistics
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import orjson

from nightjar import clock
from nightjar.contracts import IngestAck, NormalizedEvent, PipelineHealth, RawEvent, ScenarioInfo, to_doc
from nightjar.core.metrics import EVENTS_INGESTED, EVENTS_REJECTED
from nightjar.ingest import normalizer as _normalizer
from nightjar.stream import codec
from nightjar.stream.bus import BusError, StreamFullError
from nightjar.stream.runtime import (
    alert_to_triage_samples,
    events_per_minute,
    get_bus,
    list_dead_letters,
    retry_dead_letter,
    run_batch_consumer,
)
from nightjar.stream.topology import CONSUMER_BY_NAME, jobs_subject, raw_subject

MAX_EVENT_BYTES = 256 * 1024
PUBLISH_BUDGET_S = 2.0


class BackpressureError(Exception):
    """The raw stream is at its limit (discard=new) or the bus is offline. A answers 503 + Retry-After."""

    def __init__(self, retry_after_s: int = 15) -> None:
        super().__init__("event intake is temporarily full")
        self.retry_after_s = retry_after_s


class Subscription(Protocol):
    async def stop(self) -> None: ...


def _msg_id(raw: RawEvent) -> str:
    if raw.dedupe_key:
        return raw.dedupe_key[:200]
    body = orjson.dumps(raw.payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS, default=str)
    return hashlib.sha256(f"{raw.org_id}|".encode() + body).hexdigest()


async def publish_raw(events: Sequence[RawEvent]) -> IngestAck:
    """Publish to `nj.raw.<org>.<source>` with `Nats-Msg-Id = dedupe_key or sha256(payload)`. Validates size
    (≤ 256 KB per event) and shape; never blocks longer than 2 s. Raises BackpressureError."""
    try:
        bus = await asyncio.wait_for(get_bus(), timeout=PUBLISH_BUDGET_S)
    except (BusError, TimeoutError, OSError) as exc:
        raise BackpressureError(15) from exc
    accepted = duplicates = rejected = 0
    first_seq: int | None = None
    last_seq: int | None = None
    errors: list[str] = []
    deadline = asyncio.get_running_loop().time() + PUBLISH_BUDGET_S
    for i, raw in enumerate(events):
        if not isinstance(raw.payload, dict) or not raw.payload:
            rejected += 1
            errors.append(f"{i}: event must be a JSON object")
            EVENTS_REJECTED.labels(reason="shape").inc()
            continue
        data = codec.dumps(raw)
        if len(data) > MAX_EVENT_BYTES:
            rejected += 1
            errors.append(f"{i}: event larger than 256 KB")
            EVENTS_REJECTED.labels(reason="size").inc()
            continue
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise BackpressureError(5)
        headers = {
            "Nj-Org": str(raw.org_id),
            "Nj-Schema": "raw_event.v1",
            "Nj-Trace": uuid.uuid4().hex,
            "Nj-Received-At": (raw.received_at or clock.now()).isoformat(),
        }
        try:
            ack = await asyncio.wait_for(
                bus.publish(raw_subject(raw.org_id, raw.source), data, headers=headers, msg_id=_msg_id(raw)),
                timeout=max(0.05, remaining),
            )
        except StreamFullError as exc:
            if accepted == 0 and duplicates == 0:
                raise BackpressureError(15) from exc
            errors.append(f"{i}: intake full; retry the remaining events")
            rejected += len(events) - i
            break
        except (BusError, TimeoutError) as exc:
            raise BackpressureError(15) from exc
        if ack.duplicate:
            duplicates += 1
            continue
        accepted += 1
        EVENTS_INGESTED.labels(source=raw.source).inc()
        first_seq = ack.seq if first_seq is None else first_seq
        last_seq = ack.seq
    return IngestAck(accepted, duplicates, rejected, first_seq, last_seq, tuple(errors[:50]))


def normalize(raw: RawEvent) -> NormalizedEvent:
    """Pure function used by the normaliser worker and by A's file-upload preview. Raises ValueError with a
    plain reason for events that cannot be normalised (they go to the DLQ in the pipeline)."""
    return _normalizer.normalize(raw)


class _Sub:
    def __init__(self, task: asyncio.Task[None], stop: asyncio.Event) -> None:
        self._task, self._stop = task, stop

    async def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(self._task, timeout=5)


async def subscribe_normalized(
    durable: str,
    handler: Callable[[Sequence[NormalizedEvent]], Awaitable[None]],
    *,
    batch: int = 500,
    max_wait_s: float = 1.0,
) -> Subscription:
    """Durable pull consumer on `nj.events.normalized.>`: the handler gets batches; the batch is acked after the
    handler returns, nak'ed with backoff if it raises. Used by A's hunting-store writer (`store-writer`)."""
    if durable not in CONSUMER_BY_NAME:
        raise ValueError(f"unknown durable consumer {durable!r}")
    bus = await get_bus()
    stop = asyncio.Event()

    async def on_batch(msgs: Sequence[Any]) -> None:
        events = [codec.decode(NormalizedEvent, m.data) for m in msgs]
        await handler(events)

    task = asyncio.create_task(
        run_batch_consumer(durable, on_batch, stop=stop, batch=batch, fetch_timeout=max_wait_s, bus=bus)
    )
    return _Sub(task, stop)


_PLAIN = {
    "normal": "Taking in events normally",
    "catching_up": "Catching up: about {minutes} minute{s} behind",
    "degraded": "Some processing is being retried",
    "offline": "Event intake is paused; senders are asked to retry",
}


async def pipeline_health() -> PipelineHealth:
    from nightjar.core.config import get_settings

    mode = "memory" if get_settings().bus == "memory" else "nats"
    try:
        bus = await asyncio.wait_for(get_bus(), timeout=2.0)
        stats = await asyncio.wait_for(bus.stats(), timeout=3.0)
        connected = bus.connected
    except Exception:
        return PipelineHealth(mode, False, "offline", _PLAIN["offline"], 0.0, (), 0, None, None)  # type: ignore[arg-type]
    eps = events_per_minute()
    lag = max((c.pending + c.ack_pending for s in stats for c in s.consumers if c.name != "store-writer"), default=0)
    dlq = next((s.messages for s in stats if s.name == "NJ_DLQ"), 0)
    redelivered = sum(c.redelivered for s in stats for c in s.consumers)
    status = "normal"
    plain = _PLAIN["normal"]
    if not connected:
        status, plain = "offline", _PLAIN["offline"]
    elif lag > 10_000:
        minutes = max(1, round(lag / max(eps, 600)))
        status, plain = "catching_up", _PLAIN["catching_up"].format(minutes=minutes, s="s" if minutes != 1 else "")
    elif redelivered > 100 and lag > 1000:
        status, plain = "degraded", _PLAIN["degraded"]
    samples = sorted(alert_to_triage_samples())
    p50 = round(statistics.median(samples), 2) if samples else None
    p95 = round(samples[min(len(samples) - 1, int(0.95 * len(samples)))], 2) if samples else None
    return PipelineHealth(mode, connected, status, plain, round(eps, 1), stats, int(dlq), p50, p95)  # type: ignore[arg-type]


def list_scenarios() -> list[ScenarioInfo]:
    """Bundled synthetic replays (`halden-night`, …) plus downloaded OTRF scenarios when present."""
    from nightjar.workers.replay.loader import scenarios

    return scenarios()


async def start_replay(org_id: uuid.UUID, scenario_id: str, *, speed: float, job_id: uuid.UUID) -> None:
    """Enqueue a replay job (`nj.jobs.replay`); the worker re-times events relative to `clock.now()` and
    publishes them as `source="replay"`. Progress via A's job row + SSE `job.updated`."""
    from nightjar.workers.replay.loader import scenario_ids

    if scenario_id not in scenario_ids():
        raise KeyError(scenario_id)
    await enqueue_job("replay", job_id, {"orgId": str(org_id), "scenarioId": scenario_id, "speed": float(speed)})


async def enqueue_job(kind: str, job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Publish `nj.jobs.<kind>` (kinds: replay, replay_file, train, weekly_report, reindex)."""
    bus = await get_bus()
    body = {"jobId": str(job_id), "kind": kind, "payload": payload}
    try:
        await bus.publish(
            jobs_subject(kind),
            codec.dumps(body),
            msg_id=f"job-{job_id}",
            headers={"Nj-Schema": "job.v1", "Nj-Org": str(payload.get("orgId", ""))},
        )
    except (BusError, StreamFullError) as exc:
        raise BackpressureError(15) from exc


async def list_dlq(*, page: int = 1, page_size: int = 25) -> tuple[list[dict[str, Any]], int]:
    """Dead-lettered messages `{ seq, stream, consumer, subject, deliveries, reason, at }`, newest first."""
    return await list_dead_letters(page=page, page_size=page_size)


async def retry_dlq(seq: int) -> None:
    """Re-publish a dead letter to its original subject (KeyError if unknown or already retried)."""
    await retry_dead_letter(seq)


def to_wire(event: NormalizedEvent) -> dict[str, Any]:
    return to_doc(event)
