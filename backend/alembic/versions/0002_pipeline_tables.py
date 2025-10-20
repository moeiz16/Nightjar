"""pipeline tables (pl_*)

Revision ID: 0002p
Revises: 0001

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import nightjar.db.base

revision: str = "0002p"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pl_rule_overrides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", nightjar.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pl_rule_overrides")),
        sa.UniqueConstraint("org_id", "rule_id", name="uq_pl_rule_overrides_org_rule"),
    )
    op.create_index(op.f("ix_pl_rule_overrides_org_id"), "pl_rule_overrides", ["org_id"])
    op.create_table(
        "pl_ueba_baselines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("median", sa.Float(), nullable=False),
        sa.Column("mad", sa.Float(), nullable=False),
        sa.Column("p95", sa.Float(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("updated_at", nightjar.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pl_ueba_baselines")),
        sa.UniqueConstraint(
            "org_id", "entity_type", "entity_id", "feature", name="uq_pl_ueba_baselines_entity_feature"
        ),
    )
    op.create_index(op.f("ix_pl_ueba_baselines_org_id"), "pl_ueba_baselines", ["org_id"])
    op.create_index(op.f("ix_pl_ueba_baselines_entity_id"), "pl_ueba_baselines", ["entity_id"])
    op.create_table(
        "pl_correlation_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_key", sa.String(length=64), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=True),
        sa.Column("entities", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("last_seen", nightjar.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pl_correlation_state")),
        sa.UniqueConstraint("org_id", "correlation_key", name="uq_pl_correlation_state_org_key"),
    )
    op.create_index(op.f("ix_pl_correlation_state_org_id"), "pl_correlation_state", ["org_id"])
    op.create_table(
        "pl_dead_letters",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("stream", sa.String(length=32), nullable=False),
        sa.Column("consumer", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("original_seq", sa.BigInteger(), nullable=True),
        sa.Column("deliveries", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("retried_at", nightjar.db.base.UTCDateTime(timezone=True), nullable=True),
        sa.Column("created_at", nightjar.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pl_dead_letters")),
    )
    op.create_index(op.f("ix_pl_dead_letters_org_id"), "pl_dead_letters", ["org_id"])


def downgrade() -> None:
    op.drop_table("pl_dead_letters")
    op.drop_table("pl_correlation_state")
    op.drop_table("pl_ueba_baselines")
    op.drop_table("pl_rule_overrides")
