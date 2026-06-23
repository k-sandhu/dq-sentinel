"""per-connection grants (#26 PR2 / #159)

Revision ID: 0010_connection_grants
Revises: 0009_exception_version
Create Date: 2026-06-23 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_connection_grants"
down_revision: str | None = "0009_exception_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connection_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "connection_id", name="uq_grant_user_conn"),
    )
    op.create_index("ix_connection_grants_user_id", "connection_grants", ["user_id"])
    op.create_index("ix_connection_grants_connection_id", "connection_grants", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_connection_grants_connection_id", table_name="connection_grants")
    op.drop_index("ix_connection_grants_user_id", table_name="connection_grants")
    op.drop_table("connection_grants")
