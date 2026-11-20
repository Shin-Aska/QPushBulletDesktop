"""Quarantine retained legacy inventory/history/outbox by provider account."""
from alembic import op
import sqlalchemy as sa
revision='0005'
down_revision='0004'
branch_labels=None
depends_on=None


def upgrade():
    """Partition items, actions, locked envelopes, and devices by account."""
    for table in ('items','actions','locked_envelopes'):
        op.add_column(table,sa.Column('account_id',sa.Text(),nullable=False,server_default=''))
        op.create_index('ix_'+table+'_account',table,['account_id'])
    # No tables reference devices: safely retain every existing row while making
    # account + provider identity the primary key.
    op.rename_table('devices','devices_before_accounts')
    op.create_table('devices',sa.Column('account_id',sa.Text(),primary_key=True),sa.Column('id',sa.Text(),primary_key=True),
        sa.Column('nickname',sa.Text(),nullable=False),sa.Column('type',sa.Text()),sa.Column('manufacturer',sa.Text()),sa.Column('model',sa.Text()),
        sa.Column('active',sa.Integer(),nullable=False),sa.Column('can_push',sa.Integer(),nullable=False),sa.Column('can_sms',sa.Integer(),nullable=False))
    op.execute("INSERT INTO devices SELECT '',id,nickname,type,manufacturer,model,active,can_push,can_sms FROM devices_before_accounts")
    op.drop_table('devices_before_accounts')
    # Existing installations already know their account after history sync.
    # The runtime atomically claims unowned rows for that account on first open.


def downgrade():
    """Refuse schema downgrade and require restoring a verified backup.

    :raises RuntimeError: Always raised because schema downgrades are unsupported.
    """
    raise RuntimeError('Restore a verified backup to downgrade')
