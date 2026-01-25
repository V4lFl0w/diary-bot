"""create analytics_events table

Revision ID: b13410092c15
Revises: 863ae07c57f3
Create Date: 2026-01-25 10:04:00.653750

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b13410092c15"
down_revision = "7586fc120e13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),

        # ВАЖНО: делаем ts TEXT, чтобы твоя следующая миграция 5f6f... смогла
        # спокойно сконвертить ts -> timestamptz (как ты и задумал)
        sa.Column("ts", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),

        # sqlite будет TEXT, postgres будет JSONB (совместимо с твоим JSONText().with_variant(JSONB, "postgresql"))
        sa.Column("props", sa.Text().with_variant(JSONB, "postgresql"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("analytics_events")
