"""Pipeline tables (builder B, prefix `pl_`): per-org rule switches, UEBA baselines, correlator warm-start
state and the dead-letter register. The incident tables remain the source of truth for incidents."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nightjar.db.base import Base, JSONType, OrgScopedMixin, UTCDateTime, UUIDPkMixin, UUIDType, utcnow


class RuleOverride(UUIDPkMixin, OrgScopedMixin, Base):
    """An analyst switched a detection rule on or off for one organisation (`PATCH /rules/{id}`)."""

    __tablename__ = "pl_rule_overrides"
    __table_args__ = (UniqueConstraint("org_id", "rule_id", name="uq_pl_rule_overrides_org_rule"),)

    rule_id: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class UebaBaseline(UUIDPkMixin, OrgScopedMixin, Base):
    """Robust per-entity baseline for one behaviour feature (median, MAD with floor, p95, sample count)."""

    __tablename__ = "pl_ueba_baselines"
    __table_args__ = (
        UniqueConstraint("org_id", "entity_type", "entity_id", "feature", name="uq_pl_ueba_baselines_entity_feature"),
    )

    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[str] = mapped_column(String(255), index=True)
    feature: Mapped[str] = mapped_column(String(64))
    median: Mapped[float] = mapped_column(Float)
    mad: Mapped[float] = mapped_column(Float)
    p95: Mapped[float] = mapped_column(Float)
    n: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class CorrelationState(UUIDPkMixin, OrgScopedMixin, Base):
    """Correlator warm start: the entity cluster behind each open incident."""

    __tablename__ = "pl_correlation_state"
    __table_args__ = (UniqueConstraint("org_id", "correlation_key", name="uq_pl_correlation_state_org_key"),)

    correlation_key: Mapped[str] = mapped_column(String(64))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    entities: Mapped[dict] = mapped_column(JSONType, default=dict)
    state: Mapped[dict] = mapped_column(JSONType, default=dict)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime())


class DeadLetter(Base):
    """Register of dead-lettered messages (mirrors `NJ_DLQ`, survives a memory-bus process) for the pipeline
    view and the retry endpoint. `payload` is the original message body (untrusted)."""

    __tablename__ = "pl_dead_letters"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True, index=True)
    stream: Mapped[str] = mapped_column(String(32))
    consumer: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255))
    original_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deliveries: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str] = mapped_column(Text)
    headers: Mapped[dict] = mapped_column(JSONType, default=dict)
    payload: Mapped[str] = mapped_column(Text)
    retried_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class RuleSuppression(UUIDPkMixin, OrgScopedMixin, Base):
    """A per-org exception to a detection rule ("Lower the noise", "Mark as expected"): detections from `rule_id`
    (or any rule starting with it when it ends in `*`, e.g. `ueba:user:*`) whose entities include `value` in
    `field` are still recorded but scored as suppressed, so they never open an incident on their own. Expiring
    and revocable; adding and removing one is audited."""

    __tablename__ = "pl_rule_suppressions"

    rule_id: Mapped[str] = mapped_column(String(128), index=True)
    field: Mapped[str] = mapped_column(String(16))  # user | host | ip
    value: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType, nullable=True)
    created_by_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
