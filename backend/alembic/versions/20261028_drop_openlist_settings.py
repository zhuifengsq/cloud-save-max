"""drop openlist_settings

移除 OpenList 集成（配置表、接口、浏览能力）。

Revision ID: 20261028_drop_openlist_settings
Revises: 20261028_drop_task_savepath_snapshots
Create Date: 2026-10-28 02:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '20261028_drop_openlist_settings'
down_revision = '20261028_drop_task_savepath_snapshots'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'openlist_settings' not in inspector.get_table_names():
        return
    op.drop_table('openlist_settings')


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'openlist_settings' in inspector.get_table_names():
        return
    op.create_table(
        'openlist_settings',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('token', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
