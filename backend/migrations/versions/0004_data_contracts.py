"""data contracts

Revision ID: 0004_data_contracts
Revises: 0003_sla_tracking
Create Date: 2026-06-16 07:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_data_contracts"
down_revision: str | None = "0003_sla_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_contracts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("data_contracts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_data_contracts_dataset_id"), ["dataset_id"], unique=False)
        batch_op.create_index("ix_data_contract_dataset_status", ["dataset_id", "status"], unique=False)

    op.create_table(
        "data_contract_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["data_contracts.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("data_contract_versions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_data_contract_versions_contract_id"), ["contract_id"], unique=False)
        batch_op.create_index("ix_data_contract_versions_contract", ["contract_id", "id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("data_contract_versions", schema=None) as batch_op:
        batch_op.drop_index("ix_data_contract_versions_contract")
        batch_op.drop_index(batch_op.f("ix_data_contract_versions_contract_id"))
    op.drop_table("data_contract_versions")

    with op.batch_alter_table("data_contracts", schema=None) as batch_op:
        batch_op.drop_index("ix_data_contract_dataset_status")
        batch_op.drop_index(batch_op.f("ix_data_contracts_dataset_id"))
    op.drop_table("data_contracts")
