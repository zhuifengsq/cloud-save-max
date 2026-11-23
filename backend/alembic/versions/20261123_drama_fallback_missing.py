"""Track repeatedly missing share directories separately from active batches."""
from alembic import op
import sqlalchemy as sa

revision = "20261123_drama_fallback_missing"
down_revision = "20261123_drama_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("drama_transfer_checkpoints", sa.Column(
        "fallback_missing", sa.JSON(), server_default="{}", nullable=False
    ))


def downgrade():
    op.drop_column("drama_transfer_checkpoints", "fallback_missing")
