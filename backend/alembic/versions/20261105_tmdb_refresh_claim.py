"""Track the current TMDB credits refresh owner."""

from alembic import op
import sqlalchemy as sa


revision = "20261105_tmdb_refresh_claim"
down_revision = "20261105_credits_in_media_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tmdb_media_cache", sa.Column("credits_refresh_started_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("tmdb_media_cache", "credits_refresh_started_at")
