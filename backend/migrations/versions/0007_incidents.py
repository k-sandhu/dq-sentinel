"""incident lifecycle and notification channels

Revision ID: 0005_incidents
Revises: 0004_dataset_monitor_packs
Create Date: 2026-06-16 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_incidents"
down_revision: str | None = "0006_dataset_monitor_packs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=False),
        sa.Column("current_run_id", sa.Integer(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("failure_status", sa.String(length=10), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        sa.Column("next_escalation_at", sa.DateTime(), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column("external_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["check_id"], ["checks.id"]),
        sa.ForeignKeyConstraint(["current_run_id"], ["check_runs.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_incident_dedupe_key"),
    )
    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_incidents_check_id"), ["check_id"], unique=False)
        batch_op.create_index("ix_incidents_check_status", ["check_id", "status"], unique=False)
        batch_op.create_index(batch_op.f("ix_incidents_dataset_id"), ["dataset_id"], unique=False)
        batch_op.create_index("ix_incidents_dataset_status", ["dataset_id", "status"], unique=False)
        batch_op.create_index("ix_incidents_escalation", ["status", "next_escalation_at"], unique=False)
        batch_op.create_index("ix_incidents_status", ["status"], unique=False)

    op.create_table(
        "incident_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("incident_events", schema=None) as batch_op:
        batch_op.create_index("ix_incident_events_incident", ["incident_id", "id"], unique=False)

    with op.batch_alter_table("notification_rules", schema=None) as batch_op:
        batch_op.alter_column(
            "channel",
            existing_type=sa.String(length=10),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column(
                "dedupe_window_minutes",
                sa.Integer(),
                server_default="60",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("escalation_delay_minutes", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "max_escalation_level",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_rules", schema=None) as batch_op:
        batch_op.drop_column("max_escalation_level")
        batch_op.drop_column("escalation_delay_minutes")
        batch_op.drop_column("dedupe_window_minutes")
        batch_op.alter_column(
            "channel",
            existing_type=sa.String(length=20),
            type_=sa.String(length=10),
            existing_nullable=False,
        )

    with op.batch_alter_table("incident_events", schema=None) as batch_op:
        batch_op.drop_index("ix_incident_events_incident")
    op.drop_table("incident_events")

    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.drop_index("ix_incidents_status")
        batch_op.drop_index("ix_incidents_escalation")
        batch_op.drop_index("ix_incidents_dataset_status")
        batch_op.drop_index(batch_op.f("ix_incidents_dataset_id"))
        batch_op.drop_index("ix_incidents_check_status")
        batch_op.drop_index(batch_op.f("ix_incidents_check_id"))
    op.drop_table("incidents")
