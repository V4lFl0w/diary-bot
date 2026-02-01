"""Add assistant mode sticky to users

Revision ID: (auto)
Revises: (auto)
Create Date: (auto)

"""

from alembic import op
import sqlalchemy as sa

revision = "fd4d950d86ea"
down_revision = "93eda1cdaab6"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_column("users", "assistant_mode"):
        op.add_column("users", sa.Column("assistant_mode", sa.String(length=32), nullable=True))
    if not _has_column("users", "assistant_mode_until"):
        op.add_column("users", sa.Column("assistant_mode_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _has_column("users", "assistant_mode_until"):
        op.drop_column("users", "assistant_mode_until")
    if _has_column("users", "assistant_mode"):
        op.drop_column("users", "assistant_mode")
