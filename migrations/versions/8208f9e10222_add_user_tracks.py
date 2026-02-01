"""add user tracks

Revision ID: 8208f9e10222
Revises: b0b54fc02dab
Create Date: 2025-12-08 23:59:03.654841

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "8208f9e10222"
down_revision: Union[str, Sequence[str], None] = "b0b54fc02dab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if "user_tracks" in insp.get_table_names():
        return

    op.create_table(
        "user_tracks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("file_id", sa.Text(), nullable=False),
        # если у тебя есть created_at/updated_at и др — добавь тут же
    )

    op.create_index("ix_user_tracks_tg_id", "user_tracks", ["tg_id"], unique=False)
    op.create_index("ix_user_tracks_user_id", "user_tracks", ["user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "user_tracks" in insp.get_table_names():
        op.drop_index("ix_user_tracks_user_id", table_name="user_tracks")
        op.drop_index("ix_user_tracks_tg_id", table_name="user_tracks")
        op.drop_table("user_tracks")
