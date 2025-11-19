"""Run a scenario through the whole pipeline in one process (memory bus, in-process A2A agents and tool servers,
offline model). Used by the demo seed (phase 2), `make eval` and the end-to-end tests.

    refs = await run_scenario(org_id, "halden-night")

The stages are the real handlers; only the transport is in-process. Investigations are dispatched after the
scenario's detections have been correlated (the dispatcher consumer starts once the detection stages are
drained), so each triage run sees its incident's full evidence and incidents keep the reference order of API
contract Part 6. Pass `concurrent_dispatch=True` to dispatch as incidents open (latency measurements), or
`investigate_at` (incident id → moment) to run each investigation at its own moment on the business clock, so a
replayed night's agent steps, verdicts and proposals carry night-time timestamps (the demo seed).
"""
# ruff: noqa: ASYNC109  (explicit per-call timeouts are part of the bus interface)

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from nightjar.contracts import IncidentRef, NormalizedEvent, RawEvent
from nightjar.stream import codec
from nightjar.stream.bus import MemoryBus
from nightjar.stream.runtime import handle_one, install_dlq_mover, run_batch_consumer, set_bus
from nightjar.workers.pipeline import HANDLERS, Pipeline

log = structlog.get_logger("nightjar.inline")
DETECTION_STAGES = ("normalizer", "detector", "ueba", "scorer", "correlator")
MALFORMED_SYSLOG = {
    "host": "mel-vpn-01",
    "program": "fortigate",
    "timestamp": "32/13/2025 25:61:00",
    "message": "action=login-failed user=?? src=203.0.113.50",
}


@dataclass
class ScenarioRun:
    created: list[IncidentRef] = field(default_factory=list)
    runs: list[uuid.UUID] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    triage_latency_s: list[float] = field(default_factory=list)
    seconds: float = 0.0
    detector_eps: float = 0.0
    pipeline: Pipeline | None = None


@contextlib.contextmanager
def _inline_settings() -> Any:
    from nightjar.core.config import get_settings

    s = get_settings()
    saved = (s.bus, s.agents_transport, s.llm_fake)
    s.bus, s.agents_transport, s.llm_fake = "memory", "inline", True
    try:
        yield s
    finally:
        s.bus, s.agents_transport, s.llm_fake = saved


async def _drain(
    pipeline: Pipeline, names: Sequence[str], *, store: Callable[[Sequence[NormalizedEvent]], Awaitable[None]] | None
) -> int:
    """Run the named stages until none of them (and the store writer) has pending work."""
    bus = pipeline.bus
    assert isinstance(bus, MemoryBus)
    total = 0
    while True:
        progressed = 0
        for name in names:
            handler = getattr(pipeline, HANDLERS[name])
            while True:
                msgs = await bus.fetch(name, 500, 0.0)
                if not msgs:
                    break
                for m in msgs:
                    await handle_one(bus, name, m, handler)
                progressed += len(msgs)
        if store is not None:
            msgs = await bus.fetch("store-writer", 500, 0.0)
            if msgs:
                events = [codec.decode(NormalizedEvent, m.data) for m in msgs]
                await store(events)
                for m in msgs:
                    await m.ack()
                progressed += len(msgs)
        total += progressed
        if progressed == 0 and bus.idle(list(names)):
            return total


def _store_writer() -> Callable[[Sequence[NormalizedEvent]], Awaitable[None]] | None:
    try:
        from nightjar.hunting.service import handle_normalized  # builder A

        return handle_normalized
    except Exception:
        return None


InvestigateAt = Callable[[uuid.UUID], Awaitable[datetime | None]]


async def _dispatch_timed(
    pipeline: Pipeline,
    when: InvestigateAt,
    *,
    store: Callable[[Sequence[NormalizedEvent]], Awaitable[None]] | None,
) -> None:
    """Start each investigation, and let it finish, with the business clock at `when(incident id)`."""
    from nightjar import clock

    bus = pipeline.bus
    assert isinstance(bus, MemoryBus)
    while True:
        msgs = await bus.fetch("dispatcher", 1, 0.0)
        if not msgs:
            break
        m = msgs[0]
        at = await when(uuid.UUID(codec.loads(m.data)["incidentId"]))
        with clock.travel(at) if at else contextlib.nullcontext():
            await handle_one(bus, "dispatcher", m, pipeline.on_incident_opened)
            await _drain(pipeline, ("orchestrator", "responder"), store=store)
    await _drain(pipeline, ("dispatcher", "orchestrator", "responder"), store=store)


async def run_scenario(
    org_id: uuid.UUID,
    scenario_id: str = "halden-night",
    *,
    end_at: datetime | None = None,
    auto_investigate: bool = True,
    store_writer: Callable[[Sequence[NormalizedEvent]], Awaitable[None]] | bool | None = True,
    concurrent_dispatch: bool = False,
    include_dlq_sample: bool = True,
    anchor: datetime | None = None,
    investigate_at: InvestigateAt | None = None,
) -> list[IncidentRef]:
    """Seed entry point: returns the incidents created, in creation (= reference) order."""
    return (
        await run_scenario_detailed(
            org_id,
            scenario_id,
            end_at=end_at,
            auto_investigate=auto_investigate,
            store_writer=store_writer,
            concurrent_dispatch=concurrent_dispatch,
            include_dlq_sample=include_dlq_sample,
            anchor=anchor,
            investigate_at=investigate_at,
        )
    ).created


async def run_scenario_detailed(
    org_id: uuid.UUID,
    scenario_id: str = "halden-night",
    *,
    end_at: datetime | None = None,
    auto_investigate: bool = True,
    store_writer: Callable[[Sequence[NormalizedEvent]], Awaitable[None]] | bool | None = True,
    concurrent_dispatch: bool = False,
    include_dlq_sample: bool = True,
    anchor: datetime | None = None,
    events: Sequence[tuple[datetime, dict[str, Any], str | None]] | None = None,
    investigate_at: InvestigateAt | None = None,
) -> ScenarioRun:
    from nightjar.mcp_servers.host import reset_inline_host

    t0 = time.perf_counter()
    with _inline_settings() as settings:
        bus = MemoryBus(raw_max_bytes=settings.stream_max_bytes_raw)
        await bus.connect()
        await bus.setup()
        install_dlq_mover(bus)
        set_bus(bus)
        store = _store_writer() if store_writer is True else (store_writer or None)
        bus.activate(*HANDLERS, *(("store-writer",) if store else ()))
        pipeline = Pipeline(bus, auto_investigate=auto_investigate)
        out = ScenarioRun(pipeline=pipeline)
        try:
            if anchor is None and end_at is not None:
                from nightjar.workers.replay.loader import night_anchor

                anchor = night_anchor(end_at)
            if concurrent_dispatch:
                stop = asyncio.Event()
                from nightjar.workers.pipeline import run_workers

                worker = asyncio.create_task(run_workers(pipeline, stop=stop))
                await _publish(org_id, scenario_id, anchor, events)
                await _wait_idle(bus, pipeline)
                await pipeline.flush_ueba()
                await _wait_idle(bus, pipeline)
                stop.set()
                await worker
            else:
                await _publish(org_id, scenario_id, anchor, events)
                if include_dlq_sample:
                    from nightjar.stream.api import publish_raw

                    await publish_raw(
                        [RawEvent(org_id, "syslog", _now(), dict(MALFORMED_SYSLOG), "syslog_json", "seed")]
                    )
                await _drain(pipeline, DETECTION_STAGES, store=store)
                await pipeline.flush_ueba()
                await _drain(pipeline, DETECTION_STAGES, store=store)
                if investigate_at is not None:
                    await _dispatch_timed(pipeline, investigate_at, store=store)
                else:
                    await _drain(pipeline, ("dispatcher", "orchestrator", "responder"), store=store)
            out.created = list(pipeline.created)
            out.runs = list(pipeline.runs)
            out.counts = dict(pipeline.counts)
            out.triage_latency_s = list(pipeline.triage_latency)
            out.detector_eps = pipeline.detector.throughput()
        finally:
            set_bus(None)
            await reset_inline_host()
    out.seconds = time.perf_counter() - t0
    log.info("scenario_done", scenario=scenario_id, incidents=len(out.created), seconds=round(out.seconds, 1))
    return out


async def _publish(
    org_id: uuid.UUID,
    scenario_id: str,
    anchor: datetime | None,
    events: Sequence[tuple[datetime, dict[str, Any], str | None]] | None,
) -> None:
    from nightjar.workers.jobs import publish_paced, replay_scenario

    if events is None:
        await replay_scenario(org_id, scenario_id, speed=0, anchor=anchor, reference_order=True)
    else:
        await publish_paced(org_id, events, total=len(events), speed=0, source_ref=scenario_id)


async def _wait_idle(bus: MemoryBus, pipeline: Pipeline, timeout: float = 600) -> None:
    deadline = time.monotonic() + timeout
    names = [n for n in HANDLERS if n != "jobs"]
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        if bus.idle(names):
            await asyncio.sleep(0.5)
            if bus.idle(names):
                return


def _now() -> datetime:
    from nightjar import clock

    return clock.now()


async def store_events_noop(_: Sequence[NormalizedEvent]) -> None:
    return None


__all__ = ["ScenarioRun", "run_batch_consumer", "run_scenario", "run_scenario_detailed"]
