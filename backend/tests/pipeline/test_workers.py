"""Pipeline stages and jobs on the in-process bus, plus the same backbone against a real nats-server."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest

from nightjar.stream import codec
from nightjar.stream.bus import MemoryBus
from nightjar.workers.inline import _drain
from nightjar.workers.pipeline import Pipeline
from tests.pipeline_support import T0, raw, sysmon_proc, vpn_fail

NATS = Path(__file__).resolve().parents[3] / ".tools" / "nats-server"


async def test_raw_to_scored_detection_through_the_stages(bus: MemoryBus, platform) -> None:
    from nightjar.stream.api import publish_raw

    payload = sysmon_proc(
        "C:\\Windows\\System32\\vssadmin.exe",
        "vssadmin.exe delete shadows /all /quiet",
        host="ant-wms-01",
        user="NT AUTHORITY\\SYSTEM",
    )
    await publish_raw([raw(payload)])
    p = Pipeline(bus, auto_investigate=False)
    await _drain(p, ("normalizer", "detector", "scorer", "correlator"), store=None)
    dets = list(platform.detections.values())
    assert dets and all(d.triage is not None for d in dets)
    assert {d.rule_title for d in dets} >= {"Shadow Copies Deletion Using Operating Systems Utilities"}
    assert p.created  # critical impact technique on a crown jewel opens an incident


async def test_redelivered_event_is_not_detected_twice(bus: MemoryBus, platform) -> None:
    from nightjar.stream.api import publish_raw

    payload = sysmon_proc("C:\\x\\af.exe", "af.exe -f objectcategory=computer", host="ant-dc-01")
    await publish_raw([raw(payload)])
    await publish_raw([raw(payload)])  # duplicate within the window: acknowledged, not stored twice
    p = Pipeline(bus, auto_investigate=False)
    await _drain(p, ("normalizer", "detector", "scorer"), store=None)
    assert p.counts["normalized"] == 1
    n = len(platform.detections)
    for d in list(platform.detections.values()):  # replaying scored detections is idempotent on id
        await platform.record_detections(d.org_id, [d])
    assert len(platform.detections) == n


async def test_unparseable_event_is_dead_lettered(bus: MemoryBus) -> None:
    from nightjar.stream.api import list_dlq, publish_raw

    await publish_raw([raw({"host": "h", "program": "fortigate", "timestamp": "32/13/2025 25:61:00", "message": "x"})])
    await _drain(Pipeline(bus), ("normalizer",), store=None)
    items, total = await list_dlq()
    assert total == 1 and "unreadable timestamp" in items[0]["reason"]


async def test_store_writer_receives_every_normalised_event(bus: MemoryBus) -> None:
    from nightjar.stream.api import publish_raw

    stored = []

    async def store(events):
        stored.extend(events)

    bus.activate("store-writer")
    await publish_raw([raw(vpn_fail(f"u{i}", "203.0.113.50", T0)) for i in range(5)])
    await _drain(Pipeline(bus), ("normalizer",), store=store)
    assert len(stored) == 5


@pytest.mark.parametrize("kind", ["jsonl", "json", "csv", "zip"])
async def test_file_replay_formats(bus: MemoryBus, tmp_path: Path, kind: str) -> None:
    from nightjar.workers.jobs import replay_file

    rows = [vpn_fail(f"u{i}", "203.0.113.51", T0) for i in range(3)]
    path = tmp_path / f"events.{kind if kind != 'zip' else 'zip'}"
    if kind == "jsonl":
        path.write_text("\n".join(json.dumps(r) for r in rows))
    elif kind == "json":
        path.write_text(json.dumps(rows))
    elif kind == "csv":
        path.write_text(
            "Event Name,Source IP,Event DateTime,Request Parameters\n"
            + "\n".join(f"ListObjects,198.51.100.{i},{T0.isoformat()},\"{{'bucketName': 'b'}}\"" for i in range(3))
        )
    else:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("events.jsonl", "\n".join(json.dumps(r) for r in rows))
    out = await replay_file({"orgId": str(uuid.uuid4()), "path": str(path), "speed": 0, "retime": True}, None)
    assert out["accepted"] == 3


async def test_scenario_replay_job_publishes_the_bundle(bus: MemoryBus) -> None:
    from nightjar.workers.jobs import run_job

    await run_job(
        {
            "kind": "replay",
            "jobId": None,
            "payload": {"orgId": str(uuid.uuid4()), "scenarioId": "halden-night", "speed": 0},
        },
        None,
    )
    assert len(bus.stream_messages("NJ_RAW")) > 5000


def test_night_anchor_follows_the_contract() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from nightjar.workers.replay.loader import night_anchor

    bxl = ZoneInfo("Europe/Brussels")
    assert night_anchor(datetime(2025, 11, 20, 10, 0, tzinfo=bxl)) == datetime(2025, 11, 20, 0, 0, tzinfo=bxl)
    assert night_anchor(datetime(2025, 11, 20, 8, 0, tzinfo=bxl)) == datetime(2025, 11, 19, 0, 0, tzinfo=bxl)


# ── real nats-server ─────────────────────────────────────────────────────────
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def nats_url(tmp_path: Path):
    if not NATS.exists():
        pytest.skip("nats-server not downloaded (make tools)")
    port = _free_port()
    proc = subprocess.Popen(  # noqa: ASYNC220
        [str(NATS), "-js", "-sd", str(tmp_path / "js"), "-a", "127.0.0.1", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        await asyncio.sleep(0.05)
    yield f"nats://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=10)


@pytest.mark.nats
async def test_nats_backbone_end_to_end(nats_url: str) -> None:
    from nightjar.stream.natsbus import NatsBus
    from nightjar.stream.runtime import handle_one, install_dlq_mover, list_dead_letters

    bus = NatsBus(nats_url, raw_max_bytes=64 * 1024)
    await bus.setup()
    install_dlq_mover(bus)
    await bus.watch_max_deliveries()
    try:
        ack = await bus.publish("nj.raw.o.webhook", codec.dumps(raw(vpn_fail("a", "203.0.113.50", T0))), msg_id="m1")
        dup = await bus.publish("nj.raw.o.webhook", b"{}", msg_id="m1")
        assert ack.seq == 1 and dup.duplicate
        (m,) = await bus.fetch("normalizer", 10, 2)
        await m.ack()
        stats = {s.name: s for s in await bus.stats()}
        assert stats["NJ_RAW"].messages == 1 and len(stats) == 6

        async def boom(_):
            raise ValueError("cannot normalise")

        await bus.publish("nj.raw.o.webhook", b"{}", headers={"Nj-Org": "o"})
        (bad,) = await bus.fetch("normalizer", 10, 2)
        assert await handle_one(bus, "normalizer", bad, boom) == "dlq"
        _, total = await list_dead_letters()
        assert total >= 1
    finally:
        await bus.close()


@pytest.mark.nats
async def test_nats_full_raw_stream_is_backpressure(nats_url: str) -> None:
    from nightjar.stream.bus import StreamFullError
    from nightjar.stream.natsbus import NatsBus

    bus = NatsBus(nats_url, raw_max_bytes=4096)
    await bus.setup()
    try:
        with pytest.raises(StreamFullError):  # noqa: PT012
            for _ in range(100):
                await bus.publish("nj.raw.o.webhook", b"x" * 256)
    finally:
        await bus.close()
