"""Add scoped machine credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0014_api_tokens"
down_revision = "0013_schema_baseline_scopes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("connection_scope", sa.JSON(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)


def downgrade():
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_table("api_tokens")
