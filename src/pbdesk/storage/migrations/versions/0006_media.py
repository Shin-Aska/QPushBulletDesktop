"""Retain bounded public media metadata without replacing existing history."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    """Add bounded media metadata columns to items and sms_messages."""
    for name in ('application_name', 'icon', 'file_name', 'file_type', 'file_url'):
        op.add_column('items', sa.Column(name, sa.Text(), nullable=True))
    for name in ('image_urls', 'recipients'):
        op.add_column('sms_messages', sa.Column(name, sa.Text(), nullable=False, server_default='[]'))


def downgrade():
    """Refuse schema downgrade and require restoring a verified backup.

    :raises RuntimeError: Always raised because schema downgrades are unsupported.
    """
    raise RuntimeError('Restore a verified backup to downgrade')
