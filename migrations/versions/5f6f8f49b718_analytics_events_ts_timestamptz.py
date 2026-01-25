"""analytics_events ts timestamptz

Revision ID: 5f6f8f49b718
Revises: d8d1968672cf
Create Date: 
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# --- Alembic identifiers ---
revision = "5f6f8f49b718"
down_revision = "b13410092c15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "analytics_events",
        "ts",
        existing_type=sa.Text(),
        type_=sa.DateTime(timezone=True),
        postgresql_using="ts::timestamptz",
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "analytics_events",
        "ts",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.Text(),
        postgresql_using="ts::text",
        nullable=False,
    )
