"""initial_schema

Revision ID: 001_initial
Revises: 
Create Date: 2026-09-26 03:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = '001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # The project began with SQLAlchemy metadata creation. Materialize the
    # baseline schema here so `alembic upgrade head` also works on a fresh DB.
    from app.models import all as _models  # noqa: F401
    from app.database.session import Base
    Base.metadata.create_all(bind=op.get_bind())

def downgrade() -> None:
    pass
