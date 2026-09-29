"""Add one-time password reset credentials and session versioning.

Revision ID: 004_password_reset_tokens
Revises: 003_bank_transactions
"""

from alembic import op
import sqlalchemy as sa


revision = "004_password_reset_tokens"
down_revision = "003_bank_transactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "token_version" not in {column["name"] for column in inspector.get_columns("users")}:
        op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    if "password_reset_tokens" in inspector.get_table_names():
        return
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_expires_at", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_column("users", "token_version")
