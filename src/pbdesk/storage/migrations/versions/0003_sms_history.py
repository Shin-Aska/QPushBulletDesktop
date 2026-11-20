"""Account/device-partitioned SMS history, synchronization and local key references."""
from alembic import op
import sqlalchemy as sa
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    """Create SMS history, sync, thread, envelope, and encryption tables."""
    op.create_table('sms_messages', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Text(), nullable=False), sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('conversation_id', sa.Text(), nullable=False), sa.Column('message_id', sa.Text(), nullable=False),
        sa.Column('direction', sa.Text(), nullable=False), sa.Column('body', sa.Text(), nullable=False),
        sa.Column('timestamp', sa.Float(), nullable=False), sa.Column('addresses', sa.Text(), nullable=False),
        sa.Column('name', sa.Text()), sa.Column('decrypted', sa.Integer(), nullable=False),
        sa.UniqueConstraint('account_id','device_id','conversation_id','message_id','direction',name='uq_sms_identity'))
    op.create_index('ix_sms_device_time','sms_messages',['account_id','device_id','timestamp'])
    op.create_table('sms_sync',sa.Column('account_id',sa.Text(),primary_key=True),sa.Column('device_id',sa.Text(),primary_key=True),
        sa.Column('state',sa.Text(),nullable=False),sa.Column('last_success_at',sa.Float()),sa.Column('error',sa.Text()),
        sa.Column('has_more',sa.Integer(),nullable=False))
    op.create_table('sms_threads',sa.Column('account_id',sa.Text(),primary_key=True),sa.Column('device_id',sa.Text(),primary_key=True),
        sa.Column('conversation_id',sa.Text(),primary_key=True),sa.Column('fingerprint',sa.Text(),nullable=False))
    op.create_table('sms_envelopes',sa.Column('account_id',sa.Text(),primary_key=True),sa.Column('device_id',sa.Text(),primary_key=True),
        sa.Column('permanent_key',sa.Text(),primary_key=True),sa.Column('payload',sa.Text(),nullable=False))
    op.create_table('device_encryption',sa.Column('account_id',sa.Text(),primary_key=True),sa.Column('device_id',sa.Text(),primary_key=True),
        sa.Column('mode',sa.Text(),nullable=False),sa.Column('entry',sa.Text(),nullable=False))


def downgrade():
    """Refuse schema downgrade and require restoring a verified backup.

    :raises RuntimeError: Always raised because schema downgrades are unsupported.
    """
    raise RuntimeError('Restore a verified backup to downgrade')
