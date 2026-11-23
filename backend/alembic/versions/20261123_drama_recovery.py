"""Track confirmation attempts and version manual recovery operations."""
from alembic import op
import sqlalchemy as sa

revision = "20261123_drama_recovery"
down_revision = "20261123_drama_checkpoint"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("drama_transfer_checkpoints", sa.Column(
        "version", sa.String(32), server_default="legacy", nullable=False
    ))
    op.add_column("drama_transfer_checkpoints", sa.Column(
        "recovery", sa.JSON(), server_default="{}", nullable=False
    ))


def downgrade():
    # The earlier executor loads these JSON batches into a fixed dataclass.
    fields = {"output_fids", "rename_signature"}
    checkpoints = sa.table(
        "drama_transfer_checkpoints", sa.column("task_id", sa.Integer()),
        sa.column("batch", sa.JSON(none_as_null=True)), sa.column("confirmed", sa.JSON()),
    )
    conn = op.get_bind()
    for row in conn.execute(sa.select(checkpoints)).mappings().all():
        batch = row["batch"]
        legacy_batch = {key: value for key, value in batch.items() if key not in fields} if batch is not None else None
        confirmed = [
            {key: value for key, value in item.items() if key not in fields}
            for item in row["confirmed"]
        ]
        if legacy_batch != batch or confirmed != row["confirmed"]:
            conn.execute(checkpoints.update().where(checkpoints.c.task_id == row["task_id"])
                         .values(batch=legacy_batch, confirmed=confirmed))
    op.drop_column("drama_transfer_checkpoints", "recovery")
    op.drop_column("drama_transfer_checkpoints", "version")
