"""check versioning + rollback (#185)

Revision ID: 0011_check_versions
Revises: 0010_connection_grants
Create Date: 2026-06-24 00:00:00.000000

Adds an append-only ``check_versions`` table snapshotting a check's definition on
every change, and backfills a ``v1`` baseline for every existing check so each one
has restorable history immediately.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_check_versions"
down_revision: str | None = "0010_connection_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "check_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("check_type", sa.String(length=50), nullable=False),
        sa.Column("column_name", sa.String(length=255), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("schedule_kind", sa.String(length=10), nullable=True),
        sa.Column("schedule_expr", sa.String(length=100), nullable=True),
        sa.Column("change_note", sa.String(length=255), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["check_id"], ["checks.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("check_id", "version", name="uq_check_versions_check_version"),
    )
    with op.batch_alter_table("check_versions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_check_versions_check_id"), ["check_id"], unique=False)

    # Backfill a v1 baseline for every existing check so history/restore work at once.
    op.execute(
        """
        INSERT INTO check_versions
            (check_id, version, name, check_type, column_name, params, severity,
             rationale, schedule_kind, schedule_expr, change_note, created_by_id, created_at)
        SELECT
            id, 1, name, check_type, column_name, params, severity,
            COALESCE(rationale, ''), schedule_kind, schedule_expr, 'baseline', created_by_id, created_at
        FROM checks
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("check_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_check_versions_check_id"))
    op.drop_table("check_versions")
