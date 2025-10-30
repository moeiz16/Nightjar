"""Long jobs on `nj.jobs.<kind>` (consumer `jobs`, one at a time): scenario replays and uploaded-file replays.
Progress goes to A's job row (`nightjar.services.jobs.update_job`, which also emits SSE `job.updated`)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
import zipfile
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from nightjar import clock
from nightjar.contracts import RawEvent
from nightjar.ingest.common import parse_ts
from nightjar.stream.api import BackpressureError, publish_raw

log = structlog.get_logger("nightjar.jobs")
BATCH = 200


async def _progress(job_id: str | None, **kw: Any) -> None:
    if not job_id:
        return
    try:
        from nightjar.services.jobs import update_job  # builder A

        await update_job(uuid.UUID(job_id), **kw)
    except Exception as exc:
        log.debug("job_progress_skipped", error=str(exc)[:120])


async def publish_paced(
    org_id: uuid.UUID,
    items: Iterable[tuple[datetime, dict[str, Any], str | None]],
    *,
    total: int,
    speed: float,
    source_ref: str,
    job_id: str | None = None,
    source: str = "replay",
) -> dict[str, int]:
    """Publish (timestamp, payload, format) in batches. `speed` = event-time seconds per wall second (60 = one hour
    per minute); 0 = as fast as the bus accepts. Backpressure → wait and retry (never drop)."""
    sent = dup = rej = 0
    batch: list[RawEvent] = []
    prev: datetime | None = None
    done = 0

    async def flush() -> None:
        nonlocal sent, dup, rej, batch
        while batch:
            try:
                ack = await publish_raw(batch)
            except BackpressureError as exc:
                await asyncio.sleep(min(exc.retry_after_s, 5))
                continue
            sent, dup, rej = sent + ack.accepted, dup + ack.duplicates, rej + ack.rejected
            batch = []

    for when, payload, fmt in items:
        if speed > 0 and prev is not None:
            gap = (when - prev).total_seconds() / speed
            if gap > 0:
                await flush()
                await asyncio.sleep(min(gap, 30))
        prev = when
        batch.append(RawEvent(org_id, source, clock.now(), payload, fmt, source_ref))  # type: ignore[arg-type]
        done += 1  # noqa: SIM113
        if len(batch) >= BATCH:
            await flush()
            await _progress(job_id, progress=int(100 * done / max(total, 1)), status="running")
    await flush()
    return {"accepted": sent, "duplicates": dup, "rejected": rej}


async def replay_scenario(
    org_id: uuid.UUID,
    scenario_id: str,
    *,
    speed: float,
    job_id: str | None = None,
    anchor: datetime | None = None,
    reference_order: bool = False,
) -> dict[str, int]:
    from nightjar.workers.replay import loader

    if scenario_id == "halden-night":
        lines = list(loader.halden_night(anchor, time_order=not reference_order and speed > 0))
        items = [(when, payload, line.fmt) for line, when, payload in lines]
        return await publish_paced(org_id, items, total=len(items), speed=speed, source_ref=scenario_id, job_id=job_id)
    if scenario_id.startswith("otrf-"):
        key = scenario_id.removeprefix("otrf-")
        items = [(when, payload, None) for when, payload in loader.otrf_events(key)]
        return await publish_paced(org_id, items, total=len(items), speed=speed, source_ref=scenario_id, job_id=job_id)
    raise KeyError(scenario_id)


def _file_rows(path: Path, fmt: str | None) -> list[dict[str, Any]]:
    def parse_text(name: str, text: str) -> list[dict[str, Any]]:
        if name.endswith(".csv"):
            return [dict(r) for r in csv.DictReader(io.StringIO(text))]
        text = text.strip()
        if text.startswith("["):
            return [r for r in json.loads(text) if isinstance(r, dict)]
        if text.startswith("{") and "\n" not in text:
            obj = json.loads(text)
            return obj.get("Records") or obj.get("events") or [obj]
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.endswith((".json", ".jsonl", ".ndjson", ".csv"))]
            if not names:
                raise ValueError("the archive has no .json, .jsonl, .ndjson or .csv file")
            return parse_text(names[0], zf.read(names[0]).decode("utf-8", errors="replace"))
    return parse_text(path.name, path.read_text(encoding="utf-8", errors="replace"))


async def replay_file(payload: dict[str, Any], job_id: str | None) -> dict[str, int]:
    org_id = uuid.UUID(payload["orgId"])
    rows = _file_rows(Path(payload["path"]), payload.get("format"))
    stamps: list[datetime | None] = []
    for r in rows:
        try:
            stamps.append(
                parse_ts(
                    r.get("@timestamp")
                    or r.get("eventTime")
                    or r.get("published")
                    or r.get("CreationTime")
                    or r.get("TimeCreated")
                    or r.get("timestamp")
                    or r.get("Event DateTime")
                )
            )
        except ValueError:
            stamps.append(None)
    known = [s for s in stamps if s]
    shift = (clock.now() - max(known)) if payload.get("retime", True) and known else timedelta(0)
    items = []
    for r, s in zip(rows, stamps, strict=True):
        when = (s + shift) if s else clock.now()
        if s and shift:
            for k in ("@timestamp", "eventTime", "published", "CreationTime", "timestamp"):
                if k in r:
                    r[k] = when.isoformat()
            r.pop("TimeCreated", None)
            r.setdefault("@timestamp", when.isoformat())
        items.append((when, r, payload.get("format")))
    items.sort(key=lambda x: x[0])
    return await publish_paced(
        org_id,
        items,
        total=len(items),
        speed=float(payload.get("speed", 60)),
        source_ref=Path(payload["path"]).name,
        job_id=job_id,
        source="file",
    )


async def run_job(job: dict[str, Any], pipeline: Any) -> None:
    kind, job_id, payload = job.get("kind"), job.get("jobId"), job.get("payload") or {}
    await _progress(job_id, status="running", progress=0)
    try:
        if kind == "replay":
            result = await replay_scenario(
                uuid.UUID(payload["orgId"]), payload["scenarioId"], speed=float(payload.get("speed", 60)), job_id=job_id
            )
        elif kind == "replay_file":
            result = await replay_file(payload, job_id)
        elif kind == "train":
            from nightjar.ml.train import main as train_main

            await asyncio.to_thread(train_main, ["all"])
            result = {"trained": 1}
        else:
            raise ValueError(f"unknown job kind {kind!r}")
    except Exception as exc:
        await _progress(job_id, status="failed", error=str(exc)[:500])
        raise
    await _progress(job_id, status="succeeded", progress=100, result=result)
