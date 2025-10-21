"""Domain tables (builder A): inventory, detections, incidents, investigations, response approvals, tickets, the
incident clock, hunting saved searches, weekly briefs and model promotions. API contract Part 2.

Every tenant table carries `org_id`; every query filters on the caller's org. Append-only logs (timeline rows,
agent steps, sign-in history) use integer ids; everything else UUIDs.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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

_IntPk = BigInteger().with_variant(Integer, "sqlite")


# ── Inventory: asset inventory and identity directory ─────────────────────────
class Asset(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_assets_org_name"),)

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))
    ips: Mapped[list] = mapped_column(JSONType, default=list)
    os: Mapped[str | None] = mapped_column(String(120), nullable=True)
    site: Mapped[str] = mapped_column(String(120), default="")
    jurisdiction: Mapped[str] = mapped_column(String(4), default="EU")
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    owner_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    business_service: Mapped[str | None] = mapped_column(String(200), nullable=True)
    criticality: Mapped[str] = mapped_column(String(16), default="medium")
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    isolated: Mapped[bool] = mapped_column(Boolean, default=False)
    isolated_by_approval_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    vulnerabilities: Mapped[list] = mapped_column(JSONType, default=list)


class Identity(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("org_id", "username", name="uq_identities_org_username"),)

    username: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str] = mapped_column(ShortStr)
    email: Mapped[str] = mapped_column(String(320), default="")
    department: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(String(160), default="")
    jurisdiction: Mapped[str] = mapped_column(String(4), default="EU")
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    manager: Mapped[str | None] = mapped_column(String(120), nullable=True)
    privileged: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa: Mapped[str] = mapped_column(String(16), default="push")
    usual_countries: Mapped[list] = mapped_column(JSONType, default=list)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(16), default="employee")
    disabled_by_approval_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    sessions_revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    password_reset_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SignInHistory(Base):
    __tablename__ = "sign_in_history"
    __table_args__ = (Index("ix_sign_in_history_org_user_day", "org_id", "username", "day"),)

    id: Mapped[int] = mapped_column(_IntPk, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType)
    username: Mapped[str] = mapped_column(String(120))
    day: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(2))
    count: Mapped[int] = mapped_column(Integer, default=1)


class IpBlock(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "ip_blocks"
    __table_args__ = (UniqueConstraint("org_id", "ip", name="uq_ip_blocks_org_ip"),)

    ip: Mapped[str] = mapped_column(String(64))
    blocked: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)


class AccessKey(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "access_keys"
    __table_args__ = (UniqueConstraint("org_id", "key_id", name="uq_access_keys_org_key"),)

    key_id: Mapped[str] = mapped_column(String(128))
    identity: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(16), default="aws")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | revoked
    revoked_by_approval_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)


class QuarantinedMessage(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "quarantined_messages"
    __table_args__ = (UniqueConstraint("org_id", "message_id", name="uq_quarantined_messages_org_msg"),)

    message_id: Mapped[str] = mapped_column(String(255))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)


# ── Detections, triage scores, behaviour anomalies (rows written by B through incidents.api) ───────
class DetectionRow(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "detections"
    __table_args__ = (Index("ix_detections_org_last_seen", "org_id", "last_seen"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    rule_id: Mapped[str] = mapped_column(String(200), index=True)
    rule_title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(12))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    attack: Mapped[list] = mapped_column(JSONType, default=list)
    entities: Mapped[dict] = mapped_column(JSONType, default=dict)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime())
    event_ids: Mapped[list] = mapped_column(JSONType, default=list)
    evidence: Mapped[list] = mapped_column(JSONType, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[dict] = mapped_column(JSONType, default=dict)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True, index=True)


class TriageScoreRow(Base):
    __tablename__ = "triage_scores"

    detection_id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    probability: Mapped[float] = mapped_column(Float)
    band: Mapped[str] = mapped_column(String(16))
    threshold: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list] = mapped_column(JSONType, default=list)
    model_version: Mapped[str] = mapped_column(String(64))
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AnomalyRow(OrgScopedMixin, Base):
    __tablename__ = "anomalies"
    __table_args__ = (Index("ix_anomalies_org_entity", "org_id", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(8))
    entity_id: Mapped[str] = mapped_column(String(200))
    window_start: Mapped[datetime] = mapped_column(UTCDateTime())
    window_end: Mapped[datetime] = mapped_column(UTCDateTime())
    score: Mapped[float] = mapped_column(Float)
    isolation_score: Mapped[float] = mapped_column(Float, default=0.0)
    robust_z: Mapped[dict] = mapped_column(JSONType, default=dict)
    features: Mapped[dict] = mapped_column(JSONType, default=dict)
    reasons: Mapped[list] = mapped_column(JSONType, default=list)
    model_version: Mapped[str] = mapped_column(String(64), default="")
    detection_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ── Incidents and their timeline ─────────────────────────────────────────────
class Incident(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("org_id", "reference", name="uq_incidents_org_reference"),
        UniqueConstraint("org_id", "correlation_key", name="uq_incidents_org_correlation_key"),
        Index("ix_incidents_org_status", "org_id", "status"),
    )

    number: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(16))
    correlation_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(16), default="new")
    stage: Mapped[str] = mapped_column(String(16), default="triage")
    summary: Mapped[str] = mapped_column(Text, default="")
    attack: Mapped[list] = mapped_column(JSONType, default=list)
    entities: Mapped[dict] = mapped_column(JSONType, default=dict)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime())
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    likely_benign: Mapped[bool] = mapped_column(Boolean, default=False)
    max_triage_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verdict_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    triage_run_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    triaged_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    contained_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolution: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    facts: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class IncidentEvent(Base):
    """Timeline rows (`TimelineEntry`), append-only."""

    __tablename__ = "incident_events"
    __table_args__ = (Index("ix_incident_events_incident_at", "incident_id", "at"),)

    id: Mapped[int] = mapped_column(_IntPk, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(UUIDType)
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    kind: Mapped[str] = mapped_column(String(16))
    actor_type: Mapped[str] = mapped_column(String(8), default="system")
    actor_name: Mapped[str] = mapped_column(String(200), default="Nightjar SOC")
    title: Mapped[str] = mapped_column(String(400))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    executive: Mapped[bool] = mapped_column(Boolean, default=False)


# ── Investigations (agent runs) and the replayable step log ──────────────────
class InvestigationRun(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "investigation_runs"

    incident_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    trigger: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(12), default="queued")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    verdict: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    enrichment: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    proposals: Mapped[list] = mapped_column(JSONType, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentStepRow(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_agent_steps_run_seq"),)

    id: Mapped[int] = mapped_column(_IntPk, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType)
    seq: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(400))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool: Mapped[str | None] = mapped_column(String(120), nullable=True)
    a2a_task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flags: Mapped[list] = mapped_column(JSONType, default=list)
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)


# ── Response actions and the approval gate (ADR 0006) ────────────────────────
class ActionProposal(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "action_proposals"
    __table_args__ = (Index("ix_action_proposals_org_status", "org_id", "status"),)

    incident_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(255))
    target_type: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text, default="")
    expected_effect: Mapped[str] = mapped_column(Text, default="")
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)
    destructive: Mapped[bool] = mapped_column(Boolean, default=True)
    crown_jewel: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    proposed_by_type: Mapped[str] = mapped_column(String(8), default="agent")
    proposed_by_name: Mapped[str] = mapped_column(String(200), default="Response colleague")
    proposed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    required_roles: Mapped[list] = mapped_column(JSONType, default=list)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    token: Mapped[str | None] = mapped_column(String(600), nullable=True)
    token_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    token_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class ActionApproval(UUIDPkMixin, Base):
    __tablename__ = "action_approvals"
    __table_args__ = (UniqueConstraint("proposal_id", "role", name="uq_action_approvals_proposal_role"),)

    proposal_id: Mapped[uuid.UUID] = mapped_column(UUIDType, ForeignKey("action_proposals.id", ondelete="CASCADE"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDType)
    user_name: Mapped[str] = mapped_column(ShortStr)
    role: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(8))  # approve | reject
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime())


# ── Built-in service desk ────────────────────────────────────────────────────
class Ticket(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_tickets_org_key"),)

    incident_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    number: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(4), default="p3")
    assignee_team: Mapped[str] = mapped_column(String(120), default="Security operations")
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_by_type: Mapped[str] = mapped_column(String(8), default="agent")
    created_by_name: Mapped[str] = mapped_column(String(200), default="Response colleague")


class TicketComment(UUIDPkMixin, Base):
    __tablename__ = "ticket_comments"

    ticket_id: Mapped[uuid.UUID] = mapped_column(UUIDType, ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType)
    author: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ── Incident clock: regime assessments, deadlines, notices ───────────────────
class RegimeAssessmentRow(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "regime_assessments"
    __table_args__ = (
        UniqueConstraint("incident_id", "regime", "entity_id", name="uq_regime_assessments_incident_regime_entity"),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    regime: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[str] = mapped_column(String(40))
    proposal: Mapped[dict] = mapped_column(JSONType, default=dict)
    determination: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class DeadlineRow(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "deadlines"
    __table_args__ = (Index("ix_deadlines_org_active", "org_id", "active"),)

    incident_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    regime: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[str] = mapped_column(String(40))
    notice_type: Mapped[str] = mapped_column(String(40))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    due_at: Mapped[datetime] = mapped_column(UTCDateTime())
    timezone: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="on_track")  # last state announced over SSE
    notice_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    basis: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Notice(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "notices"

    incident_id: Mapped[uuid.UUID] = mapped_column(UUIDType, index=True)
    regime: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[str] = mapped_column(String(40))
    type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    sections: Mapped[list] = mapped_column(JSONType, default=list)
    deadline_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    submission: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    generated_by: Mapped[str] = mapped_column(String(80), default="offline")


class NoticeVersion(UUIDPkMixin, Base):
    __tablename__ = "notice_versions"
    __table_args__ = (UniqueConstraint("notice_id", "version", name="uq_notice_versions_notice_version"),)

    notice_id: Mapped[uuid.UUID] = mapped_column(UUIDType, ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUIDType)
    version: Mapped[int] = mapped_column(Integer)
    sections: Mapped[list] = mapped_column(JSONType, default=list)
    edited_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    edited_by_name: Mapped[str] = mapped_column(String(200), default="Nightjar SOC")
    edited_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ── Hunting, executive briefs, model promotions ──────────────────────────────
class SavedSearch(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "saved_searches"

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    query: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WeeklyBrief(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "weekly_briefs"
    __table_args__ = (UniqueConstraint("org_id", "week", name="uq_weekly_briefs_org_week"),)

    week: Mapped[str] = mapped_column(String(10))
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    headline: Mapped[str] = mapped_column(String(400))
    sections: Mapped[list] = mapped_column(JSONType, default=list)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    incident_ids: Mapped[list] = mapped_column(JSONType, default=list)
    generated_by: Mapped[str] = mapped_column(String(16), default="offline")
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ModelPromotion(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "model_promotions"

    model_name: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(16), default="champion")
    promoted_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
