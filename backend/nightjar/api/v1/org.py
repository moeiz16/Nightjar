"""Organisation profile and members (API contract §1.4). Invitations, member changes, ingest keys and regulator
contacts are builder A's next step on this router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.permissions import role_label
from nightjar.db.models.platform import Membership, User
from nightjar.schemas.base import CamelIn, Name
from nightjar.services import auth as auth_svc
from nightjar.services.audit import audit

router = APIRouter(prefix="/org", tags=["organisation"])

TimeZone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class OrgPatchIn(CamelIn):
    name: Name | None = None
    timezone: TimeZone | None = None


@router.get("")
async def get_org(ctx: RequestContext = Depends(require_perm("settings:read"))) -> dict:
    assert ctx.org is not None
    return auth_svc.org_out(ctx.org)


@router.patch("")
async def patch_org(
    body: OrgPatchIn, ctx: RequestContext = Depends(require_perm("org:admin")), db: AsyncSession = Depends(get_db)
) -> dict:
    assert ctx.org is not None
    changed = body.model_dump(exclude_unset=True)
    for key, value in changed.items():
        setattr(ctx.org, key, value)
    await audit(db, ctx, action="org.updated", resource_type="org", resource_id=ctx.org.id,
                metadata={"fields": sorted(changed)})  # fmt: skip
    await db.commit()
    return auth_svc.org_out(ctx.org)


@router.get("/members")
async def members(
    ctx: RequestContext = Depends(require_perm("members:read")), db: AsyncSession = Depends(get_db)
) -> dict:
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org_id, Membership.status == "active")
            .order_by(User.full_name)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(m.id),
                "user": auth_svc.user_out(u),
                "role": m.role,
                "roleLabel": role_label(m.role),
                "joinedAt": m.created_at.isoformat() if m.created_at else None,
            }
            for m, u in rows
        ]
    }
