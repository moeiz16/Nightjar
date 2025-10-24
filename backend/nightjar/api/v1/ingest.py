"""Event intake (API contract §1.5): webhook JSON, syslog-style NDJSON, file replay and bundled scenarios.

Everything is published to the raw stream and acknowledged before normalisation (`202`). When the stream is full
or offline the API answers `503 intake_full` with `Retry-After`, and senders retry.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.contracts import RawEvent, to_doc
from nightjar.core.config import get_settings
from nightjar.core.deps import RequestContext, get_db, get_optional_ctx, require_perm
from nightjar.core.errors import AppError, forbidden, unavailable, validation_error
from nightjar.core.metrics import EVENTS_INGESTED
from nightjar.core.security import hash_token
from nightjar.db.models.platform import IngestKey
from nightjar.schemas.base import CamelIn
from nightjar.services.jobs import create_job, job_out

router = APIRouter(prefix="/ingest", tags=["ingest"])

MAX_JSON_BYTES = 1024 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_EVENTS = 500
FORMATS = ("ecs", "winlog", "sysmon", "cloudtrail", "okta", "m365", "nightjar", "syslog_json")
UPLOAD_SUFFIXES = (".json", ".jsonl", ".ndjson", ".csv", ".zip")


def _intake_full(retry: int = 15) -> AppError:
    return AppError(
        503,
        "intake_full",
        "Service unavailable",
        "Event intake is busy. Retry shortly.",
        headers={"Retry-After": str(retry)},
    )


async def _key_org(db: AsyncSession, request: Request, source: str) -> tuple[uuid.UUID, str] | None:
    raw = request.headers.get(get_settings().ingest_key_header)
    if raw is None:
        return None
    key = (
        await db.execute(select(IngestKey).where(IngestKey.key_hash == hash_token(raw.strip())))
    ).scalar_one_or_none()
    if key is None or key.revoked_at is not None or key.source != source:
        raise AppError(401, "invalid_ingest_key", "Unauthorized", "The event-source key is missing or revoked.")
    key.last_used_at = clock.real_now()
    return key.org_id, key.prefix


async def _publish(events: list[RawEvent], rejected: int, errors: list[str]) -> dict:
    try:
        from nightjar.stream import api as stream

        ack = await stream.publish_raw(events) if events else None
    except NotImplementedError as exc:
        raise unavailable("Event intake is not running.", code="intake_full") from exc
    except Exception as exc:
        if type(exc).__name__ == "BackpressureError":
            raise _intake_full(getattr(exc, "retry_after_s", 15)) from exc
        raise unavailable("Event intake is not running.", code="intake_full") from exc
    if events:
        EVENTS_INGESTED.labels(events[0].source).inc(len(events))
    out = to_doc(ack) if ack else {"accepted": 0, "duplicates": 0, "firstSeq": None, "lastSeq": None, "errors": []}
    out["rejected"] = int(out.get("rejected") or 0) + rejected
    out["errors"] = [*errors, *(out.get("errors") or [])][:100]
    return out


def _raw(org_id: uuid.UUID, source: str, payload: dict, fmt: str | None, ref: str | None) -> RawEvent:
    dedupe = payload.get("dedupeKey") if isinstance(payload.get("dedupeKey"), str) else None
    return RawEvent(
        org_id=org_id,
        source=source,  # type: ignore[arg-type]
        received_at=clock.now(),
        payload=payload,
        format=fmt,  # type: ignore[arg-type]
        source_ref=ref,
        dedupe_key=dedupe,
    )


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(
    request: Request,
    fmt: str | None = Query(None, alias="format"),
    db: AsyncSession = Depends(get_db),
    ctx: RequestContext | None = Depends(get_optional_ctx),
) -> dict:
    keyed = await _key_org(db, request, "webhook")
    if keyed is None:
        if ctx is None:
            raise AppError(401, "invalid_ingest_key", "Unauthorized", "Send an event-source key or sign in.")
        if not ctx.has("events:ingest"):
            raise forbidden()
        org_id, ref = ctx.org_id, f"user:{ctx.user.id}"
    else:
        org_id, ref = keyed
    await db.commit()
    if fmt is not None and fmt not in FORMATS:
        raise validation_error("Unknown format.", [{"field": "format", "message": f"Use one of {', '.join(FORMATS)}."}])
    body = await request.body()
    if len(body) > MAX_JSON_BYTES:
        raise AppError(413, "payload_too_large", "Payload too large", "Send at most 1 MB per request.")
    try:
        data = json.loads(body or b"null")
    except json.JSONDecodeError as exc:
        raise AppError(400, "bad_request", "Bad request", "The body is not valid JSON.") from exc
    items = data.get("events") if isinstance(data, dict) and "events" in data else [data]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_EVENTS:
        raise validation_error(f"Send between 1 and {MAX_EVENTS} events.", [{"field": "events", "message": "1–500."}])
    events, errors = [], []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{i}: not a JSON object")
        elif len(json.dumps(item)) > MAX_EVENT_BYTES:
            errors.append(f"{i}: larger than 256 KB")
        else:
            events.append(_raw(org_id, "webhook", item, fmt, ref))
    return await _publish(events, len(errors), errors)


@router.post("/syslog", status_code=status.HTTP_202_ACCEPTED)
async def ingest_syslog(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    keyed = await _key_org(db, request, "syslog")
    if keyed is None:
        raise AppError(401, "invalid_ingest_key", "Unauthorized", "Send a syslog event-source key.")
    await db.commit()
    org_id, ref = keyed
    body = await request.body()
    if len(body) > MAX_JSON_BYTES:
        raise AppError(413, "payload_too_large", "Payload too large", "Send at most 1 MB per request.")
    events, errors = [], []
    lines = [ln for ln in body.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    if len(lines) > MAX_EVENTS * 4:
        raise validation_error("Too many lines in one request.", [{"field": "body", "message": "At most 2,000 lines."}])
    for i, line in enumerate(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"{i}: not valid JSON")
            continue
        if not isinstance(item, dict):
            errors.append(f"{i}: not a JSON object")
            continue
        events.append(_raw(org_id, "syslog", item, "syslog_json", ref))
    return await _publish(events, len(errors), errors)


def _parse_upload(name: str, data: bytes) -> tuple[list[dict], int]:
    """Objects from .json/.jsonl/.ndjson/.csv (or one of those inside a .zip); (events, rejected)."""
    lower = name.lower()
    if lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                inner = next((n for n in zf.namelist() if n.lower().endswith(UPLOAD_SUFFIXES[:-1])), None)
                if inner is None:
                    raise AppError(415, "unsupported_media_type", "Unsupported file", "The archive has no log file.")
                info = zf.getinfo(inner)
                if info.file_size > get_settings().max_upload_mb * 1024 * 1024 * 4:
                    raise AppError(413, "payload_too_large", "Payload too large", "The archive expands too far.")
                return _parse_upload(inner, zf.read(inner))
        except zipfile.BadZipFile as exc:
            raise AppError(415, "unsupported_media_type", "Unsupported file", "Not a valid zip archive.") from exc
    text = data.decode("utf-8", errors="replace")
    rejected = 0
    if lower.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(text)))
        return [dict(r) for r in rows], 0
    stripped = text.lstrip()
    if lower.endswith(".json") and stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            if isinstance(parsed, dict):
                parsed = parsed.get("Records") or parsed.get("events") or [parsed]
            items = [p for p in parsed if isinstance(p, dict)]
            return items, len(parsed) - len(items)
    items = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            rejected += 1
            continue
        if isinstance(obj, dict):
            items.append(obj)
        else:
            rejected += 1
    return items, rejected


@router.post("/files", status_code=status.HTTP_202_ACCEPTED)
async def ingest_file(
    file: UploadFile = File(...),
    fmt: str | None = Form(None, alias="format"),
    speed: float = Form(60.0, ge=1, le=600),
    retime: bool = Form(True),
    ctx: RequestContext = Depends(require_perm("events:ingest")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    s = get_settings()
    name = Path(file.filename or "upload.json").name
    if not name.lower().endswith(UPLOAD_SUFFIXES):
        raise AppError(
            415, "unsupported_media_type", "Unsupported file", "Upload .json, .jsonl, .ndjson, .csv or .zip."
        )
    if fmt is not None and fmt not in FORMATS:
        raise validation_error("Unknown format.", [{"field": "format", "message": f"Use one of {', '.join(FORMATS)}."}])
    data = await file.read(s.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise AppError(413, "payload_too_large", "Payload too large", f"Upload at most {s.max_upload_mb} MB.")
    items, rejected = _parse_upload(name, data)
    if not items:
        raise validation_error("No events found in the file.", [{"field": "file", "message": "No events found."}])
    sample, detected = [], fmt
    try:
        from nightjar.stream import api as stream

        for item in items[:3]:
            try:
                ev = stream.normalize(_raw(ctx.org_id, "file", item, fmt, name))
                detected = detected or ev.labels.get("format") or ev.dataset
                doc = to_doc(ev)
                doc.pop("raw", None)
                sample.append(doc)
            except ValueError:
                rejected += 1
    except NotImplementedError:
        pass
    uploads = s.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()[:16]
    job = await create_job(db, org_id=ctx.org_id, kind="replay", payload={}, created_by=ctx.user.id)
    path = uploads / f"{job.id}-{digest}{Path(name).suffix.lower()}"
    path.write_bytes(data)
    payload: dict[str, Any] = {
        "path": str(path),
        "format": fmt,
        "speed": speed,
        "retime": retime,
        "orgId": str(ctx.org_id),
        "fileName": name,
        "events": len(items),
    }
    job.payload = payload
    await db.commit()
    try:
        from nightjar.stream import api as stream

        await stream.enqueue_job("replay_file", job.id, payload)
    except Exception as exc:
        from nightjar.services.jobs import apply_update

        await apply_update(db, job, status="failed", error="Event intake is not running.")
        await db.commit()
        raise _intake_full() from exc
    return {"job": job_out(job), "preview": {"detectedFormat": detected, "sampleEvents": sample, "rejected": rejected}}


@router.get("/scenarios")
async def scenarios(ctx: RequestContext = Depends(require_perm("events:ingest"))) -> list[dict]:
    try:
        from nightjar.stream import api as stream

        return [to_doc(s) for s in stream.list_scenarios()]
    except NotImplementedError:
        return []


class ReplayIn(CamelIn):
    speed: float = 60.0


@router.post("/scenarios/{scenario_id}/replay", status_code=status.HTTP_202_ACCEPTED)
async def replay(
    scenario_id: str,
    body: ReplayIn | None = None,
    ctx: RequestContext = Depends(require_perm("events:ingest")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    speed = (body.speed if body else 60.0) or 60.0
    if not 1 <= speed <= 600:
        raise validation_error("Speed must be between 1 and 600.", [{"field": "speed", "message": "1–600."}])
    try:
        from nightjar.stream import api as stream

        known = {s.id for s in stream.list_scenarios()}
    except NotImplementedError as exc:
        raise _intake_full() from exc
    if scenario_id not in known:
        raise AppError(404, "not_found", "Scenario not found", "Scenario not found.")
    job = await create_job(
        db,
        org_id=ctx.org_id,
        kind="replay",
        payload={"scenarioId": scenario_id, "speed": speed},
        created_by=ctx.user.id,
    )
    await db.commit()
    try:
        await stream.start_replay(ctx.org_id, scenario_id, speed=speed, job_id=job.id)
    except Exception as exc:
        raise _intake_full() from exc
    return {"job": job_out(job)}
