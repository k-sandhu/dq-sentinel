"""scope schema baselines by consumer

Revision ID: 0013_schema_baseline_scopes
Revises: 0012_rca_report_json
Create Date: 2026-09-30 22:42:34.036667

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_schema_baseline_scopes"
down_revision: str | None = "0012_rca_report_json"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The server default backfills existing snapshots and supports inserts during
    # rolling upgrades. Active contracts recover their scoped pins on first run.
    with op.batch_alter_table("schema_snapshots", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("baseline_scope", sa.String(length=80), server_default="manual", nullable=False)
        )


def downgrade() -> None:
    # Old code understands one manual pin per dataset. Keep that pin and retain
    # contract snapshots as unpinned history instead of reviving a clobber.
    op.execute(sa.text("UPDATE schema_snapshots SET is_baseline = false WHERE baseline_scope != 'manual'"))
    with op.batch_alter_table("schema_snapshots", schema=None) as batch_op:
        batch_op.drop_column("baseline_scope")
