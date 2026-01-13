"""add timestamps to user_tracks

Revision ID: 3e6a508adf17
Revises: 8208f9e10222
Create Date: 2025-12-09 04:23:45.994096
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3e6a508adf17"
down_revision: Union[str, Sequence[str], None] = "8208f9e10222"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return bool(insp.has_table(name))


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade() -> None:
    # Если таблицы нет — ничего не делаем
    if not _has_table("user_tracks"):
        return

    # 1) добавляем колонки (если их нет)
    if not _has_column("user_tracks", "created_at"):
        op.add_column(
            "user_tracks",
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            "UPDATE user_tracks SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )

    if not _has_column("user_tracks", "updated_at"):
        op.add_column(
            "user_tracks",
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            "UPDATE user_tracks SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"
        )

    # 2) приводим к NOT NULL + дефолтам (SQLite-friendly)
    with op.batch_alter_table("user_tracks") as batch:
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        )
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        )


def downgrade() -> None:
    if not _has_table("user_tracks"):
        return

    with op.batch_alter_table("user_tracks") as batch:
        if _has_column("user_tracks", "updated_at"):
            batch.drop_column("updated_at")
        if _has_column("user_tracks", "created_at"):
            batch.drop_column("created_at")