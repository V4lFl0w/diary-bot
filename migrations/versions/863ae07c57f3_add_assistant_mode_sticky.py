"""Add assistant mode sticky

Revision ID: 863ae07c57f3
Revises: f2dca74bfa03
Create Date: 2026-01-25 09:30:31.578759
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "863ae07c57f3"
down_revision: Union[str, Sequence[str], None] = "f2dca74bfa03"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade() -> None:
    # эта миграция больше не ломает повторные прогоны
    if not _table_exists("payments"):
        return

    if not _has_column("payments", "refunded_at"):
        op.add_column("payments", sa.Column("refunded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if not _table_exists("payments"):
        return

    if _has_column("payments", "refunded_at"):
        op.drop_column("payments", "refunded_at")
