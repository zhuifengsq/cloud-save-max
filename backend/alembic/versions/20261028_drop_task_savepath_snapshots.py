"""drop task_savepath_snapshots

进度改为执行时计算并保存在 tasks.extra_json.saved_latest，不再使用快照表。

Revision ID: 20261028_drop_task_savepath_snapshots
Revises: 20260706_drop_enddate_update_subdir
Create Date: 2026-10-28 00:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '20261028_drop_task_savepath_snapshots'
down_revision = '20260706_drop_enddate_update_subdir'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'task_savepath_snapshots' not in inspector.get_table_names():
        return
    op.drop_table('task_savepath_snapshots')


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'task_savepath_snapshots' in inspector.get_table_names():
        return

    op.create_table(
        'task_savepath_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('task_uid', sa.String(length=64), nullable=False),
        sa.Column('task_execution_id', sa.Integer(), nullable=True),
        sa.Column('drive_account_id', sa.Integer(), nullable=True),
        sa.Column('savepath', sa.String(length=255), nullable=False),
        sa.Column('files_json', sa.Text(), nullable=False),
        sa.Column('file_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_size', sa.BigInteger(), nullable=True),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('saved_latest_season', sa.Integer(), nullable=True),
        sa.Column('saved_latest_episode', sa.Integer(), nullable=True),
        sa.Column('saved_latest_name', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['task_uid'], ['tasks.task_uid'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_execution_id'], ['task_executions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['drive_account_id'], ['drive_accounts.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('task_uid', name='uq_task_savepath_snapshots_task_uid'),
    )
    op.create_index('ix_task_savepath_snapshots_task_execution_id', 'task_savepath_snapshots', ['task_execution_id'])
    op.create_index('ix_task_savepath_snapshots_drive_account_id', 'task_savepath_snapshots', ['drive_account_id'])
