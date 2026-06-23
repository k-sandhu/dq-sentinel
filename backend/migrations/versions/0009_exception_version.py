"""exception optimistic-concurrency version (#156)

Revision ID: 0009_exception_version
Revises: 0008_data_contracts
Create Date: 2026-06-23 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_exception_version"
down_revision: str | None = "0008_data_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill existing rows to 1 via server_default; the column is then a
    # monotonically-increasing optimistic-concurrency token for triage.
    op.add_column(
        "exception_records",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    with op.batch_alter_table("exception_records") as batch:
        batch.drop_column("version")
