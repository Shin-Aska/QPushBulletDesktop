"""Keep source/destination association and actual local decryption evidence."""
from alembic import op
import sqlalchemy as sa
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None


def upgrade():
    """Add source device, target device, and decryption columns to items."""
    op.add_column('items',sa.Column('source_device_id',sa.Text()))
    op.add_column('items',sa.Column('target_device_id',sa.Text()))
    op.add_column('items',sa.Column('decrypted',sa.Integer(),nullable=False,server_default='0'))
    op.execute('UPDATE items SET source_device_id=device_id')


def downgrade():
    """Refuse schema downgrade and require restoring a verified backup.

    :raises RuntimeError: Always raised because schema downgrades are unsupported.
    """
    raise RuntimeError('Restore a verified backup to downgrade')
