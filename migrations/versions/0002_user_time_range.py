"""Store each organizer's preferred event time range.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("preferred_start_hour", sa.Integer(), nullable=False, server_default="9"),
    )
    op.add_column(
        "users",
        sa.Column("preferred_end_hour", sa.Integer(), nullable=False, server_default="24"),
    )


def downgrade() -> None:
    op.drop_column("users", "preferred_end_hour")
    op.drop_column("users", "preferred_start_hour")
