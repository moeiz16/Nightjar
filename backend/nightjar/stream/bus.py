"""The bus interface and its in-process implementation (`BUS=memory`).

`MemoryBus` implements the same semantics the pipeline relies on from JetStream (ADR 0001), so unit tests and
single-process demos exercise the real handlers: subjects with wildcards, per-stream limits with
`discard=new` backpressure, `Nats-Msg-Id` de-duplication within the duplicate window, durable pull consumers
with independent cursors, explicit ack / nak(delay) / term, redelivery after `ack_wait`, `max_ack_pending`
flow control, `max_deliver` with a max-deliveries callback (the advisory) and work-queue retention.
"""
# ruff: noqa: ASYNC109  (explicit per-call timeouts are part of the bus interface)

from __future__ import annotations

import abc
import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nightjar.contracts import ConsumerStat, StreamStat
from nightjar.stream.topology import CONSUMERS, ConsumerSpec, StreamSpec, streams, subject_matches


class BusError(Exception):
    """The bus is unreachable or refused an operation (transient)."""


class StreamFullError(BusError):
    """The target stream is at its byte/message limit with discard=new."""


@dataclass(frozen=True, slots=True)
class PubAck:
    stream: str
    seq: int
    duplicate: bool = False


class Msg(abc.ABC):
    subject: str
    data: bytes
    headers: dict[str, str]
    seq: int
    num_delivered: int

    @abc.abstractmethod
    async def ack(self) -> None: ...

    @abc.abstractmethod
    async def nak(self, delay: float | None = None) -> None: ...

    @abc.abstractmethod
    async def term(self) -> None: ...

    async def in_progress(self) -> None:  # pragma: no cover - default no-op
        return None


MaxDeliveriesHandler = Callable[[str, str, int, int], Awaitable[None]]  # stream, consumer, seq, deliveries


class Bus(abc.ABC):
    mode: str = "abstract"

    def __init__(self) -> None:
        self.max_deliveries_handler: MaxDeliveriesHandler | None = None

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @property
    @abc.abstractmethod
    def connected(self) -> bool: ...

    @abc.abstractmethod
    async def setup(self) -> None:
        """Create streams and durable consumers (idempotent)."""

    @abc.abstractmethod
    async def publish(
        self, subject: str, data: bytes, *, headers: dict[str, str] | None = None, msg_id: str | None = None
    ) -> PubAck: ...

    @abc.abstractmethod
    async def fetch(self, consumer: str, batch: int, timeout: float) -> list[Msg]: ...

    @abc.abstractmethod
    async def get_msg(self, stream: str, seq: int) -> tuple[str, bytes, dict[str, str]] | None: ...

    @abc.abstractmethod
    async def stats(self) -> tuple[StreamStat, ...]: ...

    @abc.abstractmethod
    async def publish_core(self, subject: str, data: bytes) -> None: ...

    @abc.abstractmethod
    async def subscribe_core(self, subject: str, cb: Callable[[bytes], Awaitable[None]]) -> Any: ...

    async def delete_msg(self, stream: str, seq: int) -> None:  # pragma: no cover - optional
        return None


# ─────────────────────────────────────────────────────────────────────────────
# In-process implementation
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _Stored:
    seq: int
    subject: str
    data: bytes
    headers: dict[str, str]
    at: float


@dataclass
class _Stream:
    spec: StreamSpec
    messages: dict[int, _Stored] = field(default_factory=dict)
    bytes: int = 0
    last_seq: int = 0
    dedupe: dict[str, tuple[int, float]] = field(default_factory=dict)


@dataclass
class _Pending:
    deliveries: int
    deadline: float  # ack_wait expiry or nak'ed redelivery time
    naked: bool = False


@dataclass
class _Consumer:
    spec: ConsumerSpec
    cursor: int = 0  # last stream seq considered for first delivery
    pending: dict[int, _Pending] = field(default_factory=dict)
    redelivered: int = 0
    acked: set[int] = field(default_factory=set)
    active: bool = False  # has fetched at least once (only active readers hold messages in memory)


class _MemMsg(Msg):
    def __init__(self, bus: MemoryBus, consumer: _Consumer, stored: _Stored, deliveries: int) -> None:
        self._bus, self._consumer, self._stored = bus, consumer, stored
        self.subject, self.data, self.headers = stored.subject, stored.data, dict(stored.headers)
        self.seq, self.num_delivered = stored.seq, deliveries
        self._done = False

    async def ack(self) -> None:
        if self._done:
            return
        self._done = True
        self._bus._ack(self._consumer, self.seq)

    async def nak(self, delay: float | None = None) -> None:
        if self._done:
            return
        self._done = True
        await self._bus._nak(self._consumer, self.seq, delay or 0.0)

    async def term(self) -> None:
        if self._done:
            return
        self._done = True
        self._bus._ack(self._consumer, self.seq)

    async def in_progress(self) -> None:
        p = self._consumer.pending.get(self.seq)
        if p:
            p.deadline = time.monotonic() + self._consumer.spec.ack_wait_s


class MemoryBus(Bus):
    mode = "memory"

    def __init__(self, *, raw_max_bytes: int = 256 * 1024 * 1024, time_scale: float = 1.0) -> None:
        super().__init__()
        self._streams: dict[str, _Stream] = {}
        self._consumers: dict[str, _Consumer] = {}
        self._core: dict[str, list[Callable[[bytes], Awaitable[None]]]] = {}
        self._cond = asyncio.Condition()
        self._connected = False
        self._raw_max_bytes = raw_max_bytes
        # Tests shrink back-off/ack-wait delays (0.01 → 100x faster) without changing the topology.
        self.time_scale = time_scale

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def setup(self) -> None:
        for spec in streams(self._raw_max_bytes):
            self._streams.setdefault(spec.name, _Stream(spec))
        for c in CONSUMERS:
            self._consumers.setdefault(c.name, _Consumer(c))

    def activate(self, *consumers: str) -> None:
        """Mark consumers as readers from now on (messages are kept until they ack), before their first fetch."""
        for name in consumers:
            if name in self._consumers:
                self._consumers[name].active = True

    def add_stream(self, spec: StreamSpec) -> None:
        self._streams.setdefault(spec.name, _Stream(spec))

    def add_consumer(self, spec: ConsumerSpec) -> None:
        self._consumers.setdefault(spec.name, _Consumer(spec))

    def _stream_for(self, subject: str) -> _Stream:
        for s in self._streams.values():
            if any(subject_matches(p, subject) for p in s.spec.subjects):
                return s
        raise BusError(f"no stream for subject {subject}")

    async def publish(
        self, subject: str, data: bytes, *, headers: dict[str, str] | None = None, msg_id: str | None = None
    ) -> PubAck:
        if not self._connected:
            raise BusError("bus offline")
        st = self._stream_for(subject)
        now = time.monotonic()
        if msg_id:
            hit = st.dedupe.get(msg_id)
            if hit and now - hit[1] < st.spec.duplicate_window_s:
                return PubAck(st.spec.name, hit[0], duplicate=True)
        self._expire(st, now)
        size = len(data) + len(subject) + sum(len(k) + len(v) for k, v in (headers or {}).items())
        if st.spec.max_bytes > 0 and st.bytes + size > st.spec.max_bytes:
            if st.spec.discard == "new":
                raise StreamFullError(f"{st.spec.name}: maximum bytes exceeded")
            while st.messages and st.bytes + size > st.spec.max_bytes:
                first = min(st.messages)
                st.bytes -= len(st.messages.pop(first).data)
        st.last_seq += 1
        st.messages[st.last_seq] = _Stored(st.last_seq, subject, data, dict(headers or {}), now)
        st.bytes += size
        if msg_id:
            st.dedupe[msg_id] = (st.last_seq, now)
        async with self._cond:
            self._cond.notify_all()
        return PubAck(st.spec.name, st.last_seq)

    def _expire(self, st: _Stream, now: float) -> None:
        if len(st.dedupe) > 50_000:
            st.dedupe = {k: v for k, v in st.dedupe.items() if now - v[1] < st.spec.duplicate_window_s}

    def _ack(self, c: _Consumer, seq: int) -> None:
        c.pending.pop(seq, None)
        st = self._streams[c.spec.stream]
        if st.spec.retention == "workqueue":
            stored = st.messages.pop(seq, None)
            if stored:
                st.bytes -= len(stored.data)
        else:
            # JetStream keeps limits-stream messages until max_age/max_bytes. In process we free a message once
            # every active reader of the stream has acked it, which bounds memory and keeps the raw intake's
            # discard=new backpressure meaningful (a slow stage fills it, a drained pipeline empties it).
            c.acked.add(seq)
            readers = [x for x in self._consumers.values() if x.spec.stream == st.spec.name and x.active]
            if all(seq in x.acked for x in readers):
                stored = st.messages.pop(seq, None)
                if stored:
                    st.bytes -= len(stored.data)
                for x in readers:
                    x.acked.discard(seq)

    async def _nak(self, c: _Consumer, seq: int, delay: float) -> None:
        p = c.pending.get(seq)
        if p is None:
            return
        p.deadline = time.monotonic() + delay * self.time_scale
        p.naked = True
        async with self._cond:
            self._cond.notify_all()

    def _matches(self, c: _Consumer, subject: str) -> bool:
        return any(subject_matches(f, subject) for f in c.spec.filter_subjects)

    async def _collect(self, c: _Consumer, batch: int) -> tuple[list[Msg], list[tuple[int, int]]]:
        st = self._streams[c.spec.stream]
        now = time.monotonic()
        out: list[Msg] = []
        exhausted: list[tuple[int, int]] = []
        # 1. redeliveries that are due (nak delay elapsed or ack_wait expired)
        for seq, p in sorted(c.pending.items()):
            if len(out) >= batch:
                break
            if p.deadline > now:
                continue
            stored = st.messages.get(seq)
            if stored is None:
                c.pending.pop(seq, None)
                continue
            if p.deliveries >= c.spec.max_deliver:
                c.pending.pop(seq, None)
                exhausted.append((seq, p.deliveries))
                continue
            p.deliveries += 1
            p.naked = False
            p.deadline = now + c.spec.ack_wait_s * self.time_scale
            c.redelivered += 1
            out.append(_MemMsg(self, c, stored, p.deliveries))
        # 2. first deliveries, bounded by max_ack_pending
        if len(out) < batch:
            for seq in range(c.cursor + 1, st.last_seq + 1):
                if len(out) >= batch or len(c.pending) >= c.spec.max_ack_pending:
                    break
                c.cursor = seq
                stored = st.messages.get(seq)
                if stored is None:
                    continue
                if not self._matches(c, stored.subject):
                    continue
                c.pending[seq] = _Pending(1, now + c.spec.ack_wait_s * self.time_scale)
                out.append(_MemMsg(self, c, stored, 1))
        return out, exhausted

    async def fetch(self, consumer: str, batch: int, timeout: float) -> list[Msg]:
        if not self._connected:
            raise BusError("bus offline")
        c = self._consumers[consumer]
        c.active = True
        deadline = time.monotonic() + timeout
        while True:
            out, exhausted = await self._collect(c, batch)
            for seq, deliveries in exhausted:
                if self.max_deliveries_handler:
                    await self.max_deliveries_handler(c.spec.stream, c.spec.name, seq, deliveries)
                self._ack(c, seq)  # the consumer gives up on it (JetStream stops delivering it too)
            if out:
                return out
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            wait = remaining
            due = [p.deadline for p in c.pending.values()]
            if due:
                wait = max(0.0, min(wait, min(due) - time.monotonic()))
            try:
                async with self._cond:
                    await asyncio.wait_for(self._cond.wait(), timeout=max(wait, 0.001))
            except TimeoutError:
                pass

    def pending_count(self, consumer: str) -> int:
        """Messages not yet delivered plus delivered-but-unacked, for this consumer."""
        c = self._consumers[consumer]
        st = self._streams[c.spec.stream]
        undelivered = sum(
            1
            for s in range(c.cursor + 1, st.last_seq + 1)
            if (m := st.messages.get(s)) is not None and self._matches(c, m.subject)
        )
        return undelivered + len(c.pending)

    def idle(self, consumers: list[str] | None = None) -> bool:
        names = consumers or list(self._consumers)
        return all(self.pending_count(n) == 0 for n in names if n in self._consumers)

    async def get_msg(self, stream: str, seq: int) -> tuple[str, bytes, dict[str, str]] | None:
        stored = self._streams[stream].messages.get(seq)
        return (stored.subject, stored.data, dict(stored.headers)) if stored else None

    async def delete_msg(self, stream: str, seq: int) -> None:
        st = self._streams[stream]
        stored = st.messages.pop(seq, None)
        if stored:
            st.bytes -= len(stored.data)

    def stream_messages(self, stream: str) -> list[tuple[int, str, bytes, dict[str, str]]]:
        st = self._streams[stream]
        return [(s, m.subject, m.data, dict(m.headers)) for s, m in sorted(st.messages.items())]

    async def stats(self) -> tuple[StreamStat, ...]:
        out = []
        for st in self._streams.values():
            consumers = tuple(
                ConsumerStat(
                    name=c.spec.name,
                    pending=self.pending_count(c.spec.name) - len(c.pending),
                    ack_pending=len(c.pending),
                    redelivered=c.redelivered,
                )
                for c in self._consumers.values()
                if c.spec.stream == st.spec.name
            )
            out.append(StreamStat(st.spec.name, len(st.messages), st.bytes, consumers))
        return tuple(out)

    async def publish_core(self, subject: str, data: bytes) -> None:
        for pattern, cbs in list(self._core.items()):
            if subject_matches(pattern, subject):
                for cb in cbs:
                    await cb(data)

    async def subscribe_core(self, subject: str, cb: Callable[[bytes], Awaitable[None]]) -> Any:
        self._core.setdefault(subject, []).append(cb)
        return (subject, cb)
