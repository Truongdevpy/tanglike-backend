"""Add email verification state and one-time tokens.

Revision ID: 007_email_verification
Revises: 006_provider_retry_limits
"""

from alembic import op
import sqlalchemy as sa


revision = "007_email_verification"
down_revision = "006_provider_retry_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "email_verified" not in {column["name"] for column in inspector.get_columns("users")}:
        op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "email_verification_tokens" in inspector.get_table_names():
        return
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_email_verification_tokens_user_id", "email_verification_tokens", ["user_id"])
    op.create_index("ix_email_verification_tokens_expires_at", "email_verification_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_email_verification_tokens_expires_at", table_name="email_verification_tokens")
    op.drop_index("ix_email_verification_tokens_user_id", table_name="email_verification_tokens")
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified")
