"""dataset monitor packs

Revision ID: 0004_dataset_monitor_packs
Revises: 0003_sla_tracking
Create Date: 2026-06-16 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_dataset_monitor_packs"
down_revision: str | None = "0003_sla_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_monitor_packs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_result", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", name="uq_dataset_monitor_pack_dataset"),
    )
    with op.batch_alter_table("dataset_monitor_packs", schema=None) as batch_op:
        batch_op.create_index("ix_dataset_monitor_packs_dataset", ["dataset_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("dataset_monitor_packs", schema=None) as batch_op:
        batch_op.drop_index("ix_dataset_monitor_packs_dataset")
    op.drop_table("dataset_monitor_packs")
