"""Measure alert-to-triage over the production transports: a real nats-server (JetStream), the three agents as
HTTP A2A services on their own ports and the tool servers over Streamable HTTP, with every pipeline stage running
concurrently. The platform side is the in-memory implementation of contract Part 3 (no database needed).

    cd backend && python ../scripts/measure_latency.py      # writes eval/latency_nats.json

Stops nats-server and every server it started. Timings are wall-clock on this machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATS = ROOT / ".tools" / "nats-server"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def main() -> None:
    ports = {k: free_port() for k in ("nats", "triage", "enrichment", "response", "mcp")}
    os.environ.update(
        BUS="nats",
        NATS_URL=f"nats://127.0.0.1:{ports['nats']}",
        AGENTS_TRANSPORT="http",
        AGENT_TRIAGE_URL=f"http://127.0.0.1:{ports['triage']}",
        AGENT_ENRICHMENT_URL=f"http://127.0.0.1:{ports['enrichment']}",
        AGENT_RESPONSE_URL=f"http://127.0.0.1:{ports['response']}",
        MCP_URL=f"http://127.0.0.1:{ports['mcp']}",
        LLM_FAKE="true",
        LOG_LEVEL="ERROR",
        DATABASE_URL=f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/latency.db",
    )
    logging.disable(logging.WARNING)
    import structlog
    import uvicorn

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    from nightjar import clock
    from nightjar.agents.service import build_agent_app
    from nightjar.mcp_servers.host import ToolHost
    from nightjar.stream.runtime import get_bus, reset_bus
    from nightjar.workers.inmemory_platform import HALDEN_ORG, InMemoryPlatform
    from nightjar.workers.jobs import replay_scenario
    from nightjar.workers.pipeline import Pipeline, run_workers

    store = Path(tempfile.mkdtemp(prefix="nj-latency-"))
    proc = subprocess.Popen(  # noqa: ASYNC220
        [str(NATS), "-js", "-sd", str(store), "-a", "127.0.0.1", "-p", str(ports["nats"])],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    servers: list[uvicorn.Server] = []
    tasks: list[asyncio.Task[None]] = []
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", ports["nats"])) == 0:
                    break
            await asyncio.sleep(0.05)
        clock.install("2025-11-20")
        platform = InMemoryPlatform().install()
        apps = {name: build_agent_app(name) for name in ("triage", "enrichment", "response")}
        apps["mcp"] = ToolHost(os.environ["MCP_URL"])  # type: ignore[assignment]
        for name, app in apps.items():
            srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ports[name], log_level="error"))
            servers.append(srv)
            tasks.append(asyncio.create_task(srv.serve()))
        while not all(s.started for s in servers):
            await asyncio.sleep(0.05)
        bus = await get_bus()
        await bus.watch_max_deliveries()  # type: ignore[attr-defined]
        pipeline = Pipeline(bus, auto_investigate=True)
        stop = asyncio.Event()
        worker = asyncio.create_task(run_workers(pipeline, stop=stop))
        t0 = time.perf_counter()
        sent = await replay_scenario(HALDEN_ORG, "halden-night", speed=0, reference_order=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            await pipeline.flush_ueba()
            if (
                pipeline.counts.get("investigations", 0) >= 6
                and len(platform.runs) >= 6
                and all(r.status in ("completed", "failed") for r in platform.runs.values())
            ):
                break
        elapsed = time.perf_counter() - t0
        stop.set()
        await worker
        lat = sorted(platform.alert_to_triage_seconds())
        result = {
            "transport": "nats-server JetStream + HTTP A2A agents + Streamable HTTP tool servers",
            "events": sent["accepted"],
            "incidents": len(platform.incidents_sorted()),
            "investigations": len(platform.runs),
            "alert_to_triage_s": {
                "p50": round(statistics.median(lat), 2) if lat else None,
                "p95": round(lat[int(0.95 * (len(lat) - 1))], 2) if lat else None,
                "max": round(max(lat), 2) if lat else None,
            },
            "replay_to_last_verdict_s": round(elapsed, 1),
            "verdicts": [
                (
                    i.reference,
                    next(
                        (r.verdict.verdict for r in platform.runs.values() if r.ref.incident_id == i.id and r.verdict),
                        None,
                    ),
                )
                for i in platform.incidents_sorted()
            ],
        }
        (ROOT / "eval" / "latency_nats.json").write_text(json.dumps(result, indent=1) + "\n")
        print(json.dumps(result, indent=1))
        platform.uninstall()
    finally:
        for s in servers:
            s.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        await reset_bus()
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(store, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
