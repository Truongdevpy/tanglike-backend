"""Hash stored user API keys.

Revision ID: 008_hash_user_api_keys
Revises: 007_email_verification
"""

import hashlib
from alembic import op
import sqlalchemy as sa

revision = "008_hash_user_api_keys"
down_revision = "007_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "api_key_hash" not in columns:
        op.add_column("users", sa.Column("api_key_hash", sa.String(length=64), nullable=True))
    if "api_key_prefix" not in columns:
        op.add_column("users", sa.Column("api_key_prefix", sa.String(length=20), nullable=True))
    if "ix_users_api_key_hash" not in {item["name"] for item in inspector.get_indexes("users")}:
        op.create_index("ix_users_api_key_hash", "users", ["api_key_hash"], unique=True)
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, api_key FROM users WHERE api_key IS NOT NULL")).mappings()
    for row in rows:
        raw_key = row["api_key"]
        bind.execute(sa.text(
            "UPDATE users SET api_key_hash=:key_hash, api_key_prefix=:prefix, api_key=NULL WHERE id=:id"
        ), {"id": row["id"], "key_hash": hashlib.sha256(raw_key.encode("utf-8")).hexdigest(), "prefix": raw_key[:12]})


def downgrade() -> None:
    op.drop_index("ix_users_api_key_hash", table_name="users")
    op.drop_column("users", "api_key_prefix")
    op.drop_column("users", "api_key_hash")
