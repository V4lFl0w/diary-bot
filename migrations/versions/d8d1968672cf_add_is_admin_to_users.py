"""add is_admin to users

Revision ID: d8d1968672cf
Revises: 01286a052106
Create Date: 2025-12-10 06:19:14.326838
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "d8d1968672cf"
down_revision: Union[str, Sequence[str], None] = "01286a052106"
branch_labels = None
depends_on = None


def _create_users_table() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind else ""

    id_col = (
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True)
        if dialect == "sqlite"
        else sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True)
    )

    op.create_table(
        "users",
        id_col,
        sa.Column("tg_id", sa.BigInteger(), nullable=False),

        sa.Column("locale", sa.String(8), nullable=False, server_default=sa.text("'ru'")),
        sa.Column("lang", sa.String(8), nullable=True),
        sa.Column("tz", sa.String(64), nullable=False, server_default=sa.text("'Europe/Kyiv'")),

        sa.Column("policy_accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("consent_accepted_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),

        sa.Column("is_premium", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("premium_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("premium_trial_given", sa.Boolean(), nullable=False, server_default=sa.text("false")),

        sa.Column("morning_auto", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("morning_time", sa.Time(), nullable=False, server_default=sa.text("'09:30:00'")),

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_index("ix_users_tg_id", "users", ["tg_id"], unique=True)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if "users" not in insp.get_table_names():
        _create_users_table()
        return

    cols = {c["name"] for c in insp.get_columns("users")}
    if "is_admin" in cols:
        return

    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if "users" in insp.get_table_names():
        with op.batch_alter_table("users") as batch:
            try:
                batch.drop_column("is_admin")
            except Exception:
                pass
