"""The stream API A calls (contract Part 3): intake acks, backpressure, subscriptions, health, scenarios, jobs."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from nightjar.contracts import NormalizedEvent, RawEvent
from nightjar.stream import api
from nightjar.stream.bus import MemoryBus
from nightjar.stream.runtime import set_bus
from tests.pipeline_support import T0, raw, vpn_fail


async def test_publish_raw_acks_duplicates_and_rejects(bus: MemoryBus) -> None:
    good = raw(vpn_fail("a.ng", "203.0.113.50", T0))
    ack = await api.publish_raw([good, good, RawEvent(good.org_id, "webhook", T0, {}, None)])
    assert (ack.accepted, ack.duplicates, ack.rejected) == (1, 1, 1)
    assert ack.first_seq == ack.last_seq == 1 and ack.errors == ("2: event must be a JSON object",)


async def test_dedupe_key_wins_over_payload_hash(bus: MemoryBus) -> None:
    a = RawEvent(uuid.uuid4(), "webhook", T0, {"x": 1}, None, None, "k1")
    b = RawEvent(a.org_id, "webhook", T0, {"x": 2}, None, None, "k1")
    ack = await api.publish_raw([a, b])
    assert (ack.accepted, ack.duplicates) == (1, 1)


async def test_oversized_events_are_rejected_per_item(bus: MemoryBus) -> None:
    ack = await api.publish_raw([raw({"message": "x" * 300_000, "host": "h", "program": "p"})])
    assert ack.rejected == 1 and "256 KB" in ack.errors[0]


async def test_full_intake_raises_backpressure() -> None:
    small = MemoryBus(raw_max_bytes=2048)
    await small.connect()
    await small.setup()
    set_bus(small)
    with pytest.raises(api.BackpressureError) as exc:  # noqa: PT012
        for i in range(100):
            await api.publish_raw([raw({"host": "h", "program": "p", "message": f"{i} " + "y" * 200})])
    assert exc.value.retry_after_s > 0


async def test_offline_bus_is_backpressure_too() -> None:
    offline = MemoryBus()
    await offline.setup()
    set_bus(offline)
    with pytest.raises(api.BackpressureError):
        await api.publish_raw([raw({"host": "h", "program": "p", "message": "m"})])


async def test_subscribe_normalized_delivers_batches(bus: MemoryBus) -> None:
    from nightjar.stream import codec
    from nightjar.stream.topology import events_subject
    from tests.pipeline_support import norm

    got: list[NormalizedEvent] = []
    done = asyncio.Event()

    async def handler(events: list[NormalizedEvent]) -> None:
        got.extend(events)
        done.set()

    sub = await api.subscribe_normalized("store-writer", handler, batch=10, max_wait_s=0.05)
    e = norm(vpn_fail("a.ng", "203.0.113.50", T0))
    await bus.publish(events_subject(e.org_id, e.dataset), codec.dumps(e))
    await asyncio.wait_for(done.wait(), 2)
    await sub.stop()
    assert got == [e]
    with pytest.raises(ValueError, match="unknown durable"):
        await api.subscribe_normalized("nope", handler)


async def test_pipeline_health_reports_streams_and_status(bus: MemoryBus) -> None:
    h = await api.pipeline_health()
    assert h.mode == "memory" and h.connected and h.status == "normal"
    assert h.plain_status == "Taking in events normally" and len(h.streams) == 6


async def test_pipeline_health_offline() -> None:
    offline = MemoryBus()
    await offline.setup()
    set_bus(offline)
    h = await api.pipeline_health()
    assert h.status == "offline" and not h.connected


def test_list_scenarios_has_the_bundled_night() -> None:
    (night,) = [s for s in api.list_scenarios() if s.id == "halden-night"]
    assert night.source == "synthetic" and night.incidents_expected == 6 and 5000 < night.events < 7000


async def test_start_replay_enqueues_a_job(bus: MemoryBus) -> None:
    job = uuid.uuid4()
    await api.start_replay(uuid.uuid4(), "halden-night", speed=60, job_id=job)
    (_, subject, data, _) = bus.stream_messages("NJ_WORK")[0]
    assert subject == "nj.jobs.replay" and str(job).encode() in data
    with pytest.raises(KeyError):
        await api.start_replay(uuid.uuid4(), "nope", speed=1, job_id=uuid.uuid4())


async def test_dlq_listing_and_retry_via_api(bus: MemoryBus) -> None:
    from nightjar.stream.runtime import dead_letter

    await dead_letter(
        bus,
        stream="NJ_RAW",
        consumer="normalizer",
        subject="nj.raw.o.syslog",
        data=b"{}",
        headers={"Nj-Org": str(uuid.uuid4())},
        deliveries=1,
        reason="ValueError: bad",
        seq=7,
    )
    items, total = await api.list_dlq(page=1, page_size=10)
    assert total == 1 and items[0]["reason"] == "ValueError: bad"
    await api.retry_dlq(items[0]["seq"])
    assert (await bus.fetch("normalizer", 1, 0.01))[0].data == b"{}"
