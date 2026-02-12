"""kb_items v1

Revision ID: 20260212_220020
Revises: 
Create Date: 2026-02-12 22:00:20

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260212_220020"
down_revision = "fd4d950d86ea"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kb_items_user_id", "kb_items", ["user_id"])
    op.create_index("ix_kb_items_user_created", "kb_items", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_kb_items_user_created", table_name="kb_items")
    op.drop_index("ix_kb_items_user_id", table_name="kb_items")
    op.drop_table("kb_items")
