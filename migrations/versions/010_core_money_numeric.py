"""Use fixed-point numeric values for wallet and payment money.

Revision ID: 010_core_money_numeric
Revises: 009_transaction_reference_unique
"""

from alembic import op
import sqlalchemy as sa

revision = "010_core_money_numeric"
down_revision = "009_transaction_reference_unique"
branch_labels = None
depends_on = None


def _alter(table: str, column: str, typ: sa.types.TypeEngine) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.Float(), type_=typ)
    else:
        op.alter_column(table, column, existing_type=sa.Float(), type_=typ)


def upgrade() -> None:
    money = sa.Numeric(18, 2)
    for table, columns in {
        "users": ["balance"],
        "orders": ["price"],
        "transactions": ["amount", "balance_before", "balance_after"],
        "payments": ["amount"],
        "bank_transactions": ["amount"],
    }.items():
        for column in columns:
            _alter(table, column, money)


def downgrade() -> None:
    for table, columns in {
        "users": ["balance"],
        "orders": ["price"],
        "transactions": ["amount", "balance_before", "balance_after"],
        "payments": ["amount"],
        "bank_transactions": ["amount"],
    }.items():
        for column in columns:
            _alter(table, column, sa.Float())
