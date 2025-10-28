"""NATS JetStream implementation of the bus (`BUS=nats`, ADR 0001). Verified patterns: scripts/smoke/nats_smoke.py.

* streams/consumers are created or updated idempotently from `topology`;
* publishes await stream acks (at most 256 in flight, 2 s budget); a full `NJ_RAW` (discard=new) raises
  `StreamFullError`, which the public API turns into `BackpressureError` → 503 `intake_full`;
* pull subscriptions are bound to the durable consumers; `nak(delay)` uses the consumer's back-off list;
* the MAX_DELIVERIES advisory is routed to `max_deliveries_handler` (the DLQ mover).
"""
# ruff: noqa: ASYNC109  (explicit per-call timeouts are part of the bus interface)

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import nats
from nats.aio.client import Client as NatsClient
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    DiscardPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import APIError, NotFoundError

from nightjar.contracts import ConsumerStat, StreamStat
from nightjar.stream.bus import Bus, BusError, Msg, PubAck, StreamFullError
from nightjar.stream.topology import CONSUMER_BY_NAME, CONSUMERS, ConsumerSpec, StreamSpec, streams

_FULL_CODES = {10047, 10077, 10078}  # maximum bytes / messages exceeded


class _NatsMsg(Msg):
    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.subject = raw.subject
        self.data = raw.data or b""
        self.headers = dict(raw.headers or {})
        md = raw.metadata
        self.seq = int(md.sequence.stream)
        self.num_delivered = int(md.num_delivered)

    async def ack(self) -> None:
        await self._raw.ack()

    async def nak(self, delay: float | None = None) -> None:
        await self._raw.nak(delay=delay)

    async def term(self) -> None:
        await self._raw.term()

    async def in_progress(self) -> None:
        await self._raw.in_progress()


class NatsBus(Bus):
    mode = "nats"

    def __init__(self, url: str, *, raw_max_bytes: int = 256 * 1024 * 1024, name: str = "nightjar") -> None:
        super().__init__()
        self.url = url
        self.name = name
        self._raw_max_bytes = raw_max_bytes
        self._nc: NatsClient | None = None
        self._js: Any = None
        self._subs: dict[str, Any] = {}
        self._inflight = asyncio.Semaphore(256)
        self._advisory: Any = None

    async def connect(self) -> None:
        if self._nc and self._nc.is_connected:
            return
        try:
            self._nc = await nats.connect(
                self.url, name=self.name, connect_timeout=2, max_reconnect_attempts=-1, reconnect_time_wait=1
            )
        except Exception as exc:
            raise BusError(f"cannot reach the event bus at {self.url}: {exc}") from exc
        self._js = self._nc.jetstream(timeout=2.0)

    async def close(self) -> None:
        for sub in self._subs.values():  # pull subscriptions: unsubscribe, never drain (ADR 0001)
            try:
                await sub.unsubscribe()
            except Exception:  # noqa: S112 - best effort on shutdown
                continue
        self._subs.clear()
        if self._nc:
            try:
                await self._nc.close()
            finally:
                self._nc = None

    @property
    def connected(self) -> bool:
        return bool(self._nc and self._nc.is_connected)

    def _stream_config(self, spec: StreamSpec) -> StreamConfig:
        return StreamConfig(
            name=spec.name,
            subjects=list(spec.subjects),
            retention=RetentionPolicy.WORK_QUEUE if spec.retention == "workqueue" else RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=float(spec.max_age_s),
            max_bytes=spec.max_bytes,
            discard=DiscardPolicy.NEW if spec.discard == "new" else DiscardPolicy.OLD,
            duplicate_window=float(spec.duplicate_window_s),
        )

    @staticmethod
    def _consumer_config(spec: ConsumerSpec) -> ConsumerConfig:
        kwargs: dict[str, Any] = {
            "durable_name": spec.name,
            "ack_policy": AckPolicy.EXPLICIT,
            "deliver_policy": DeliverPolicy.ALL,
            "ack_wait": float(spec.ack_wait_s),
            "max_deliver": spec.max_deliver,
            "max_ack_pending": spec.max_ack_pending,
        }
        if len(spec.filter_subjects) == 1:
            kwargs["filter_subject"] = spec.filter_subjects[0]
        else:
            kwargs["filter_subjects"] = list(spec.filter_subjects)
        return ConsumerConfig(**kwargs)

    async def setup(self) -> None:
        await self.connect()
        for spec in streams(self._raw_max_bytes):
            cfg = self._stream_config(spec)
            try:
                await self._js.stream_info(spec.name)
                await self._js.update_stream(cfg)
            except NotFoundError:
                await self._js.add_stream(cfg)
            except APIError:
                await self._js.add_stream(cfg)
        for c in CONSUMERS:
            await self.ensure_consumer(c)

    async def ensure_consumer(self, spec: ConsumerSpec) -> None:
        try:
            await self._js.add_consumer(spec.stream, self._consumer_config(spec))
        except APIError as exc:  # existing durable with a different config: recreate it
            if "already" not in str(exc).lower() and getattr(exc, "err_code", 0) != 10148:
                raise
            await self._js.delete_consumer(spec.stream, spec.name)
            await self._js.add_consumer(spec.stream, self._consumer_config(spec))

    async def publish(
        self, subject: str, data: bytes, *, headers: dict[str, str] | None = None, msg_id: str | None = None
    ) -> PubAck:
        if not self.connected:
            raise BusError("event bus offline")
        hdrs = dict(headers or {})
        if msg_id:
            hdrs["Nats-Msg-Id"] = msg_id
        async with self._inflight:
            try:
                ack = await asyncio.wait_for(self._js.publish(subject, data, headers=hdrs or None), timeout=2.0)
            except APIError as exc:
                if getattr(exc, "err_code", None) in _FULL_CODES or "maximum" in str(exc).lower():
                    raise StreamFullError(str(exc)) from exc
                raise BusError(str(exc)) from exc
            except (TimeoutError, nats.errors.TimeoutError, nats.errors.NoRespondersError) as exc:
                raise BusError(f"publish timed out on {subject}") from exc
        return PubAck(ack.stream, int(ack.seq), bool(getattr(ack, "duplicate", False)))

    async def _sub(self, consumer: str) -> Any:
        sub = self._subs.get(consumer)
        if sub is None:
            spec = CONSUMER_BY_NAME[consumer]
            sub = await self._js.pull_subscribe_bind(durable=spec.name, stream=spec.stream)
            self._subs[consumer] = sub
        return sub

    async def fetch(self, consumer: str, batch: int, timeout: float) -> list[Msg]:
        if not self.connected:
            raise BusError("event bus offline")
        sub = await self._sub(consumer)
        try:
            msgs = await sub.fetch(batch, timeout=timeout)
        except (TimeoutError, nats.errors.TimeoutError):
            return []
        return [_NatsMsg(m) for m in msgs]

    async def get_msg(self, stream: str, seq: int) -> tuple[str, bytes, dict[str, str]] | None:
        try:
            m = await self._js.get_msg(stream, seq)
        except (NotFoundError, APIError):
            return None
        return m.subject or "", m.data or b"", dict(m.headers or {})

    async def delete_msg(self, stream: str, seq: int) -> None:
        try:
            await self._js.delete_msg(stream, seq)
        except (NotFoundError, APIError):
            return

    async def stats(self) -> tuple[StreamStat, ...]:
        out = []
        for spec in streams(self._raw_max_bytes):
            try:
                info = await self._js.stream_info(spec.name)
            except Exception:  # noqa: S112 - missing stream: skip
                continue
            consumers = []
            for c in CONSUMERS:
                if c.stream != spec.name:
                    continue
                try:
                    ci = await self._js.consumer_info(spec.name, c.name)
                except Exception:  # noqa: S112
                    continue
                consumers.append(
                    ConsumerStat(
                        name=c.name,
                        pending=int(ci.num_pending or 0),
                        ack_pending=int(ci.num_ack_pending or 0),
                        redelivered=int(ci.num_redelivered or 0),
                    )
                )
            out.append(StreamStat(spec.name, int(info.state.messages), int(info.state.bytes), tuple(consumers)))
        return tuple(out)

    async def publish_core(self, subject: str, data: bytes) -> None:
        if self._nc:
            await self._nc.publish(subject, data)

    async def subscribe_core(self, subject: str, cb: Callable[[bytes], Awaitable[None]]) -> Any:
        if not self._nc:
            raise BusError("event bus offline")

        async def _handler(msg: Any) -> None:
            await cb(msg.data)

        return await self._nc.subscribe(subject, cb=_handler)

    async def watch_max_deliveries(self) -> None:
        """Route `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<stream>.<consumer>` to the DLQ mover."""
        if not self._nc or self._advisory is not None:
            return

        async def _on(msg: Any) -> None:
            if not self.max_deliveries_handler:
                return
            adv = json.loads(msg.data)
            await self.max_deliveries_handler(
                adv["stream"], adv["consumer"], int(adv["stream_seq"]), int(adv.get("deliveries", 0))
            )

        self._advisory = await self._nc.subscribe("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*", cb=_on)
