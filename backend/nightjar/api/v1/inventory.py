"""Asset inventory and identity directory lists (API contract §1.8a). Read-only: the directory changes only through
approved response actions (isolation, account switches), never by hand here."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.db.models.soc import Asset, Identity
from nightjar.directory.api import asset_doc, identity_doc
from nightjar.services.pipeline import kev_docs

router = APIRouter(tags=["inventory"])

_CRIT = {"crown_jewel": 0, "high": 1, "medium": 2, "low": 3}


@router.get("/assets")
async def list_assets(
    q: str | None = Query(None, max_length=120),
    kind: str | None = Query(None, max_length=40),
    criticality: Literal["crown_jewel", "high", "medium", "low"] | None = None,
    ctx: RequestContext = Depends(require_perm("incidents:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(Asset).where(Asset.org_id == ctx.org_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Asset.name.ilike(like), Asset.business_service.ilike(like), Asset.site.ilike(like)))
    if kind:
        stmt = stmt.where(Asset.kind == kind)
    if criticality:
        stmt = stmt.where(Asset.criticality == criticality)
    rows = list((await db.execute(stmt)).scalars())
    rows.sort(key=lambda a: (_CRIT.get(a.criticality, 9), a.name))
    return [asset_doc(a, kev_docs(list(a.vulnerabilities or []))) for a in rows]


@router.get("/identities")
async def list_identities(
    q: str | None = Query(None, max_length=120),
    kind: str | None = Query(None, max_length=40),
    privileged: bool | None = None,
    ctx: RequestContext = Depends(require_perm("incidents:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(Identity).where(Identity.org_id == ctx.org_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(Identity.username.ilike(like), Identity.display_name.ilike(like), Identity.department.ilike(like))
        )
    if kind:
        stmt = stmt.where(Identity.kind == kind)
    if privileged is not None:
        stmt = stmt.where(Identity.privileged.is_(privileged))
    rows = (await db.execute(stmt.order_by(Identity.display_name))).scalars()
    return [identity_doc(i) for i in rows]
