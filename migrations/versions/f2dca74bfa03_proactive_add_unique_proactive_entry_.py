"""proactive: add unique proactive entry per day

Revision ID: f2dca74bfa03
Revises: 4a83df83b3bb
Create Date: 2026-01-24 06:46:39.946649

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2dca74bfa03'
down_revision: Union[str, Sequence[str], None] = '4a83df83b3bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "ux_proactive_entry_day",
        "proactive_entries",
        ["user_id", "kind", "local_date"],
    )

def downgrade() -> None:
    op.drop_constraint("ux_proactive_entry_day", "proactive_entries", type_="unique")
