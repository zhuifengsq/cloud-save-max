"""Allow verified folder selection changes for directory-only checkpoints."""
from alembic import op
import sqlalchemy as sa

revision = "20261123_drama_folder_scope"
down_revision = "20261123_drama_fallback_missing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("drama_transfer_checkpoints", sa.Column("fallback_scope", sa.String(64), nullable=True))
    op.add_column("drama_transfer_checkpoints", sa.Column(
        "fallback_excluded", sa.JSON(), server_default="[]", nullable=False
    ))


def downgrade():
    op.drop_column("drama_transfer_checkpoints", "fallback_excluded")
    op.drop_column("drama_transfer_checkpoints", "fallback_scope")
