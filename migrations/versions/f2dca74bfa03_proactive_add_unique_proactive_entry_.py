"""proactive: add unique proactive entry per day

Revision ID: f2dca74bfa03
Revises: 4a83df83b3bb
Create Date: 2026-01-22 10:xx:xx.xxxxxx
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f2dca74bfa03"
down_revision = "4a83df83b3bb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    exists = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = :name
            LIMIT 1
            """
        ),
        {"name": "ux_proactive_entry_day"},
    ).scalar()

    if exists:
        return

    op.create_unique_constraint(
        "ux_proactive_entry_day",
        "proactive_entries",
        ["user_id", "kind", "local_date"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    exists = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = :name
            LIMIT 1
            """
        ),
        {"name": "ux_proactive_entry_day"},
    ).scalar()

    if not exists:
        return

    op.drop_constraint("ux_proactive_entry_day", "proactive_entries", type_="unique")
