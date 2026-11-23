"""Persist in-flight Quark drama transfers."""
from alembic import op
import sqlalchemy as sa

revision = "20261123_drama_checkpoint"
down_revision = "20261116_default_profile_time"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "drama_transfer_checkpoints",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("batch", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("fallback_dirs", sa.JSON(), nullable=False),
        sa.Column("confirmed", sa.JSON(), nullable=False),
        sa.Column("renames_started", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade():
    op.drop_table("drama_transfer_checkpoints")
