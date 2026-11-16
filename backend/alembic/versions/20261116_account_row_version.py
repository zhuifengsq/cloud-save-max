"""Protect account writes against stale ORM snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '20261116_account_row_version'
down_revision = '20261116_drive_credentials'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('drive_accounts', sa.Column('row_version', sa.Integer(), nullable=False, server_default='1'))


def downgrade():
    op.drop_column('drive_accounts', 'row_version')
