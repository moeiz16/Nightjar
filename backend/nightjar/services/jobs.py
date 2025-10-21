"""Long-running jobs (replays, weekly brief, training): the `jobs` row plus SSE `job.updated` to its creator."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.events import emit
from nightjar.db.models.platform import Job
from nightjar.db.session import session_scope


def job_out(job: Job) -> dict:
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "result": job.result,
        "error": job.error or None,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
    }


async def _announce(db: AsyncSession, job: Job) -> None:
    if job.org_id is None:
        return
    await emit(
        db,
        job.org_id,
        "job.updated",
        {"jobId": str(job.id), "kind": job.kind, "status": job.status, "progress": job.progress},
        user_ids=[job.created_by] if job.created_by else None,
    )


async def create_job(
    db: AsyncSession, *, org_id: uuid.UUID, kind: str, payload: dict[str, Any], created_by: uuid.UUID | None
) -> Job:
    job = Job(org_id=org_id, kind=kind, payload=payload, status="queued", progress=0, created_by=created_by, error="")
    db.add(job)
    await db.flush()
    await _announce(db, job)
    return job


async def apply_update(
    db: AsyncSession,
    job: Job,
    *,
    status: str | None = None,
    progress: int | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> Job:
    if status:
        job.status = status
        if status == "running" and job.started_at is None:
            job.started_at = clock.now()
        if status in ("succeeded", "failed"):
            job.finished_at = clock.now()
            job.progress = 100 if status == "succeeded" else job.progress
    if progress is not None:
        job.progress = max(0, min(100, int(progress)))
    if result is not None:
        job.result = result
    if error is not None:
        job.error = error[:2000]
    await _announce(db, job)
    return job


async def update_job(
    job_id: uuid.UUID,
    *,
    status: str | None = None,
    progress: int | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """For workers (builder B): progress of a job the API created. Emits `job.updated` to the job's creator."""
    async with session_scope() as db:
        job = await db.get(Job, job_id)
        if job is None:
            raise KeyError(f"unknown job {job_id}")
        await apply_update(db, job, status=status, progress=progress, result=result, error=error)
