"""Public invitation links (API contract §1.4): look up and accept."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.api.v1.auth import set_session_cookies
from nightjar.core.deps import check_csrf, client_ip, get_db
from nightjar.core.errors import AppError, not_found, unauthorized
from nightjar.core.permissions import role_label
from nightjar.core.security import hash_token, verify_password
from nightjar.db.models.platform import Invitation, Membership, Organization
from nightjar.schemas.base import CamelIn, Name, Password
from nightjar.services import auth as auth_svc
from nightjar.services.audit import audit

router = APIRouter(prefix="/invitations", tags=["organisation"])


class AcceptIn(CamelIn):
    full_name: Name | None = None
    password: Password | None = None


async def _load(db: AsyncSession, token: str) -> tuple[Invitation, Organization]:
    inv = (await db.execute(select(Invitation).where(Invitation.token_hash == hash_token(token)))).scalar_one_or_none()
    if inv is None or inv.revoked_at is not None or inv.accepted_at is not None:
        raise not_found("Invitation")
    if inv.expires_at < clock.real_now():
        raise AppError(410, "expired", "Invitation expired", "This invitation has expired. Ask for a new one.")
    org = await db.get(Organization, inv.org_id)
    if org is None:
        raise not_found("Invitation")
    return inv, org


@router.get("/{token}")
async def lookup(token: str, db: AsyncSession = Depends(get_db)) -> dict:
    inv, org = await _load(db, token)
    return {
        "orgName": org.name,
        "email": inv.email,
        "role": inv.role,
        "roleLabel": role_label(inv.role),
        "expiresAt": inv.expires_at.isoformat(),
        "accountExists": await auth_svc.get_user_by_email(db, inv.email) is not None,
    }


@router.post("/{token}/accept")
async def accept(
    token: str, body: AcceptIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    check_csrf(request)
    inv, org = await _load(db, token)
    ip, ua = client_ip(request), request.headers.get("user-agent")
    user = await auth_svc.get_user_by_email(db, inv.email)
    if user is None:
        if not body.full_name or not body.password:
            raise AppError(
                422,
                "validation_error",
                "Validation failed",
                "Enter your name and choose a password.",
                errors=[{"field": "fullName", "message": "Required."}, {"field": "password", "message": "Required."}],
            )
        user = await auth_svc.create_user(db, email=inv.email, password=body.password, full_name=body.full_name)
    elif not body.password or not verify_password(body.password, user.password_hash):
        raise unauthorized("Incorrect email or password.", code="invalid_credentials")
    membership = (
        await db.execute(select(Membership).where(Membership.org_id == org.id, Membership.user_id == user.id))
    ).scalar_one_or_none()
    if membership is None:
        db.add(Membership(org_id=org.id, user_id=user.id, role=inv.role, status="active"))
    else:
        membership.status, membership.role = "active", inv.role
    inv.accepted_at = clock.real_now()
    user.preferences = {**(user.preferences or {}), "lastOrgId": str(org.id)}
    await audit(
        db,
        None,
        org_id=org.id,
        actor_id=user.id,
        actor_name=user.full_name,
        action="invitation.accepted",
        resource_type="invitation",
        resource_id=inv.id,
        ip=ip,
        user_agent=ua,
        metadata={"email": inv.email, "role": inv.role, "roleLabel": role_label(inv.role)},
    )
    issued = await auth_svc.issue_session(db, user, org_id=org.id, ip=ip, user_agent=ua)
    await db.commit()
    set_session_cookies(response, issued.token)
    return await auth_svc.me_payload(db, user, issued.session)
