"""scorecard knowledge fields

Revision ID: 0004_scorecard_knowledge_fields
Revises: 0003_sla_tracking
Create Date: 2026-06-16 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_scorecard_knowledge_fields"
down_revision: str | None = "0003_sla_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("table_knowledge", schema=None) as batch_op:
        batch_op.add_column(sa.Column("domain", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("team", sa.String(length=255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("slo_target_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("slo_window_days", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("slo_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("table_knowledge", schema=None) as batch_op:
        batch_op.drop_column("slo_enabled")
        batch_op.drop_column("slo_window_days")
        batch_op.drop_column("slo_target_score")
        batch_op.drop_column("team")
        batch_op.drop_column("domain")
