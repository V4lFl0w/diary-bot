"""user_tracks: enforce not null on tg_id user_id file_id

Revision ID: 7f5d9ca507a1
Revises: 4eb10165de91
Create Date: 2026-01-22 09:10:14.779574

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7f5d9ca507a1"
down_revision = "4eb10165de91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # гарантируем NOT NULL — схема должна совпадать с моделями
    op.alter_column(
        "user_tracks",
        "tg_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.alter_column(
        "user_tracks",
        "user_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.alter_column(
        "user_tracks",
        "file_id",
        existing_type=sa.Text(),
        nullable=False,
    )


def downgrade() -> None:
    # откат — если вдруг понадобится
    op.alter_column(
        "user_tracks",
        "tg_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    op.alter_column(
        "user_tracks",
        "user_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    op.alter_column(
        "user_tracks",
        "file_id",
        existing_type=sa.Text(),
        nullable=True,
    )
