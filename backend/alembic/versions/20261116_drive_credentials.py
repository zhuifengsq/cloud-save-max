"""Add opt-in credential envelope columns; never migrate live credentials implicitly."""
from alembic import op
import sqlalchemy as sa

revision = '20261116_drive_credentials'
down_revision = '20261106_drama_lead_in'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('drive_accounts', sa.Column('credential_uuid', sa.String(36), nullable=True))
    op.add_column('drive_accounts', sa.Column('credential_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('drive_accounts', sa.Column('credential_blob', sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT count(*) FROM drive_accounts WHERE credential_version <> 0')).scalar():
        raise RuntimeError('Restore a matching pre-encryption backup before downgrade')
    op.drop_column('drive_accounts', 'credential_blob')
    op.drop_column('drive_accounts', 'credential_version')
    op.drop_column('drive_accounts', 'credential_uuid')
