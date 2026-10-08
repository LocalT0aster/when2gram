"""Store availability masks beyond SQLite's integer range.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("availability_days") as batch_op:
        batch_op.alter_column(
            "slot_mask",
            existing_type=sa.BigInteger(),
            type_=sa.String(length=32),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("availability_days") as batch_op:
        batch_op.alter_column(
            "slot_mask",
            existing_type=sa.String(length=32),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
