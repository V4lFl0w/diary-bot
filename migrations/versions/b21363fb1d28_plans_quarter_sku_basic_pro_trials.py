"""plans: quarter + sku + basic/pro trials

Revision ID: b21363fb1d28
Revises: c23cdec84ace
Create Date: 2026-01-21 00:39:21.044588

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b21363fb1d28'
down_revision: Union[str, Sequence[str], None] = 'c23cdec84ace'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    # add enum value quarter (Postgres safe)
    op.execute("""
DO $$
BEGIN
    -- create enum if missing (for fresh DB)
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_plan') THEN
        CREATE TYPE payment_plan AS ENUM ('month', 'year');
    END IF;

    -- add quarter if missing
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'payment_plan'
          AND e.enumlabel = 'quarter'
    ) THEN
        ALTER TYPE payment_plan ADD VALUE 'quarter';
    END IF;
END $$;
""")# payments.sku

    # users: tier + 2 trials
    op.add_column('users', sa.Column('premium_plan', sa.String(length=16), server_default='basic', nullable=False))
    op.add_column('users', sa.Column('basic_trial_given', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('users', sa.Column('pro_trial_given', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    pass
