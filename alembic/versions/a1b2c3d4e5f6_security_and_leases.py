"""Security schema and operation leases

Revision ID: a1b2c3d4e5f6
Revises: 6a8e6a51f813
Create Date: 2026-11-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "6a8e6a51f813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Operations table lease columns
    if "operations" in existing_tables:
        existing_cols = [c["name"] for c in inspector.get_columns("operations")]
        if "worker_id" not in existing_cols:
            op.add_column("operations", sa.Column("worker_id", sa.String(length=64), nullable=True))
        if "lease_expires_at" not in existing_cols:
            op.add_column("operations", sa.Column("lease_expires_at", sa.Float(), nullable=True))

    # Security tables for security store
    if "rate_limits" not in existing_tables:
        op.create_table(
            "rate_limits",
            sa.Column("client_ip", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tier", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("blocked_until", sa.Float(), nullable=False, server_default="0"),
            sa.Column("last_attempt_at", sa.Float(), nullable=False, server_default="0"),
        )

    if "security_sessions" not in existing_tables:
        op.create_table(
            "security_sessions",
            sa.Column("token_hash", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("last_seen_at", sa.Float(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("client_ip", sa.String(length=64), nullable=False),
            sa.Column("key_fingerprint", sa.String(length=64), nullable=False),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "security_sessions" in existing_tables:
        op.drop_table("security_sessions")
    if "rate_limits" in existing_tables:
        op.drop_table("rate_limits")
