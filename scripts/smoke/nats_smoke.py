"""Smoke check for the event backbone (ADR 0001): nats-server + JetStream + nats-py on this machine.

Starts `.tools/nats-server` with JetStream in a temporary store, then verifies:
  1. publish with JetStream acks (stream sequence numbers)
  2. a durable pull consumer that survives reconnect (same durable name, resumes after the acked position)
  3. redelivery when a message is not acked within `ack_wait`, and immediate redelivery on `nak`
  4. max_deliver exhaustion → the MAX_DELIVERIES advisory → the message is copied into a DLQ stream
  5. backpressure: a stream at its byte limit with discard=new rejects publishes (the API turns this into 503)
  6. resident memory of nats-server

    python scripts/smoke/nats_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import nats
from nats.js.api import AckPolicy, ConsumerConfig, DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import APIError

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / ".tools" / "nats-server"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return int(out.stdout.strip() or 0) / 1024


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(1)


async def run(url: str, pid: int) -> None:
    nc = await nats.connect(url, name="nightjar-smoke")
    js = nc.jetstream()

    # 1. stream + publish acks
    await js.add_stream(
        StreamConfig(
            name="NJ_SMOKE",
            subjects=["nj.smoke.>"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=3600,
            duplicate_window=120,
        )
    )
    await js.add_stream(StreamConfig(name="NJ_SMOKE_DLQ", subjects=["nj.dlq.smoke.>"], storage=StorageType.FILE))
    acks = [
        await js.publish("nj.smoke.events", json.dumps({"n": i}).encode(), headers={"Nats-Msg-Id": f"evt-{i}"})
        for i in range(20)
    ]
    dup = await js.publish("nj.smoke.events", b"{}", headers={"Nats-Msg-Id": "evt-0"})
    check("publish with stream acks", acks[-1].seq == 20, f"last seq {acks[-1].seq}")
    check("duplicate suppressed by Nats-Msg-Id", bool(dup.duplicate))

    # 2. durable pull consumer, ack, reconnect resumes
    cfg = ConsumerConfig(
        durable_name="smoke-detector",
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=1.0,
        max_deliver=3,
        max_ack_pending=50,
        filter_subject="nj.smoke.events",
    )
    sub = await js.pull_subscribe("nj.smoke.events", durable="smoke-detector", config=cfg)
    batch = await sub.fetch(10, timeout=2)
    for m in batch:
        await m.ack()
    await sub.unsubscribe()
    sub = await js.pull_subscribe("nj.smoke.events", durable="smoke-detector", config=cfg)
    batch = await sub.fetch(10, timeout=2)
    first = json.loads(batch[0].data)["n"]
    check("durable consumer resumes after acked position", first == 10, f"next n={first}")

    # 3. redelivery: ack all but n=10 (no ack) and n=11 (nak)
    held = batch[0]
    naked = batch[1]
    for m in batch[2:]:
        await m.ack()
    await naked.nak()
    t0 = time.monotonic()
    redelivered: dict[int, int] = {}
    while time.monotonic() - t0 < 5 and len(redelivered) < 2:
        try:
            for m in await sub.fetch(5, timeout=1):
                n = json.loads(m.data)["n"]
                redelivered[n] = m.metadata.num_delivered
                await m.ack()
        except TimeoutError:
            pass
    check("nak redelivers immediately", redelivered.get(11) == 2, f"deliveries={redelivered.get(11)}")
    check("ack_wait expiry redelivers", redelivered.get(10) == 2, f"deliveries={redelivered.get(10)}")
    del held

    # 4. max_deliver → advisory → DLQ
    dlq_done = asyncio.Event()

    async def on_advisory(msg: nats.aio.msg.Msg) -> None:
        adv = json.loads(msg.data)
        original = await js.get_msg(adv["stream"], adv["stream_seq"])
        await js.publish(
            "nj.dlq.smoke.events",
            original.data or b"",
            headers={
                "Nj-Original-Subject": original.subject or "",
                "Nj-Original-Seq": str(adv["stream_seq"]),
                "Nj-Consumer": adv["consumer"],
                "Nj-Deliveries": str(adv["deliveries"]),
            },
        )
        dlq_done.set()

    await nc.subscribe("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.NJ_SMOKE.*", cb=on_advisory)
    poison = await js.publish("nj.smoke.events", json.dumps({"n": "poison"}).encode())
    deliveries = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < 8 and not dlq_done.is_set():
        try:
            for m in await sub.fetch(5, timeout=1):
                if json.loads(m.data)["n"] == "poison":
                    deliveries = m.metadata.num_delivered
                    await m.nak()  # simulate a handler that keeps failing
                else:
                    await m.ack()
        except TimeoutError:
            pass
    await asyncio.wait_for(dlq_done.wait(), timeout=5)
    dlq_info = await js.stream_info("NJ_SMOKE_DLQ")
    check("max_deliver reached", deliveries == 3, f"deliveries={deliveries}")
    check("poison message copied to DLQ via advisory", dlq_info.state.messages == 1, f"orig seq {poison.seq}")

    # 5. backpressure: byte-limited stream with discard=new rejects when full
    await js.add_stream(
        StreamConfig(name="NJ_SMOKE_BP", subjects=["nj.bp.>"], max_bytes=4096, discard=DiscardPolicy.NEW)
    )
    rejected = False
    accepted = 0
    for _ in range(200):
        try:
            await js.publish("nj.bp.raw", b"x" * 256)
            accepted += 1
        except APIError as exc:
            rejected = "maximum bytes" in str(exc).lower() or exc.err_code == 10047
            break
    check("full stream rejects publishes (discard=new)", rejected, f"accepted {accepted} before reject")

    info = await js.consumer_info("NJ_SMOKE", "smoke-detector")
    check(
        "consumer lag observable",
        info.num_pending == 0,
        f"pending={info.num_pending} ack_pending={info.num_ack_pending}",
    )
    rss = _rss_mb(pid)
    check("nats-server resident memory under 64 MB", rss < 64, f"{rss:.1f} MB")
    await sub.unsubscribe()
    await nc.close()


def main() -> int:
    if not BINARY.exists():
        sys.exit("Run `make tools` first (downloads .tools/nats-server).")
    store = Path(tempfile.mkdtemp(prefix="nj-nats-"))
    port = _free_port()
    proc = subprocess.Popen(
        [str(BINARY), "-js", "-sd", str(store), "-a", "127.0.0.1", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        print(
            f"nats-server {subprocess.run([str(BINARY), '--version'], capture_output=True, text=True, check=False).stdout.strip()}"
        )
        asyncio.run(run(f"nats://127.0.0.1:{port}", proc.pid))
        print("NATS smoke: PASS")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(store, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
