"""Add durable bank transaction polling records.

Revision ID: 003_bank_transactions
Revises: 002_payment_idempotency
"""

from alembic import op
import sqlalchemy as sa


revision = "003_bank_transactions"
down_revision = "002_payment_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "bank_transactions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "bank_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("bank_transaction_id", sa.String(length=150), nullable=False, unique=True),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bank_transactions_status", "bank_transactions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_bank_transactions_status", table_name="bank_transactions")
    op.drop_table("bank_transactions")
