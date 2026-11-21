"""Track automatic draft pins separately from explicit user pins.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "draft_auto_pinned" not in {
        column["name"] for column in inspector.get_columns("stream_offers")
    }:
        # Existing pins retain their meaning; their origin cannot be inferred
        # safely from their share, so migration never removes them.
        op.add_column(
            "stream_offers",
            sa.Column("draft_auto_pinned", sa.Boolean(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("stream_offers", "draft_auto_pinned")
