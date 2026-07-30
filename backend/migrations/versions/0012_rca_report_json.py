"""structured RCA report payload (#287)

Revision ID: 0012_rca_report_json
Revises: 0011_check_versions
Create Date: 2026-07-30 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_rca_report_json"
down_revision: str | None = "0011_check_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable on purpose: NULL means "the agent only produced markdown", which is
    # what every pre-existing session has. The UI falls back to report_md then.
    op.add_column("rca_sessions", sa.Column("report_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("rca_sessions") as batch:
        batch.drop_column("report_json")
