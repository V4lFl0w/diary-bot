"""payments table

Revision ID: b0b54fc02dab
Revises:
Create Date: 2025-11-14 00:16:30.016965

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "b0b54fc02dab"
down_revision: Union[str, Sequence[str], None] = None
branch_labels = None
depends_on = None


def _ensure_users_table() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if "users" in insp.get_table_names():
        return

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
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )

    op.create_index("ix_users_tg_id", "users", ["tg_id"], unique=True)


def upgrade() -> None:
    _ensure_users_table()

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id")),
        sa.Column("provider", sa.Enum("mono", "crypto", "stars", "test", name="payment_provider"), nullable=False),
        sa.Column(
            "plan",
            sa.Enum("trial", "month", "year", "quarter", "lifetime", "topup", name="payment_plan"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("cancelled", "pending", "paid", "refunded", "failed", name="payment_status"),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("external_id", sa.String(length=128)),
        sa.Column("payload", sa.Text()),
        sa.Column("sku", sa.String(length=64)),
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_index("ix_payments_provider_status", "payments", ["provider", "status"], unique=False)
    op.create_index("ix_payments_user_id", "payments", ["user_id"], unique=False)
    op.create_index("ix_payments_sku", "payments", ["sku"], unique=False)
    op.create_index("uq_payments_external_id", "payments", ["external_id"], unique=True)
    op.create_index("ix_payments_external_id", "payments", ["external_id"], unique=False)


def downgrade() -> None:
    op.drop_table("payments")
    # users не трогаем в downgrade первого ревижена, иначе можно снести данные
