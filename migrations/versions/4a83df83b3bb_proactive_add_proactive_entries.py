"""proactive: add proactive_entries

Revision ID: 4a83df83b3bb
Revises: 49f6661d80cb
Create Date: 
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "4a83df83b3bb"
down_revision = "49f6661d80cb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proactive_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_proactive_entries_user_id", "proactive_entries", ["user_id"])
    op.create_index("ix_proactive_entries_local_date", "proactive_entries", ["local_date"])
    op.create_unique_constraint(
        "ux_proactive_entry_day",
        "proactive_entries",
        ["user_id", "kind", "local_date"],
    )


def downgrade() -> None:
    op.drop_constraint("ux_proactive_entry_day", "proactive_entries", type_="unique")
    op.drop_index("ix_proactive_entries_local_date", table_name="proactive_entries")
    op.drop_index("ix_proactive_entries_user_id", table_name="proactive_entries")
    op.drop_table("proactive_entries")
