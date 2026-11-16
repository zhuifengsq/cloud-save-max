"""Enforce one global default and store successful profile refresh time."""
from alembic import op
import sqlalchemy as sa

revision = '20261116_default_profile_time'
down_revision = '20261116_account_row_version'
branch_labels = None
depends_on = None


def upgrade():
    # Historical last_checked_at can describe a failed probe, so do not invent
    # a successful profile timestamp for existing records.
    op.add_column('drive_accounts', sa.Column('profile_updated_at', sa.DateTime(timezone=True), nullable=True))
    accounts = sa.table('drive_accounts', sa.column('id', sa.Integer()),
        sa.column('is_default', sa.Boolean()), sa.column('row_version', sa.Integer()))
    connection = op.get_bind()
    keep = connection.execute(sa.select(sa.func.min(accounts.c.id)).where(accounts.c.is_default.is_(True))).scalar()
    if keep is not None:
        connection.execute(accounts.update().where(accounts.c.is_default.is_(True), accounts.c.id != keep)
            .values(is_default=False, row_version=accounts.c.row_version + 1))
    op.create_index('uq_drive_accounts_single_default', 'drive_accounts', ['is_default'], unique=True,
        sqlite_where=sa.column('is_default').is_(True), postgresql_where=sa.column('is_default').is_(True))


def downgrade():
    op.drop_index('uq_drive_accounts_single_default', table_name='drive_accounts')
    op.drop_column('drive_accounts', 'profile_updated_at')
