"""Add durable worker run monitoring.

Revision ID: 005_job_runs
Revises: 004_password_reset_tokens
"""

from alembic import op
import sqlalchemy as sa


revision = "005_job_runs"
down_revision = "004_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "job_runs" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
    )
    op.create_index("ix_job_runs_job_name", "job_runs", ["job_name"])
    op.create_index("ix_job_runs_status", "job_runs", ["status"])
    op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_job_runs_started_at", table_name="job_runs")
    op.drop_index("ix_job_runs_status", table_name="job_runs")
    op.drop_index("ix_job_runs_job_name", table_name="job_runs")
    op.drop_table("job_runs")
