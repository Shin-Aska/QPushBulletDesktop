"""Keep source/destination association and actual local decryption evidence."""
from alembic import op
import sqlalchemy as sa
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('items',sa.Column('source_device_id',sa.Text()))
    op.add_column('items',sa.Column('target_device_id',sa.Text()))
    op.add_column('items',sa.Column('decrypted',sa.Integer(),nullable=False,server_default='0'))
    op.execute('UPDATE items SET source_device_id=device_id')


def downgrade():
    raise RuntimeError('Restore a verified backup to downgrade')
