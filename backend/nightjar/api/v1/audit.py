"""Tamper-evident audit trail (API contract §1.15)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.db.models.platform import AuditLog
from nightjar.services.audit import describe, verify_chain

router = APIRouter(prefix="/audit", tags=["audit"])


def entry_out(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "action": row.action,
        "label": describe(row.action, row.meta),
        "actorName": row.actor_name,
        "actorType": row.actor_type,
        "resourceType": row.resource_type,
        "resourceId": row.resource_id,
        "metadata": row.meta or {},
        "ip": row.ip,
        "createdAt": row.created_at.isoformat(),
        "hash": row.hash,
        "prevHash": row.prev_hash,
    }


@router.get("")
async def list_audit(
    ctx: RequestContext = Depends(require_perm("audit:read")),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200, alias="pageSize"),
    action: str | None = None,
    actor_type: str | None = Query(None, alias="actorType"),
    resource_type: str | None = Query(None, alias="resourceType"),
    resource_id: str | None = Query(None, alias="resourceId"),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
) -> dict:
    conds = [AuditLog.org_id == ctx.org_id]
    if action:
        conds.append(AuditLog.action.startswith(action) if action.endswith(".") else AuditLog.action == action)
    if actor_type:
        conds.append(AuditLog.actor_type == actor_type)
    if resource_type:
        conds.append(AuditLog.resource_type == resource_type)
    if resource_id:
        conds.append(AuditLog.resource_id == resource_id)
    if date_from:
        conds.append(AuditLog.created_at >= date_from)
    if date_to:
        conds.append(AuditLog.created_at <= date_to)
    total = (await db.execute(select(func.count()).select_from(AuditLog).where(*conds))).scalar_one()
    rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(*conds)
                .order_by(AuditLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [entry_out(r) for r in rows],
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": (total + page_size - 1) // page_size,
    }


@router.get("/verify")
async def verify(ctx: RequestContext = Depends(require_perm("audit:read")), db: AsyncSession = Depends(get_db)) -> dict:
    return await verify_chain(db, ctx.org_id)
