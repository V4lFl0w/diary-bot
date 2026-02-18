"""quota_and_kv_cache

Revision ID: fb8bdfb44796
Revises: fd4d950d86ea
Create Date: auto
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fb8bdfb44796"
down_revision = "20260212_220020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # KV cache for expensive external calls (SerpAPI/Lens/web text, etc.)
    op.create_table(
        "kv_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("namespace", sa.String(length=64), nullable=False, server_default=sa.text("'default'")),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("namespace", "key", name="uq_kv_cache_namespace_key"),
    )

    op.create_index("ix_kv_cache_expires_at", "kv_cache", ["expires_at"], unique=False)
    op.create_index("ix_kv_cache_namespace_key", "kv_cache", ["namespace", "key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_kv_cache_namespace_key", table_name="kv_cache")
    op.drop_index("ix_kv_cache_expires_at", table_name="kv_cache")
    op.drop_table("kv_cache")
