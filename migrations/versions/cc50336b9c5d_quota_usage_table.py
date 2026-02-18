"""quota_usage_table

Revision ID: cc50336b9c5d
Revises: fb8bdfb44796
Create Date: 2026-02-17 02:04:36.224814

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cc50336b9c5d"
down_revision: Union[str, Sequence[str], None] = "fb8bdfb44796"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quota_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("bucket_date", sa.String(length=10), nullable=False),
        sa.Column("used_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("user_id", "feature", "bucket_date", name="uq_quota_usage_user_feature_day"),
    )
    op.create_index("ix_quota_usage_user_id", "quota_usage", ["user_id"], unique=False)
    op.create_index("ix_quota_usage_feature", "quota_usage", ["feature"], unique=False)
    op.create_index("ix_quota_usage_bucket_date", "quota_usage", ["bucket_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_quota_usage_bucket_date", table_name="quota_usage")
    op.drop_index("ix_quota_usage_feature", table_name="quota_usage")
    op.drop_index("ix_quota_usage_user_id", table_name="quota_usage")
    op.drop_table("quota_usage")
