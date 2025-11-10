"""The in-process bus implements the JetStream semantics the pipeline relies on (ADR 0001)."""

from __future__ import annotations

import asyncio

import pytest

from nightjar.stream.bus import BusError, MemoryBus, StreamFullError
from nightjar.stream.runtime import PermanentError, TransientError, handle_one, list_dead_letters, retry_dead_letter
from nightjar.stream.topology import CONSUMER_BY_NAME, CONSUMERS, StreamSpec, streams, subject_matches


@pytest.mark.parametrize(
    ("pattern", "subject", "ok"),
    [
        ("nj.raw.>", "nj.raw.org.webhook", True),
        ("nj.raw.>", "nj.raw", False),
        ("nj.incidents.*.opened", "nj.incidents.org.opened", True),
        ("nj.incidents.*.opened", "nj.incidents.org.updated", False),
        ("nj.events.normalized.>", "nj.events.normalized.o.windows_sysmon", True),
        ("a.*", "a.b.c", False),
    ],
)
def test_subject_matching(pattern: str, subject: str, ok: bool) -> None:
    assert subject_matches(pattern, subject) is ok


def test_topology_matches_the_contract() -> None:
    names = {s.name for s in streams()}
    assert names == {"NJ_RAW", "NJ_EVENTS", "NJ_DETECTIONS", "NJ_INCIDENTS", "NJ_WORK", "NJ_DLQ"}
    assert len(CONSUMERS) == 10
    raw = next(s for s in streams() if s.name == "NJ_RAW")
    assert raw.discard == "new" and raw.duplicate_window_s == 120
    assert next(s for s in streams() if s.name == "NJ_WORK").retention == "workqueue"
    assert CONSUMER_BY_NAME["orchestrator"].max_ack_pending == 4
    assert CONSUMER_BY_NAME["normalizer"].delay_for(1) == 1 and CONSUMER_BY_NAME["normalizer"].delay_for(9) == 120


async def test_publish_assigns_sequences_and_dedupes(bus: MemoryBus) -> None:
    a = await bus.publish("nj.raw.o.webhook", b"1", msg_id="x")
    b = await bus.publish("nj.raw.o.webhook", b"2", msg_id="y")
    dup = await bus.publish("nj.raw.o.webhook", b"1", msg_id="x")
    assert (a.seq, b.seq) == (1, 2) and dup.duplicate and dup.seq == 1


async def test_full_raw_stream_refuses_new_messages() -> None:
    small = MemoryBus(raw_max_bytes=4096)
    await small.connect()
    await small.setup()
    accepted = 0
    with pytest.raises(StreamFullError):  # noqa: PT012
        for _ in range(200):
            await small.publish("nj.raw.o.webhook", b"x" * 256)
            accepted += 1
    assert 10 <= accepted <= 15


async def test_offline_bus_raises() -> None:
    b = MemoryBus()
    with pytest.raises(BusError):
        await b.publish("nj.raw.o.webhook", b"x")


async def test_durable_consumers_have_independent_cursors(bus: MemoryBus) -> None:
    for i in range(3):
        await bus.publish(f"nj.events.normalized.o.ds{i}", str(i).encode())
    d = await bus.fetch("detector", 10, 0.01)
    u = await bus.fetch("ueba", 10, 0.01)
    assert [m.data for m in d] == [b"0", b"1", b"2"] == [m.data for m in u]
    for m in d:
        await m.ack()
    assert await bus.fetch("detector", 10, 0.01) == []


async def test_nak_redelivers_and_counts_deliveries(bus: MemoryBus) -> None:
    await bus.publish("nj.raw.o.webhook", b"x")
    (m,) = await bus.fetch("normalizer", 1, 0.01)
    await m.nak(delay=0)
    (again,) = await bus.fetch("normalizer", 1, 0.5)
    assert again.seq == m.seq and again.num_delivered == 2


async def test_ack_wait_expiry_redelivers() -> None:
    b = MemoryBus(time_scale=0.0005)  # ack_wait 30 s → 15 ms
    await b.connect()
    await b.setup()
    await b.publish("nj.raw.o.webhook", b"x")
    (m,) = await b.fetch("normalizer", 1, 0.01)
    await asyncio.sleep(0.03)
    (again,) = await b.fetch("normalizer", 1, 0.2)
    assert again.num_delivered == 2 and m.seq == again.seq


async def test_max_ack_pending_bounds_in_flight(bus: MemoryBus) -> None:
    for i in range(10):
        await bus.publish(f"nj.agents.tasks.o{i}", b"t")
    first = await bus.fetch("orchestrator", 10, 0.01)
    assert len(first) == 4  # ≤ 4 investigations in flight
    assert await bus.fetch("orchestrator", 10, 0.01) == []
    await first[0].ack()
    assert len(await bus.fetch("orchestrator", 10, 0.01)) == 1


async def test_work_queue_removes_acked_messages(bus: MemoryBus) -> None:
    await bus.publish("nj.jobs.replay", b"j")
    (m,) = await bus.fetch("jobs", 1, 0.01)
    await m.ack()
    assert bus.stream_messages("NJ_WORK") == []


async def test_max_deliveries_moves_message_to_dlq(bus: MemoryBus) -> None:
    await bus.publish("nj.incidents.o.opened", b"poison", headers={"Nj-Org": "o"})
    for _ in range(10):
        msgs = await bus.fetch("dispatcher", 1, 0.05)
        for m in msgs:
            await m.nak(delay=0)
    items, total = await list_dead_letters()
    assert total == 1 and items[0]["consumer"] == "dispatcher" and items[0]["deliveries"] == 5
    assert len(bus.stream_messages("NJ_DLQ")) == 1


async def test_handle_one_dead_letters_permanent_errors(bus: MemoryBus) -> None:
    await bus.publish("nj.raw.o.webhook", b"bad", headers={"Nj-Org": "o"})
    (m,) = await bus.fetch("normalizer", 1, 0.01)

    async def boom(_: object) -> None:
        raise PermanentError("cannot normalise")

    assert await handle_one(bus, "normalizer", m, boom) == "dlq"
    (dl,) = bus.stream_messages("NJ_DLQ")
    assert dl[3]["Nj-Error"].startswith("PermanentError") and dl[3]["Nj-Original-Subject"] == "nj.raw.o.webhook"


async def test_handle_one_naks_transient_errors_then_gives_up(bus: MemoryBus) -> None:
    await bus.publish("nj.raw.o.webhook", b"x")

    async def busy(_: object) -> None:
        raise TransientError("database busy")

    outcomes = []
    for _ in range(8):
        for m in await bus.fetch("normalizer", 1, 0.3):
            outcomes.append(await handle_one(bus, "normalizer", m, busy))
        if outcomes and outcomes[-1] == "dlq":
            break
    assert outcomes[:4] == ["nak"] * 4 and outcomes[-1] == "dlq"


async def test_retry_dead_letter_republishes(bus: MemoryBus) -> None:
    await bus.publish("nj.raw.o.webhook", b"x", headers={"Nj-Org": "o"})
    (m,) = await bus.fetch("normalizer", 1, 0.01)

    async def boom(_: object) -> None:
        raise ValueError("unreadable timestamp")

    await handle_one(bus, "normalizer", m, boom)
    items, _ = await list_dead_letters()
    await retry_dead_letter(items[0]["seq"])
    (again,) = await bus.fetch("normalizer", 1, 0.01)
    assert again.data == b"x" and again.headers["Nj-Retried-From-Dlq"] == str(items[0]["seq"])
    with pytest.raises(KeyError):
        await retry_dead_letter(items[0]["seq"])


async def test_stats_report_pending_per_consumer(bus: MemoryBus) -> None:
    for _ in range(3):
        await bus.publish("nj.raw.o.webhook", b"x")
    stats = {s.name: s for s in await bus.stats()}
    norm = next(c for c in stats["NJ_RAW"].consumers if c.name == "normalizer")
    assert stats["NJ_RAW"].messages == 3 and norm.pending == 3


async def test_core_control_subjects(bus: MemoryBus) -> None:
    got: list[bytes] = []

    async def cb(data: bytes) -> None:
        got.append(data)

    await bus.subscribe_core("nj.control.rules", cb)
    await bus.publish_core("nj.control.rules", b"reload")
    assert got == [b"reload"]


def test_unknown_subject_has_no_stream() -> None:
    b = MemoryBus()
    b.add_stream(StreamSpec("X", ("x.>",), 60))
    with pytest.raises(BusError):
        b._stream_for("y.z")
