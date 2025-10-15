"""Shared FastAPI dependencies: database session, authenticated request context, RBAC.

@router.get("/incidents")
async def list_incidents(ctx: Ctx = Depends(require_perm("incidents:read")), db: AsyncSession = Depends(get_db)):
    ...  # every query filters on ctx.org_id
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.clientip import resolve_client_ip
from nightjar.core.config import get_settings
from nightjar.core.errors import AppError, forbidden, unauthorized
from nightjar.core.logging import request_id_ctx
from nightjar.core.permissions import permissions_for
from nightjar.core.security import hash_token, tokens_equal
from nightjar.db.models.platform import Membership, Organization, User, UserSession
from nightjar.db.session import get_db

__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "RequestContext",
    "check_csrf",
    "client_ip",
    "get_ctx",
    "get_db",
    "get_optional_ctx",
    "get_user_ctx",
    "require_any",
    "require_perm",
]

SESSION_COOKIE = "nj_session"
CSRF_COOKIE = "nj_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_TOUCH_EVERY = timedelta(seconds=60)


@dataclass
class RequestContext:
    user: User
    org: Organization | None
    membership: Membership | None
    role: str | None
    session: UserSession
    request_id: str | None
    ip: str | None
    user_agent: str | None
    permissions: set[str] = field(default_factory=set)
    actor_type: str = "user"

    @property
    def org_id(self) -> uuid.UUID:
        if not self.org:
            raise AppError(403, "no_active_org", "No active organisation", "Join or create an organisation first.")
        return self.org.id

    def has(self, perm: str) -> bool:
        return perm in self.permissions


def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    return resolve_client_ip(peer, request.headers.get("x-forwarded-for"))


def check_csrf(request: Request) -> None:
    """Double-submit check for every unsafe method (the session is always a cookie)."""
    if request.method in _SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not tokens_equal(cookie, header):
        raise AppError(403, "csrf_failed", "Security check failed", "Please refresh the page and try again.")


async def load_session(db: AsyncSession, token: str) -> UserSession:
    """Valid, unrevoked, inside idle and absolute limits (real clock); slides the idle expiry."""
    sess = (
        await db.execute(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    now = clock.real_now()
    if sess is None or sess.revoked_at is not None:
        raise unauthorized("Please sign in.", code="not_authenticated")
    if sess.idle_expires_at < now or sess.absolute_expires_at < now:
        raise unauthorized("Your session has expired. Please sign in again.", code="session_expired")
    if now - sess.last_seen_at >= _TOUCH_EVERY:
        s = get_settings()
        sess.last_seen_at = now
        sess.idle_expires_at = min(now + timedelta(minutes=s.session_idle_minutes), sess.absolute_expires_at)
    return sess


async def _resolve(request: Request, db: AsyncSession, *, optional: bool) -> RequestContext | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        if optional:
            return None
        raise unauthorized("Please sign in.", code="not_authenticated")
    try:
        sess = await load_session(db, token)
    except AppError:
        if optional:
            return None
        raise
    check_csrf(request)
    stmt = select(User, Membership, Organization).select_from(User).where(User.id == sess.user_id)
    stmt = stmt.outerjoin(
        Membership,
        and_(
            Membership.org_id == sess.active_org_id,
            Membership.user_id == User.id,
            Membership.status == "active",
        ),
    ).outerjoin(Organization, Organization.id == Membership.org_id)
    row = (await db.execute(stmt)).first()
    if row is None or not row[0].is_active:
        raise unauthorized("This account is no longer active.", code="not_authenticated")
    user, membership, org = row
    role = membership.role if membership else None
    return RequestContext(
        user=user,
        org=org if membership else None,
        membership=membership,
        role=role,
        session=sess,
        request_id=request_id_ctx.get(),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        permissions=set(permissions_for(role)),
    )


async def get_optional_ctx(request: Request, db: AsyncSession = Depends(get_db)) -> RequestContext | None:
    return await _resolve(request, db, optional=True)


async def get_user_ctx(request: Request, db: AsyncSession = Depends(get_db)) -> RequestContext:
    """Authenticated user; an active organisation is NOT required."""
    ctx = await _resolve(request, db, optional=False)
    assert ctx is not None
    return ctx


async def get_ctx(ctx: RequestContext = Depends(get_user_ctx)) -> RequestContext:
    """Authenticated user with an active organisation membership."""
    if not ctx.org:
        raise AppError(403, "no_active_org", "No active organisation", "Join or create an organisation first.")
    return ctx


def require_perm(perm: str) -> Callable[..., Coroutine[Any, Any, RequestContext]]:
    async def _dep(ctx: RequestContext = Depends(get_ctx)) -> RequestContext:
        if not ctx.has(perm):
            raise forbidden()
        return ctx

    _dep.__name__ = f"require_{perm.replace(':', '_')}"
    return _dep


def require_any(*perms: str) -> Callable[..., Coroutine[Any, Any, RequestContext]]:
    async def _dep(ctx: RequestContext = Depends(get_ctx)) -> RequestContext:
        if not any(ctx.has(p) for p in perms):
            raise forbidden()
        return ctx

    _dep.__name__ = "require_any_" + "_".join(p.replace(":", "_") for p in perms)
    return _dep
