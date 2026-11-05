"""move tmdb credits cache into tmdb_media_cache

Revision ID: 20261105_credits_in_media_cache
Revises: 20261105_tmdb_credits_cache
Create Date: 2026-11-05 21:10:00.000000

演员表缓存原本单独建表 tmdb_credits_cache，但 TMDB 缓存管理页只认 tmdb_media_cache，
用户看不到也删不掉。现改为把 credits 字段挂在 tmdb_media_cache 的同一行上：
详情与演员表同一条目，缓存页可见、删除/TTL/清理一次覆盖。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261105_credits_in_media_cache"
down_revision = "20261105_tmdb_credits_cache"
branch_labels = None
depends_on = None


_CREDITS_COLUMNS = (
    sa.Column("credits_json", sa.Text(), nullable=True),
    sa.Column("credits_fetched_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("credits_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("credits_fail_count", sa.Integer(), server_default="0", nullable=False),
    sa.Column("credits_last_error", sa.Text(), nullable=True),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    existing = {c["name"] for c in inspector.get_columns("tmdb_media_cache")}
    for column in _CREDITS_COLUMNS:
        if column.name not in existing:
            op.add_column("tmdb_media_cache", column)

    indexes = {i["name"] for i in inspector.get_indexes("tmdb_media_cache")}
    if "ix_tmdb_media_cache_credits_expires_at" not in indexes:
        op.create_index("ix_tmdb_media_cache_credits_expires_at", "tmdb_media_cache", ["credits_expires_at"], unique=False)

    tables = set(inspector.get_table_names())
    if "tmdb_credits_cache" in tables:
        op.drop_table("tmdb_credits_cache")


def downgrade() -> None:
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

    indexes = {i["name"] for i in inspector.get_indexes("tmdb_media_cache")}
    if "ix_tmdb_media_cache_credits_expires_at" in indexes:
        op.drop_index("ix_tmdb_media_cache_credits_expires_at", table_name="tmdb_media_cache")

    existing = {c["name"] for c in inspector.get_columns("tmdb_media_cache")}
    with op.batch_alter_table("tmdb_media_cache") as batch_op:
        for name in ("credits_last_error", "credits_fail_count", "credits_expires_at", "credits_fetched_at", "credits_json"):
            if name in existing:
                batch_op.drop_column(name)
