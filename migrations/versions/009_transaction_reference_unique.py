"""Make wallet ledger references idempotent.

Revision ID: 009_transaction_reference_unique
Revises: 008_hash_user_api_keys
"""

from alembic import op
import sqlalchemy as sa


revision = "009_transaction_reference_unique"
down_revision = "008_hash_user_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Ensure existing records have unique references before creating unique index
    bind.execute(
        sa.text("""
        UPDATE transactions
        SET reference = reference || '-' || id
        WHERE reference IN (
            SELECT reference FROM transactions GROUP BY reference HAVING COUNT(*) > 1
        )
        """)
    )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("transactions")}
    if "uq_transactions_reference" not in indexes:
        op.create_index("uq_transactions_reference", "transactions", ["reference"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("transactions")}
    if "uq_transactions_reference" in indexes:
        op.drop_index("uq_transactions_reference", table_name="transactions")
