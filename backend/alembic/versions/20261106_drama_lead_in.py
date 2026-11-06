"""drama lead in records

Revision ID: 20261106_drama_lead_in
Revises: 20261105_tmdb_refresh_claim
Create Date: 2026-11-06 21:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261106_drama_lead_in"
down_revision = "20261105_tmdb_refresh_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "task_lead_in_records" not in tables:
        op.create_table(
            "task_lead_in_records",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("drive_type", sa.String(length=32), nullable=False),
            sa.Column("account_name", sa.String(length=128), server_default="", nullable=False),
            sa.Column("dir_path", sa.String(length=512), server_default="", nullable=False),
            sa.Column("material_name", sa.String(length=512), server_default="", nullable=False),
            sa.Column("material_fid", sa.String(length=256), server_default="", nullable=False),
            sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
            sa.Column("message", sa.String(length=512), server_default="", nullable=False),
            sa.Column("config_version", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("task_id", "drive_type", name="uq_task_lead_in_records_task_drive"),
        )
        op.create_index("ix_task_lead_in_records_task_id", "task_lead_in_records", ["task_id"], unique=False)
    else:
        existing_indexes = {item.get("name") for item in inspector.get_indexes("task_lead_in_records")}
        if "ix_task_lead_in_records_task_id" not in existing_indexes:
            op.create_index("ix_task_lead_in_records_task_id", "task_lead_in_records", ["task_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "task_lead_in_records" in tables:
        existing_indexes = {item.get("name") for item in inspector.get_indexes("task_lead_in_records")}
        if "ix_task_lead_in_records_task_id" in existing_indexes:
            op.drop_index("ix_task_lead_in_records_task_id", table_name="task_lead_in_records")
        op.drop_table("task_lead_in_records")
