"""scorecard snapshots

Revision ID: 0004_scorecard_snapshots
Revises: 0003_sla_tracking
Create Date: 2026-06-16 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_scorecard_snapshots"
down_revision: str | None = "0003_sla_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scorecard_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("grain", sa.String(length=20), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("slo_target", sa.Float(), nullable=True),
        sa.Column("slo_status", sa.String(length=20), nullable=False),
        sa.Column("dataset_count", sa.Integer(), nullable=False),
        sa.Column("active_check_count", sa.Integer(), nullable=False),
        sa.Column("open_exception_count", sa.Integer(), nullable=False),
        sa.Column("breached_dataset_count", sa.Integer(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grain",
            "key",
            "snapshot_date",
            name="uq_scorecard_snapshot_grain_key_date",
        ),
    )
    with op.batch_alter_table("scorecard_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            "ix_scorecard_history_lookup",
            ["grain", "key", "snapshot_date"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("scorecard_snapshots", schema=None) as batch_op:
        batch_op.drop_index("ix_scorecard_history_lookup")
    op.drop_table("scorecard_snapshots")
