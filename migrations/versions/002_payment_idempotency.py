"""Add database-enforced idempotency keys for money movements.

Revision ID: 002_payment_idempotency
Revises: 001_initial_schema
"""

from alembic import op
import sqlalchemy as sa


revision = "002_payment_idempotency"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "payments" not in inspector.get_table_names():
        return
    constraints = inspector.get_unique_constraints("payments")
    if any("gateway_reference" in (item.get("column_names") or []) for item in constraints):
        return
    with op.batch_alter_table("payments") as batch:
        batch.create_unique_constraint("uq_payments_gateway_reference", ["gateway_reference"])


def downgrade() -> None:
    op.drop_constraint("uq_payments_gateway_reference", "payments", type_="unique")
