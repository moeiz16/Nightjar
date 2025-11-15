"""Threat hunting: structured searches, saved searches and suggestions (API contract §1.13)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import AppError, unavailable
from nightjar.db.models.platform import User
from nightjar.db.models.soc import SavedSearch
from nightjar.hunting import api as store
from nightjar.hunting.service import (
    HuntQueryIn,
    fields_doc,
    query_from_doc,
    result_doc,
    saved_doc,
    stored_query_in,
    suggestions,
)
from nightjar.schemas.base import CamelIn
from nightjar.services.audit import audit

router = APIRouter(prefix="/hunting", tags=["hunting"])

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class SavedIn(CamelIn):
    name: Name
    description: str = Field(default="", max_length=2000)
    query: HuntQueryIn
    alert: bool = False


class SavedPatchIn(CamelIn):
    name: Name | None = None
    description: str | None = Field(default=None, max_length=2000)
    query: HuntQueryIn | None = None
    alert: bool | None = None


class RunIn(CamelIn):
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None


def _stored(q: HuntQueryIn) -> dict:
    doc = q.model_dump(by_alias=True, mode="json")
    if not q.from_ and not q.to and not q.relative_hours:
        doc["relativeHours"] = 24
    return doc


async def run_query(org_id: uuid.UUID, q: HuntQueryIn) -> dict:
    try:
        result = await store.search(query_from_doc(org_id, q))
    except store.InvalidQueryError as exc:
        raise AppError(
            422,
            "invalid_query",
            "Invalid search",
            str(exc),
            errors=[{"field": exc.field, "message": str(exc)}] if exc.field else None,
        ) from exc
    except store.SearchTimeoutError as exc:
        raise unavailable("The search took longer than 10 seconds. Narrow the time range or add conditions.") from exc
    return result_doc(result)


@router.get("/fields")
async def fields(ctx: RequestContext = Depends(require_perm("hunting:read"))) -> list[dict]:
    return fields_doc()


@router.post("/search")
async def search(body: HuntQueryIn, ctx: RequestContext = Depends(require_perm("hunting:read"))) -> dict:
    return await run_query(ctx.org_id, body)


async def _saved(db: AsyncSession, org_id: uuid.UUID, saved_id: str) -> SavedSearch:
    try:
        uid = uuid.UUID(saved_id)
    except ValueError:
        uid = None
    s = await db.get(SavedSearch, uid) if uid else None
    if s is None or s.org_id != org_id:
        raise AppError(404, "not_found", "Saved search not found", "Saved search not found.")
    return s


async def _doc(db: AsyncSession, s: SavedSearch) -> dict:
    return saved_doc(s, await db.get(User, s.created_by) if s.created_by else None)


@router.get("/saved")
async def list_saved(
    ctx: RequestContext = Depends(require_perm("hunting:read")), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = list(
        (
            await db.execute(select(SavedSearch).where(SavedSearch.org_id == ctx.org_id).order_by(SavedSearch.name))
        ).scalars()
    )
    uids = {s.created_by for s in rows if s.created_by}
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(uids)))).scalars()} if uids else {}
    return [saved_doc(s, users.get(s.created_by) if s.created_by else None) for s in rows]


@router.post("/saved", status_code=status.HTTP_201_CREATED)
async def create_saved(
    body: SavedIn, ctx: RequestContext = Depends(require_perm("hunting:write")), db: AsyncSession = Depends(get_db)
) -> dict:
    query_from_doc(ctx.org_id, body.query)  # validates the time range
    s = SavedSearch(
        org_id=ctx.org_id,
        name=body.name,
        description=body.description,
        query=_stored(body.query),
        created_by=ctx.user.id,
        alert=body.alert,
    )
    db.add(s)
    await db.flush()
    await audit(db, ctx, action="hunt.saved", resource_type="saved_search", resource_id=s.id, metadata={"name": s.name})
    await db.commit()
    return await _doc(db, s)


@router.patch("/saved/{saved_id}")
async def patch_saved(
    saved_id: str,
    body: SavedPatchIn,
    ctx: RequestContext = Depends(require_perm("hunting:write")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    s = await _saved(db, ctx.org_id, saved_id)
    if body.name is not None:
        s.name = body.name
    if body.description is not None:
        s.description = body.description
    if body.query is not None:
        s.query = _stored(body.query)
    if body.alert is not None:
        s.alert = body.alert
    await audit(
        db,
        ctx,
        action="hunt.updated",
        resource_type="saved_search",
        resource_id=s.id,
        metadata={"name": s.name, **({"alert": body.alert} if body.alert is not None else {})},
    )
    await db.commit()
    return await _doc(db, s)


@router.delete("/saved/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved(
    saved_id: str, ctx: RequestContext = Depends(require_perm("hunting:write")), db: AsyncSession = Depends(get_db)
) -> Response:
    s = await _saved(db, ctx.org_id, saved_id)
    await audit(
        db, ctx, action="hunt.deleted", resource_type="saved_search", resource_id=s.id, metadata={"name": s.name}
    )
    await db.delete(s)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/saved/{saved_id}/run")
async def run_saved(
    saved_id: str,
    body: RunIn | None = None,
    ctx: RequestContext = Depends(require_perm("hunting:read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    s = await _saved(db, ctx.org_id, saved_id)
    q = stored_query_in(s.query, frm=body.from_ if body else None, to=body.to if body else None)
    out = await run_query(ctx.org_id, q)
    s.last_run_at = clock.now()
    s.last_count = out["totalMatched"]
    await audit(
        db,
        ctx,
        action="hunt.run",
        resource_type="saved_search",
        resource_id=s.id,
        metadata={"name": s.name, "matched": out["totalMatched"]},
    )
    await db.commit()
    return out


@router.get("/suggestions")
async def list_suggestions(
    ctx: RequestContext = Depends(require_perm("hunting:read")), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    return await suggestions(db, ctx.org_id)
