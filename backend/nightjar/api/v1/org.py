"""Organisation, members, invitations and event-source keys (API contract §1.4)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Response, status
from pydantic import EmailStr, StringConstraints, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.config import get_settings
from nightjar.core.deps import RequestContext, get_db, require_perm
from nightjar.core.errors import conflict, not_found
from nightjar.core.permissions import role_label
from nightjar.core.security import hash_token, new_ingest_key, new_link_token
from nightjar.db.models.platform import IngestKey, Invitation, Membership, User, UserSession
from nightjar.schemas.base import CamelIn, Name
from nightjar.services import auth as auth_svc
from nightjar.services.audit import audit

router = APIRouter(prefix="/org", tags=["organisation"])

TimeZone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Role = Literal["admin", "executive", "responder", "analyst", "auditor"]


class OrgPatchIn(CamelIn):
    name: Name | None = None
    timezone: TimeZone | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("Unknown time zone.") from exc
        return v


class RoleIn(CamelIn):
    role: Role


class InviteIn(CamelIn):
    email: EmailStr
    role: Role


class IngestKeyIn(CamelIn):
    name: Name
    source: Literal["webhook", "syslog"] = "webhook"


def member_out(m: Membership, u: User) -> dict:
    return {
        "id": str(m.id),
        "user": auth_svc.user_out(u),
        "role": m.role,
        "roleLabel": role_label(m.role),
        "joinedAt": m.created_at.isoformat() if m.created_at else None,
    }


def invitation_out(inv: Invitation, invited_by: str, accept_url: str | None = None) -> dict:
    out = {
        "id": str(inv.id),
        "email": inv.email,
        "role": inv.role,
        "roleLabel": role_label(inv.role),
        "invitedBy": invited_by,
        "expiresAt": inv.expires_at.isoformat(),
    }
    if accept_url:
        out["acceptUrl"] = accept_url
    return out


def ingest_key_out(k: IngestKey) -> dict:
    return {
        "id": str(k.id),
        "name": k.name,
        "prefix": k.prefix,
        "source": k.source,
        "createdAt": k.created_at.isoformat() if k.created_at else None,
        "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None,
        "revokedAt": k.revoked_at.isoformat() if k.revoked_at else None,
    }


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise not_found(what) from exc


@router.get("")
async def get_org(ctx: RequestContext = Depends(require_perm("settings:read"))) -> dict:
    assert ctx.org is not None
    return auth_svc.org_out(ctx.org)


@router.patch("")
async def patch_org(
    body: OrgPatchIn, ctx: RequestContext = Depends(require_perm("org:admin")), db: AsyncSession = Depends(get_db)
) -> dict:
    assert ctx.org is not None
    changed = body.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changed.items():
        setattr(ctx.org, key, value)
    await audit(
        db, ctx, action="org.updated", resource_type="org", resource_id=ctx.org.id, metadata={"fields": sorted(changed)}
    )
    await db.commit()
    return auth_svc.org_out(ctx.org)


# ── Members ──────────────────────────────────────────────────────────────────
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
    return {"items": [member_out(m, u) for m, u in rows]}


async def _member(db: AsyncSession, ctx: RequestContext, member_id: str) -> tuple[Membership, User]:
    row = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.id == _uuid(member_id, "Member"),
                Membership.org_id == ctx.org_id,
                Membership.status == "active",
            )
        )
    ).first()
    if row is None:
        raise not_found("Member")
    return row[0], row[1]


async def _admin_count(db: AsyncSession, org_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Membership)
                .where(Membership.org_id == org_id, Membership.role == "admin", Membership.status == "active")
            )
        ).scalar_one()
    )


@router.patch("/members/{member_id}")
async def change_role(
    member_id: str,
    body: RoleIn,
    ctx: RequestContext = Depends(require_perm("members:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    m, u = await _member(db, ctx, member_id)
    if m.role == "admin" and body.role != "admin" and await _admin_count(db, ctx.org_id) <= 1:
        raise conflict("conflict", "The organisation needs at least one administrator.")
    old = m.role
    m.role = body.role
    await audit(
        db,
        ctx,
        action="member.role_changed",
        resource_type="member",
        resource_id=m.id,
        metadata={"email": u.email, "from": old, "role": body.role, "roleLabel": role_label(body.role)},
    )
    await db.commit()
    return member_out(m, u)


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: str, ctx: RequestContext = Depends(require_perm("members:manage")), db: AsyncSession = Depends(get_db)
) -> Response:
    m, u = await _member(db, ctx, member_id)
    if m.role == "admin" and await _admin_count(db, ctx.org_id) <= 1:
        raise conflict("conflict", "The organisation needs at least one administrator.")
    m.status = "removed"
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == u.id, UserSession.active_org_id == ctx.org_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=clock.real_now(), revoked_reason="removed")
    )
    await audit(db, ctx, action="member.removed", resource_type="member", resource_id=m.id, metadata={"email": u.email})
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Invitations ──────────────────────────────────────────────────────────────
@router.get("/invitations")
async def list_invitations(
    ctx: RequestContext = Depends(require_perm("members:manage")), db: AsyncSession = Depends(get_db)
) -> dict:
    rows = (
        await db.execute(
            select(Invitation, User.full_name)
            .outerjoin(User, User.id == Invitation.invited_by)
            .where(
                Invitation.org_id == ctx.org_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > clock.real_now(),
            )
            .order_by(Invitation.created_at.desc())
        )
    ).all()
    return {"items": [invitation_out(i, name or "") for i, name in rows]}


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
async def invite(
    body: InviteIn, ctx: RequestContext = Depends(require_perm("members:manage")), db: AsyncSession = Depends(get_db)
) -> dict:
    email = auth_svc.normalize_email(body.email)
    existing = (
        await db.execute(
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org_id, User.email == email, Membership.status == "active")
        )
    ).first()
    if existing:
        raise conflict("conflict", "This person is already a member.")
    s = get_settings()
    token = new_link_token()
    inv = Invitation(
        org_id=ctx.org_id,
        email=email,
        role=body.role,
        token_hash=hash_token(token),
        invited_by=ctx.user.id,
        expires_at=clock.real_now() + timedelta(days=s.invitation_ttl_days),
    )
    db.add(inv)
    await db.flush()
    await audit(
        db,
        ctx,
        action="invitation.created",
        resource_type="invitation",
        resource_id=inv.id,
        metadata={"email": email, "role": body.role, "roleLabel": role_label(body.role)},
    )
    await db.commit()
    return invitation_out(inv, ctx.user.full_name, accept_url=f"{s.public_app_url}/invite/{token}")


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: str,
    ctx: RequestContext = Depends(require_perm("members:manage")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    inv = await db.get(Invitation, _uuid(invitation_id, "Invitation"))
    if inv is None or inv.org_id != ctx.org_id or inv.accepted_at or inv.revoked_at:
        raise not_found("Invitation")
    inv.revoked_at = clock.real_now()
    await audit(
        db,
        ctx,
        action="invitation.revoked",
        resource_type="invitation",
        resource_id=inv.id,
        metadata={"email": inv.email},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Event-source (ingest) keys ───────────────────────────────────────────────
@router.get("/ingest-keys")
async def list_keys(
    ctx: RequestContext = Depends(require_perm("ingest_keys:manage")), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        await db.execute(select(IngestKey).where(IngestKey.org_id == ctx.org_id).order_by(IngestKey.created_at.desc()))
    ).scalars()
    return [ingest_key_out(k) for k in rows]


@router.post("/ingest-keys", status_code=status.HTTP_201_CREATED)
async def create_key(
    body: IngestKeyIn,
    ctx: RequestContext = Depends(require_perm("ingest_keys:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    secret, prefix = new_ingest_key()
    k = IngestKey(
        org_id=ctx.org_id,
        name=body.name,
        prefix=prefix,
        key_hash=hash_token(secret),
        source=body.source,
        created_by=ctx.user.id,
    )
    db.add(k)
    await db.flush()
    await audit(
        db,
        ctx,
        action="ingest_key.created",
        resource_type="ingest_key",
        resource_id=k.id,
        metadata={"name": k.name, "source": k.source, "prefix": prefix},
    )
    await db.commit()
    return {"key": ingest_key_out(k), "secret": secret}


@router.delete("/ingest-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: str, ctx: RequestContext = Depends(require_perm("ingest_keys:manage")), db: AsyncSession = Depends(get_db)
) -> Response:
    k = await db.get(IngestKey, _uuid(key_id, "Key"))
    if k is None or k.org_id != ctx.org_id:
        raise not_found("Key")
    if k.revoked_at is None:
        k.revoked_at = clock.real_now()
        await audit(
            db,
            ctx,
            action="ingest_key.revoked",
            resource_type="ingest_key",
            resource_id=k.id,
            metadata={"name": k.name},
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
