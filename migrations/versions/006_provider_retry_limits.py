"""Bound provider submission retries.

Revision ID: 006_provider_retry_limits
Revises: 005_job_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "006_provider_retry_limits"
down_revision = "005_job_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "retry_count" not in columns:
        op.add_column("orders", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    if "next_retry_at" not in columns:
        op.add_column("orders", sa.Column("next_retry_at", sa.DateTime(), nullable=True))
    if "ix_orders_next_retry_at" not in {item["name"] for item in inspector.get_indexes("orders")}:
        op.create_index("ix_orders_next_retry_at", "orders", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_orders_next_retry_at", table_name="orders")
    op.drop_column("orders", "next_retry_at")
    op.drop_column("orders", "retry_count")
