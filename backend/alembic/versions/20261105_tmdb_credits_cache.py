"""tmdb credits cache

Revision ID: 20261105_tmdb_credits_cache
Revises: 20261028_drop_openlist_settings
Create Date: 2026-11-05 20:50:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261105_tmdb_credits_cache"
down_revision = "20261028_drop_openlist_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "tmdb_credits_cache" not in tables:
        op.create_table(
            "tmdb_credits_cache",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("media_type", sa.String(length=8), nullable=False),
            sa.Column("tmdb_id", sa.Integer(), nullable=False),
            sa.Column("language", sa.String(length=16), server_default="zh-CN", nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("fail_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("media_type", "tmdb_id", "language", name="uq_tmdb_credits_cache_key"),
        )
        op.create_index("ix_tmdb_credits_cache_expires_at", "tmdb_credits_cache", ["expires_at"], unique=False)
        op.create_index("ix_tmdb_credits_cache_tmdb", "tmdb_credits_cache", ["media_type", "tmdb_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "tmdb_credits_cache" in tables:
        op.drop_index("ix_tmdb_credits_cache_tmdb", table_name="tmdb_credits_cache")
        op.drop_index("ix_tmdb_credits_cache_expires_at", table_name="tmdb_credits_cache")
        op.drop_table("tmdb_credits_cache")
