"""Durable scheduler claims; checks are few, so no lease index is needed.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014_api_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("checks", sa.Column("claimed_until", sa.DateTime(), nullable=True))
    op.add_column("checks", sa.Column("claim_token", sa.String(36), nullable=True))


def downgrade():
    op.drop_column("checks", "claim_token")
    op.drop_column("checks", "claimed_until")
