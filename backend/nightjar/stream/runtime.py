"""Process-wide bus handle, the durable-consumer loop (ack / nak with back-off / dead-letter) and the DLQ
register. Every handler in the pipeline runs through `run_consumer`, so retry and dead-letter behaviour is
identical across stages and across the NATS and memory buses (API contract Part 4, "Retries and dead letters").
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import structlog

from nightjar import clock
from nightjar.core.metrics import CONSUMER_LAG, DLQ_MESSAGES, STAGE_LATENCY
from nightjar.stream.bus import Bus, BusError, MemoryBus, Msg
from nightjar.stream.topology import CONSUMER_BY_NAME, dlq_subject

log = structlog.get_logger("nightjar.stream")

_bus: Bus | None = None


class PermanentError(Exception):
    """The message can never be processed (cannot normalise, schema invalid): dead-letter it, no retry."""


class TransientError(Exception):
    """Retry later with back-off (database busy, agent unavailable, timeout)."""


def set_bus(bus: Bus | None) -> None:
    global _bus
    _bus = bus


def current_bus() -> Bus | None:
    return _bus


async def get_bus() -> Bus:
    """The bus for this process (connected and set up on first use)."""
    global _bus
    if _bus is None:
        from nightjar.core.config import get_settings

        s = get_settings()
        if s.bus == "memory":
            bus: Bus = MemoryBus(raw_max_bytes=s.stream_max_bytes_raw)
        else:
            from nightjar.stream.natsbus import NatsBus

            bus = NatsBus(s.nats_url, raw_max_bytes=s.stream_max_bytes_raw)
        bus.max_deliveries_handler = _make_dlq_mover(bus)
        await bus.connect()
        await bus.setup()
        _bus = bus
    return _bus


async def reset_bus() -> None:
    global _bus
    if _bus is not None:
        with contextlib.suppress(Exception):
            await _bus.close()
    _bus = None


# ─────────────────────────────────────────────────────────────────────────────
# Throughput and latency bookkeeping (pipeline_health)
# ─────────────────────────────────────────────────────────────────────────────
_events_seen: deque[float] = deque(maxlen=200_000)
_alert_to_triage: deque[float] = deque(maxlen=2_000)


def note_events(n: int) -> None:
    now = time.monotonic()
    for _ in range(min(n, 10_000)):
        _events_seen.append(now)


def events_per_minute() -> float:
    cutoff = time.monotonic() - 60
    while _events_seen and _events_seen[0] < cutoff:
        _events_seen.popleft()
    return float(len(_events_seen))


def note_alert_to_triage(seconds: float) -> None:
    _alert_to_triage.append(seconds)


def alert_to_triage_samples() -> list[float]:
    return list(_alert_to_triage)


# ─────────────────────────────────────────────────────────────────────────────
# Dead letters
# ─────────────────────────────────────────────────────────────────────────────
_memory_dlq: list[dict[str, Any]] = []  # used when the app database is unavailable (pure unit tests)


async def dead_letter(
    bus: Bus,
    *,
    stream: str,
    consumer: str,
    subject: str,
    data: bytes,
    headers: dict[str, str],
    deliveries: int,
    reason: str,
    seq: int | None,
) -> None:
    hdrs = {
        k: v for k, v in headers.items() if k.startswith("Nj-") and k not in ("Nj-Error",)
    }  # keep provenance, drop dedupe ids
    hdrs.update(
        {
            "Nj-Original-Subject": subject,
            "Nj-Original-Seq": str(seq or ""),
            "Nj-Consumer": consumer,
            "Nj-Deliveries": str(deliveries),
            "Nj-Error": reason[:500],
        }
    )
    try:
        await bus.publish(dlq_subject(stream, consumer), data, headers=hdrs)
    except BusError as exc:  # the DLQ itself is down: keep the register row anyway
        log.warning("dlq_publish_failed", error=str(exc))
    DLQ_MESSAGES.labels(stream=stream).inc()
    org = headers.get("Nj-Org")
    row = {
        "org_id": _uuid_or_none(org),
        "stream": stream,
        "consumer": consumer,
        "subject": subject,
        "original_seq": seq,
        "deliveries": deliveries,
        "reason": reason[:2000],
        "headers": {k: v for k, v in headers.items() if k != "Nats-Msg-Id"},
        "payload": data.decode("utf-8", errors="replace"),
    }
    if not await _store_dead_letter(row):
        row["id"] = len(_memory_dlq) + 1
        row["created_at"] = clock.now()
        row["retried_at"] = None
        _memory_dlq.append(row)
    log.info("dead_lettered", stream=stream, consumer=consumer, subject=subject, reason=reason[:120])


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


async def _store_dead_letter(row: dict[str, Any]) -> bool:
    try:
        from nightjar.db.models.pipeline import DeadLetter
        from nightjar.db.session import session_scope

        async with session_scope() as db:
            db.add(DeadLetter(**row))
        return True
    except Exception as exc:  # no database in this process (unit tests) or schema not migrated
        log.debug("dead_letter_db_unavailable", error=str(exc)[:200])
        return False


def _make_dlq_mover(bus: Bus) -> Callable[[str, str, int, int], Awaitable[None]]:
    async def mover(stream: str, consumer: str, seq: int, deliveries: int) -> None:
        found = await bus.get_msg(stream, seq)
        if not found:
            return
        subject, data, headers = found
        await dead_letter(
            bus,
            stream=stream,
            consumer=consumer,
            subject=subject,
            data=data,
            headers=headers,
            deliveries=deliveries,
            reason=headers.get("Nj-Last-Error") or f"gave up after {deliveries} deliveries",
            seq=seq,
        )

    return mover


def install_dlq_mover(bus: Bus) -> None:
    bus.max_deliveries_handler = _make_dlq_mover(bus)


async def list_dead_letters(*, page: int = 1, page_size: int = 25) -> tuple[list[dict[str, Any]], int]:
    page, page_size = max(1, page), max(1, min(page_size, 200))
    try:
        from sqlalchemy import func, select

        from nightjar.db.models.pipeline import DeadLetter
        from nightjar.db.session import session_scope

        async with session_scope() as db:
            total = int((await db.execute(select(func.count(DeadLetter.id)))).scalar_one())
            rows = (
                (
                    await db.execute(
                        select(DeadLetter)
                        .where(DeadLetter.retried_at.is_(None))
                        .order_by(DeadLetter.id.desc())
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                )
                .scalars()
                .all()
            )
            items = [
                {
                    "seq": r.id,
                    "stream": r.stream,
                    "consumer": r.consumer,
                    "subject": r.subject,
                    "deliveries": r.deliveries,
                    "reason": r.reason,
                    "at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
            return items, total
    except Exception:
        rows_mem = [r for r in reversed(_memory_dlq) if r.get("retried_at") is None]
        items = [
            {
                "seq": r["id"],
                "stream": r["stream"],
                "consumer": r["consumer"],
                "subject": r["subject"],
                "deliveries": r["deliveries"],
                "reason": r["reason"],
                "at": r["created_at"].isoformat(),
            }
            for r in rows_mem
        ]
        start = (page - 1) * page_size
        return items[start : start + page_size], len(rows_mem)


async def retry_dead_letter(seq: int) -> None:
    """Re-publish a dead letter to its original subject (KeyError if unknown or already retried)."""
    bus = await get_bus()
    row: dict[str, Any] | None = None
    try:
        from nightjar.db.models.pipeline import DeadLetter
        from nightjar.db.session import session_scope

        async with session_scope() as db:
            obj = await db.get(DeadLetter, seq)
            if obj is None or obj.retried_at is not None:
                raise KeyError(seq)
            row = {"subject": obj.subject, "payload": obj.payload, "headers": dict(obj.headers or {})}
            obj.retried_at = clock.now()
    except KeyError:
        raise
    except Exception:
        match = next((r for r in _memory_dlq if r["id"] == seq and r.get("retried_at") is None), None)
        if match is None:
            raise KeyError(seq) from None
        match["retried_at"] = clock.now()
        row = {"subject": match["subject"], "payload": match["payload"], "headers": dict(match["headers"])}
    assert row is not None
    headers = {k: v for k, v in row["headers"].items() if k.startswith("Nj-") and k != "Nj-Last-Error"}
    headers["Nj-Retried-From-Dlq"] = str(seq)
    await bus.publish(row["subject"], row["payload"].encode(), headers=headers)


# ─────────────────────────────────────────────────────────────────────────────
# The consumer loop
# ─────────────────────────────────────────────────────────────────────────────
MessageHandler = Callable[[Msg], Awaitable[None]]
BatchHandler = Callable[[Sequence[Msg]], Awaitable[None]]


def _is_permanent(exc: BaseException) -> bool:
    return isinstance(exc, PermanentError | ValueError | KeyError | TypeError | UnicodeDecodeError)


async def handle_one(bus: Bus, consumer: str, msg: Msg, handler: MessageHandler) -> str:
    """Run one handler with the retry/DLQ policy. Returns 'ack' | 'nak' | 'dlq'."""
    spec = CONSUMER_BY_NAME[consumer]
    started = time.perf_counter()
    try:
        await handler(msg)
    except Exception as exc:
        if _is_permanent(exc) and not isinstance(exc, TransientError):
            await dead_letter(
                bus,
                stream=spec.stream,
                consumer=consumer,
                subject=msg.subject,
                data=msg.data,
                headers=msg.headers,
                deliveries=msg.num_delivered,
                reason=f"{type(exc).__name__}: {exc}",
                seq=msg.seq,
            )
            await msg.term()
            return "dlq"
        log.warning("handler_retry", consumer=consumer, error=str(exc)[:200], deliveries=msg.num_delivered)
        if msg.num_delivered >= spec.max_deliver:
            await dead_letter(
                bus,
                stream=spec.stream,
                consumer=consumer,
                subject=msg.subject,
                data=msg.data,
                headers=msg.headers,
                deliveries=msg.num_delivered,
                reason=f"gave up after {msg.num_delivered} deliveries: {type(exc).__name__}: {exc}",
                seq=msg.seq,
            )
            await msg.term()
            return "dlq"
        await msg.nak(delay=spec.delay_for(msg.num_delivered))
        return "nak"
    await msg.ack()
    STAGE_LATENCY.labels(stage=consumer).observe(time.perf_counter() - started)
    return "ack"


async def run_consumer(
    consumer: str,
    handler: MessageHandler,
    *,
    stop: asyncio.Event,
    batch: int = 100,
    fetch_timeout: float = 1.0,
    bus: Bus | None = None,
    concurrency: int = 1,
) -> None:
    """Pull loop for one durable consumer until `stop` is set. Bus outages back off and retry."""
    bus = bus or await get_bus()
    sem = asyncio.Semaphore(max(1, concurrency))
    while not stop.is_set():
        try:
            msgs = await bus.fetch(consumer, batch, fetch_timeout)
        except BusError as exc:
            log.warning("fetch_failed", consumer=consumer, error=str(exc)[:200])
            await asyncio.sleep(1.0)
            continue
        if not msgs:
            continue
        if concurrency <= 1:
            for m in msgs:
                await handle_one(bus, consumer, m, handler)
        else:

            async def _one(m: Msg) -> None:
                async with sem:
                    await handle_one(bus, consumer, m, handler)

            await asyncio.gather(*(_one(m) for m in msgs))


async def run_batch_consumer(
    consumer: str,
    handler: BatchHandler,
    *,
    stop: asyncio.Event,
    batch: int = 500,
    fetch_timeout: float = 1.0,
    bus: Bus | None = None,
) -> None:
    """Batch variant: the batch is acked after the handler returns, nak'ed with back-off if it raises."""
    bus = bus or await get_bus()
    spec = CONSUMER_BY_NAME[consumer]
    while not stop.is_set():
        try:
            msgs = await bus.fetch(consumer, batch, fetch_timeout)
        except BusError:
            await asyncio.sleep(1.0)
            continue
        if not msgs:
            continue
        try:
            await handler(msgs)
        except Exception as exc:
            log.warning("batch_retry", consumer=consumer, error=str(exc)[:200], size=len(msgs))
            for m in msgs:
                if m.num_delivered >= spec.max_deliver:
                    await dead_letter(
                        bus,
                        stream=spec.stream,
                        consumer=consumer,
                        subject=m.subject,
                        data=m.data,
                        headers=m.headers,
                        deliveries=m.num_delivered,
                        reason=f"{type(exc).__name__}: {exc}",
                        seq=m.seq,
                    )
                    await m.term()
                else:
                    await m.nak(delay=spec.delay_for(m.num_delivered))
            continue
        for m in msgs:
            await m.ack()


async def update_lag_metrics(bus: Bus) -> None:
    with contextlib.suppress(Exception):
        for st in await bus.stats():
            for c in st.consumers:
                CONSUMER_LAG.labels(consumer=c.name).set(c.pending + c.ack_pending)
