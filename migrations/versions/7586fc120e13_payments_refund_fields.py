"""payments refund fields

Revision ID: 7586fc120e13
Revises: d8d1968672cf
Create Date: 2026-01-10 16:49:41.233518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7586fc120e13'
down_revision: Union[str, Sequence[str], None] = 'd8d1968672cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    with op.batch_alter_table("payments") as b:
        b.add_column(sa.Column("refunded_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("refund_status", sa.String(length=16), nullable=True))  # requested/approved/denied
        b.add_column(sa.Column("refund_reason", sa.String(length=500), nullable=True))
        b.add_column(sa.Column("refund_admin_note", sa.String(length=500), nullable=True))
        b.add_column(sa.Column("refund_requested_at", sa.DateTime(), nullable=True))

def downgrade():
    with op.batch_alter_table("payments") as b:
        b.drop_column("refund_requested_at")
        b.drop_column("refund_admin_note")
        b.drop_column("refund_reason")
        b.drop_column("refund_status")
        b.drop_column("refunded_at")
