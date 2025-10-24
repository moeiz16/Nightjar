"""Jobs, pipeline health, dead letters and the agents' cards (API contract §1.17)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.contracts import to_doc
from nightjar.core.deps import RequestContext, get_ctx, get_db, require_any, require_perm
from nightjar.core.errors import AppError, unavailable
from nightjar.core.paging import Page
from nightjar.db.models.platform import Job
from nightjar.services.audit import audit
from nightjar.services.jobs import job_out
from nightjar.services.pipeline import pipeline_health

router = APIRouter(tags=["health"])


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, ctx: RequestContext = Depends(get_ctx), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        job = await db.get(Job, uuid.UUID(job_id))
    except ValueError:
        job = None
    if job is None or job.org_id != ctx.org_id:
        raise AppError(404, "not_found", "Job not found", "Job not found.")
    return job_out(job)


@router.get("/pipeline/health")
async def health(ctx: RequestContext = Depends(require_perm("watch:read"))) -> dict:
    h = to_doc(await pipeline_health())
    if not ctx.has("detections:read"):  # plain status for everyone; stream detail for the security team
        h["streams"] = []
    return h


def _stream():
    from nightjar.stream import api as stream

    return stream


@router.get("/pipeline/dlq")
async def dlq(page: Page = Depends(), ctx: RequestContext = Depends(require_any("rules:manage", "org:admin"))) -> dict:
    fn = getattr(_stream(), "list_dlq", None)
    if fn is None:
        return page.wrap([], 0)
    try:
        items, total = fn(page=page.page, page_size=page.page_size)
    except NotImplementedError:
        return page.wrap([], 0)
    except Exception as exc:
        raise unavailable("The event stream is not reachable.") from exc
    return page.wrap(list(items), int(total))


@router.post("/pipeline/dlq/{seq}/retry")
async def retry(
    seq: int, ctx: RequestContext = Depends(require_perm("org:admin")), db: AsyncSession = Depends(get_db)
) -> dict:
    fn = getattr(_stream(), "retry_dlq", None)
    if fn is None:
        raise unavailable("The event stream is not reachable.")
    try:
        await fn(seq)
    except KeyError as exc:
        raise AppError(404, "not_found", "Message not found", "Dead-lettered message not found.") from exc
    except NotImplementedError as exc:
        raise unavailable("The event stream is not reachable.") from exc
    await audit(db, ctx, action="pipeline.dlq_retried", resource_type="dlq", resource_id=seq, metadata={"seq": seq})
    await db.commit()
    return {"retried": seq}


@router.get("/agents")
async def agents(ctx: RequestContext = Depends(require_perm("investigations:read"))) -> list[dict]:
    try:
        from nightjar.agents import api as agents_api

        return [to_doc(c) for c in await agents_api.agents_health()]
    except NotImplementedError:
        return []
