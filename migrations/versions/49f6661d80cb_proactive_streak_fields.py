"""proactive streak fields

Revision ID: 49f6661d80cb
Revises: 7f5d9ca507a1
Create Date: 2026-01-24 03:54:03.239634

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '49f6661d80cb'
down_revision: Union[str, Sequence[str], None] = '7f5d9ca507a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) add columns with safe defaults
    op.add_column(
        "users",
        sa.Column("proactive_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("proactive_last_done_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2) drop server_default to keep schema clean (optional but recommended)
    op.alter_column("users", "proactive_streak", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "proactive_last_done_at")
    op.drop_column("users", "proactive_streak")
