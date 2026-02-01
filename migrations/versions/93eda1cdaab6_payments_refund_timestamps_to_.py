"""Payments refund timestamps to timestamptz

Revision ID: 93eda1cdaab6
Revises: 863ae07c57f3
Create Date: 2026-01-25

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "93eda1cdaab6"
down_revision = "863ae07c57f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # refunded_at: timestamp -> timestamptz (assume stored UTC)
    op.alter_column(
        "payments",
        "refunded_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        postgresql_using="refunded_at AT TIME ZONE 'UTC'",
        existing_nullable=True,
    )
    op.alter_column(
        "payments",
        "refund_requested_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        postgresql_using="refund_requested_at AT TIME ZONE 'UTC'",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "payments",
        "refund_requested_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        postgresql_using="refund_requested_at::timestamp",
        existing_nullable=True,
    )
    op.alter_column(
        "payments",
        "refunded_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        postgresql_using="refunded_at::timestamp",
        existing_nullable=True,
    )
