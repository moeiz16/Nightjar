"""Authentication flows: sign-up, sign-in with lockout, server-side sessions, organisation switching, `Me`."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nightjar import clock
from nightjar.core.config import get_settings
from nightjar.core.errors import AppError, conflict, unauthorized
from nightjar.core.permissions import permissions_for, role_label
from nightjar.core.security import (
    hash_password,
    hash_token,
    needs_rehash,
    new_session_token,
    validate_password_strength,
    verify_password,
)
from nightjar.db.models.platform import Membership, Organization, User, UserSession
from nightjar.services.audit import audit

log = structlog.get_logger(__name__)

AVATAR_COLORS = ("#2EE6C5", "#7FB4FF", "#F2B84B", "#B7A6FF", "#FF8F70", "#8BD17C", "#E07BD9", "#5FD3F3")
DEFAULT_ORG_SETTINGS: dict = {
    "regimeContacts": {},  # filled from Settings → regulator contacts (§1.16)
    "crownJewelAssetTags": ["crown-jewel"],
    "autoInvestigate": True,
    "weeklyBriefDay": "monday",
}


@dataclass
class IssuedSession:
    token: str
    session: UserSession


# ── Lockout: N failures for one email inside the window pause sign-in for that email ──
_failures: dict[str, deque[datetime]] = defaultdict(deque)


def _lockout_check(email: str) -> None:
    s = get_settings()
    window = timedelta(minutes=s.login_lockout_minutes)
    q = _failures[email]
    now = clock.real_now()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= s.login_lockout_failures:
        retry = int((window - (now - q[0])).total_seconds()) + 1
        raise AppError(
            429,
            "rate_limited",
            "Too many attempts",
            f"Sign-in for this account is paused. Try again in {max(retry // 60, 1)} minutes.",
            headers={"Retry-After": str(retry)},
        )


def reset_lockouts() -> None:
    _failures.clear()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "org"


async def unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    slug, n = base, 1
    while (await db.execute(select(Organization.id).where(Organization.slug == slug))).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == normalize_email(email)))).scalar_one_or_none()


async def user_orgs(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[Organization, Membership]]:
    rows = (
        await db.execute(
            select(Organization, Membership)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == user_id, Membership.status == "active")
            .order_by(Membership.created_at)
        )
    ).all()
    return [(o, m) for o, m in rows]


async def create_user(db: AsyncSession, *, email: str, password: str, full_name: str, title: str | None = None) -> User:
    email = normalize_email(email)
    if await get_user_by_email(db, email):
        raise conflict("email_taken", "An account with this email already exists.")
    validate_password_strength(password, email=email)
    user = User(
        email=email,
        full_name=full_name.strip(),
        title=title,
        password_hash=hash_password(password),
        avatar_color=AVATAR_COLORS[int(hashlib.sha256(email.encode()).hexdigest(), 16) % len(AVATAR_COLORS)],
        preferences={},
    )
    db.add(user)
    await db.flush()
    return user


async def create_org(
    db: AsyncSession, *, name: str, owner: User, timezone: str = "UTC", profile: dict | None = None
) -> Organization:
    org = Organization(
        name=name.strip(),
        slug=await unique_slug(db, name),
        timezone=timezone,
        profile=profile or {},
        settings=dict(DEFAULT_ORG_SETTINGS),
    )
    db.add(org)
    await db.flush()
    db.add(Membership(org_id=org.id, user_id=owner.id, role="admin", status="active"))
    await db.flush()
    return org


async def pick_active_org(db: AsyncSession, user: User) -> uuid.UUID | None:
    orgs = await user_orgs(db, user.id)
    if not orgs:
        return None
    last = (user.preferences or {}).get("lastOrgId")
    for org, _m in orgs:
        if str(org.id) == last:
            return org.id
    return orgs[0][0].id


async def authenticate(db: AsyncSession, *, email: str, password: str, ip: str | None, user_agent: str | None) -> User:
    email = normalize_email(email)
    _lockout_check(email)
    user = await get_user_by_email(db, email)
    ok = verify_password(password, user.password_hash if user else None)
    if not user or not ok or not user.is_active:
        _failures[email].append(clock.real_now())
        if user:
            for org, _m in await user_orgs(db, user.id):
                await audit(
                    db,
                    None,
                    org_id=org.id,
                    action="auth.login_failed",
                    resource_type="user",
                    resource_id=user.id,
                    actor_id=user.id,
                    actor_name=user.full_name,
                    ip=ip,
                    user_agent=user_agent,
                )
            await db.commit()  # keep the failed-attempt record even though the request errors
        raise unauthorized("Incorrect email or password.", code="invalid_credentials")
    _failures.pop(email, None)
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = clock.now()
    return user


async def issue_session(
    db: AsyncSession, user: User, *, org_id: uuid.UUID | None, ip: str | None, user_agent: str | None
) -> IssuedSession:
    s = get_settings()
    now = clock.real_now()
    token = new_session_token()
    sess = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        active_org_id=org_id,
        ip=ip,
        user_agent=(user_agent or "")[:400] or None,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=s.session_idle_minutes),
        absolute_expires_at=now + timedelta(hours=s.session_absolute_hours),
    )
    db.add(sess)
    await db.flush()
    return IssuedSession(token, sess)


async def rotate_session(db: AsyncSession, sess: UserSession) -> IssuedSession:
    """`POST /auth/refresh`: new token for the same session (fixation defence); limits unchanged."""
    token = new_session_token()
    sess.token_hash = hash_token(token)
    now = clock.real_now()
    sess.last_seen_at = now
    sess.idle_expires_at = min(now + timedelta(minutes=get_settings().session_idle_minutes), sess.absolute_expires_at)
    await db.flush()
    return IssuedSession(token, sess)


async def revoke_session(db: AsyncSession, session_id: uuid.UUID, reason: str = "logout") -> None:
    await db.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=clock.real_now(), revoked_reason=reason)
    )


async def revoke_other_sessions(db: AsyncSession, user_id: uuid.UUID, keep: uuid.UUID | None) -> None:
    stmt = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if keep:
        stmt = stmt.where(UserSession.id != keep)
    await db.execute(stmt.values(revoked_at=clock.real_now(), revoked_reason="password_changed"))


# ── Serialisers (shapes in API contract §1.2) ────────────────────────────────
def user_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "fullName": user.full_name,
        "title": user.title,
        "avatarColor": user.avatar_color,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
    }


def org_out(org: Organization) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "timezone": org.timezone,
        "profile": org.profile or {},
        "settings": {**DEFAULT_ORG_SETTINGS, **(org.settings or {})},
        "isSample": org.is_sample,
        "createdAt": org.created_at.isoformat() if org.created_at else None,
    }


def org_summary(org: Organization, membership: Membership) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "role": membership.role,
        "roleLabel": role_label(membership.role),
    }


def session_out(sess: UserSession) -> dict:
    return {
        "expiresAt": sess.idle_expires_at.isoformat(),
        "absoluteExpiresAt": sess.absolute_expires_at.isoformat(),
    }


async def me_payload(db: AsyncSession, user: User, sess: UserSession) -> dict:
    orgs = await user_orgs(db, user.id)
    active = next(((o, m) for o, m in orgs if o.id == sess.active_org_id), None)
    membership = None
    if active:
        m = active[1]
        membership = {"role": m.role, "roleLabel": role_label(m.role), "permissions": permissions_for(m.role)}
    return {
        "user": user_out(user),
        "activeOrg": org_out(active[0]) if active else None,
        "membership": membership,
        "orgs": [org_summary(o, m) for o, m in orgs],
        "session": session_out(sess),
        "clock": {"today": clock.today().isoformat(), "demo": clock.is_shifted()},
    }
