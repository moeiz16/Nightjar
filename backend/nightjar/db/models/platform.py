"""Platform tables (builder A): organisations, users, memberships, invitations, sessions, ingest keys, the audit
hash chain, the live-update outbox and long-running jobs. Domain tables live in `soc.py` (see API contract
Part 2)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nightjar.db.base import (
    Base,
    JSONType,
    OrgScopedMixin,
    ShortStr,
    TimestampMixin,
    UTCDateTime,
    UUIDPkMixin,
    UUIDType,
    utcnow,
)


class Organization(UUIDPkMixin, TimestampMixin, Base):
    """A customer company. `profile` holds the regulatory profile (`OrgProfile` in contracts.py): legal
    entities per jurisdiction, NIS2 sector/entity type, SEC registrant status, SOCI asset classes."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(ShortStr)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    profile: Mapped[dict] = mapped_column(JSONType, default=dict)
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(ShortStr)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    avatar_color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    preferences: Mapped[dict] = mapped_column(JSONType, default=dict)


class Membership(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | removed
    # Hand-over layouts and "since you last looked" for the shift log
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Invitation(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "invitations"

    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class UserSession(UUIDPkMixin, Base):
    """Server-side session behind the `nj_session` cookie. Timers use the real clock."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active_org_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    idle_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    absolute_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class IngestKey(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    """Per-org key for webhook/syslog senders (`X-Nightjar-Key`). Only the HMAC is stored."""

    __tablename__ = "ingest_keys"

    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(32), default="webhook")  # webhook | syslog
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AuditLog(Base):
    """Append-only, per-org SHA-256 hash chain (see services/audit.py). Agents and workers append as
    `actor_type="agent"` / `"system"` with `actor_name` set (e.g. "Triage agent")."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_org_id_id", "org_id", "id"),
        Index("ix_audit_log_org_resource", "org_id", "resource_type", "resource_id"),
        # One successor per link: concurrent appenders (API + workers on SQLite) cannot fork the chain; the
        # loser gets an IntegrityError and retries (HTTP 503 / NATS redelivery).
        UniqueConstraint("org_id", "prev_hash", name="uq_audit_log_org_prev_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="user")  # user | agent | system
    actor_name: Mapped[str] = mapped_column(String(200), default="System")
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class UiEvent(Base):
    """Live-update outbox. Any process (API, workers, agents) appends; the API's SSE endpoint tails it. The
    auto id is the SSE `id:` so browsers resume with Last-Event-ID."""

    __tablename__ = "ui_events"
    __table_args__ = (Index("ix_ui_events_org_id_id", "org_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType)
    name: Mapped[str] = mapped_column(String(60))
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    permission: Mapped[str | None] = mapped_column(String(40), nullable=True)
    user_ids: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class Job(UUIDPkMixin, TimestampMixin, Base):
    """Long-running work started from the API (replays, weekly report, model training). Executed by the
    worker process from the `nj.jobs.<kind>` subject; progress is mirrored to SSE `job.updated`."""

    __tablename__ = "jobs"

    org_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|succeeded|failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
