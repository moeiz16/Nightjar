"""Authentication endpoints (API contract §1.2): httpOnly session cookie + double-submit CSRF."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar.core.config import get_settings
from nightjar.core.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    RequestContext,
    check_csrf,
    client_ip,
    get_db,
    get_user_ctx,
)
from nightjar.core.errors import not_found
from nightjar.core.security import new_csrf_token, validate_password_strength, verify_password
from nightjar.schemas.base import CamelIn, Name, Password
from nightjar.services import auth as auth_svc
from nightjar.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupIn(CamelIn):
    email: EmailStr
    password: Password
    full_name: Name
    org_name: Name


class LoginIn(CamelIn):
    email: EmailStr
    password: Password


class SwitchOrgIn(CamelIn):
    org_id: str


class PasswordIn(CamelIn):
    current_password: Password
    new_password: Password


def _cookie_common() -> dict[str, Any]:
    s = get_settings()
    return {"secure": s.cookie_secure, "samesite": "lax", "domain": s.cookie_domain, "path": "/"}


def set_session_cookies(response: Response, token: str) -> None:
    s = get_settings()
    max_age = s.session_absolute_hours * 3600
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, **_cookie_common())
    response.set_cookie(CSRF_COOKIE, new_csrf_token(), max_age=max_age, httponly=False, **_cookie_common())


def clear_session_cookies(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(SESSION_COOKIE, path="/", domain=s.cookie_domain)


@router.get("/csrf", status_code=status.HTTP_204_NO_CONTENT)
async def csrf(request: Request) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if not request.cookies.get(CSRF_COOKIE):
        response.set_cookie(CSRF_COOKIE, new_csrf_token(), httponly=False, **_cookie_common())
    return response


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    check_csrf(request)
    ip, ua = client_ip(request), request.headers.get("user-agent")
    user = await auth_svc.create_user(db, email=body.email, password=body.password, full_name=body.full_name)
    org = await auth_svc.create_org(db, name=body.org_name, owner=user)
    for action, rtype, rid, meta in (
        ("org.created", "org", org.id, {"name": org.name}),
        ("auth.signup", "user", user.id, {}),
    ):
        await audit(
            db, None, org_id=org.id, actor_id=user.id, actor_name=user.full_name, action=action,
            resource_type=rtype, resource_id=rid, metadata=meta, ip=ip, user_agent=ua,
        )  # fmt: skip
    issued = await auth_svc.issue_session(db, user, org_id=org.id, ip=ip, user_agent=ua)
    await db.commit()
    set_session_cookies(response, issued.token)
    return await auth_svc.me_payload(db, user, issued.session)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    check_csrf(request)
    ip, ua = client_ip(request), request.headers.get("user-agent")
    user = await auth_svc.authenticate(db, email=body.email, password=body.password, ip=ip, user_agent=ua)
    org_id = await auth_svc.pick_active_org(db, user)
    issued = await auth_svc.issue_session(db, user, org_id=org_id, ip=ip, user_agent=ua)
    if org_id:
        await audit(
            db, None, org_id=org_id, actor_id=user.id, actor_name=user.full_name, action="auth.login",
            resource_type="user", resource_id=user.id, ip=ip, user_agent=ua,
        )  # fmt: skip
    await db.commit()
    set_session_cookies(response, issued.token)
    return await auth_svc.me_payload(db, user, issued.session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(ctx: RequestContext = Depends(get_user_ctx), db: AsyncSession = Depends(get_db)) -> Response:
    await auth_svc.revoke_session(db, ctx.session.id)
    if ctx.org:
        await audit(db, ctx, action="auth.logout", resource_type="user", resource_id=ctx.user.id)
    await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response)
    return response


@router.post("/refresh")
async def refresh(
    response: Response, ctx: RequestContext = Depends(get_user_ctx), db: AsyncSession = Depends(get_db)
) -> dict:
    issued = await auth_svc.rotate_session(db, ctx.session)
    await db.commit()
    set_session_cookies(response, issued.token)
    return auth_svc.session_out(issued.session)


@router.get("/me")
async def me(ctx: RequestContext = Depends(get_user_ctx), db: AsyncSession = Depends(get_db)) -> dict:
    return await auth_svc.me_payload(db, ctx.user, ctx.session)


@router.post("/switch-org")
async def switch_org(
    body: SwitchOrgIn, ctx: RequestContext = Depends(get_user_ctx), db: AsyncSession = Depends(get_db)
) -> dict:
    rows = await auth_svc.user_orgs(db, ctx.user.id)
    match = next(((o, m) for o, m in rows if str(o.id) == body.org_id), None)
    if not match:
        raise not_found("Organisation")
    org, _member = match
    ctx.user.preferences = {**(ctx.user.preferences or {}), "lastOrgId": str(org.id)}
    ctx.session.active_org_id = org.id
    await audit(db, ctx, org_id=org.id, action="auth.switch_org", resource_type="org", resource_id=org.id)
    await db.commit()
    return await auth_svc.me_payload(db, ctx.user, ctx.session)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordIn, ctx: RequestContext = Depends(get_user_ctx), db: AsyncSession = Depends(get_db)
) -> Response:
    from nightjar.core.errors import validation_error
    from nightjar.core.security import hash_password

    if not verify_password(body.current_password, ctx.user.password_hash):
        raise validation_error(
            "Your current password is incorrect.",
            [{"field": "currentPassword", "message": "Incorrect password."}],
        )
    validate_password_strength(body.new_password, email=ctx.user.email)
    ctx.user.password_hash = hash_password(body.new_password)
    await auth_svc.revoke_other_sessions(db, ctx.user.id, keep=ctx.session.id)
    if ctx.org:
        await audit(db, ctx, action="auth.password_changed", resource_type="user", resource_id=ctx.user.id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
