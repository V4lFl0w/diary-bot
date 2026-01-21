"""create llm_usage table

Revision ID: 4eb10165de91
Revises: b21363fb1d28
Create Date: 2026-01-21 03:55:37.435450

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4eb10165de91'
down_revision: Union[str, Sequence[str], None] = 'b21363fb1d28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),

        sa.Column("feature", sa.String(length=50), nullable=False, server_default="assistant"),
        sa.Column("model", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("plan", sa.String(length=20), nullable=False, server_default="basic"),

        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),

        sa.Column("response_id", sa.String(length=120), nullable=True),
        sa.Column("cost_usd_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )

    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"], unique=False)
    op.create_index("ix_llm_usage_user_id", "llm_usage", ["user_id"], unique=False)
    op.create_index("ix_llm_usage_feature", "llm_usage", ["feature"], unique=False)
    op.create_index("ix_llm_usage_model", "llm_usage", ["model"], unique=False)
    op.create_index("ix_llm_usage_plan", "llm_usage", ["plan"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_llm_usage_plan", table_name="llm_usage")
    op.drop_index("ix_llm_usage_model", table_name="llm_usage")
    op.drop_index("ix_llm_usage_feature", table_name="llm_usage")
    op.drop_index("ix_llm_usage_user_id", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
