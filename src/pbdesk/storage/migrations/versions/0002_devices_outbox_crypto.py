"""Device cache, approved outbox and bounded encrypted recovery queue."""
from alembic import op
import sqlalchemy as sa
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("devices",sa.Column("id",sa.Text(),primary_key=True),
        sa.Column("nickname",sa.Text(),nullable=False),sa.Column("type",sa.Text()),
        sa.Column("manufacturer",sa.Text()),sa.Column("model",sa.Text()),
        sa.Column("active",sa.Integer(),nullable=False),sa.Column("can_push",sa.Integer(),nullable=False),
        sa.Column("can_sms",sa.Integer(),nullable=False))
    op.create_table("actions",sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("client_request_id",sa.Text(),unique=True,nullable=False),
        sa.Column("request_fingerprint",sa.Text(),nullable=False),
        sa.Column("requested_by",sa.Text(),nullable=False),
        sa.Column("approved_revision",sa.Integer()),sa.Column("approved_fingerprint",sa.Text()),
        sa.Column("revision",sa.Integer(),nullable=False),sa.Column("fingerprint",sa.Text(),nullable=False),
        sa.Column("kind",sa.Text(),nullable=False),sa.Column("device_id",sa.Text(),nullable=False),
        sa.Column("recipient",sa.Text()),sa.Column("title",sa.Text(),nullable=False),
        sa.Column("body",sa.Text(),nullable=False),sa.Column("url",sa.Text()),
        sa.Column("transport",sa.Text(),nullable=False),sa.Column("state",sa.Text(),nullable=False),
        sa.Column("created_at",sa.Text(),nullable=False),sa.Column("expires_at",sa.Text(),nullable=False),
        sa.Column("updated_at",sa.Text(),nullable=False),sa.Column("upstream_id",sa.Text()),
        sa.Column("error_code",sa.Text()),sa.Column("error_message",sa.Text()))
    op.create_index("ix_actions_state", "actions", ["state", "id"])
    op.create_table("action_audit",sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("action_id",sa.Integer(),sa.ForeignKey("actions.id"),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False),sa.Column("fingerprint",sa.Text(),nullable=False),sa.Column("state",sa.Text(),nullable=False),
        sa.Column("actor",sa.Text(),nullable=False),sa.Column("at",sa.Text(),nullable=False),
        sa.Column("error_code",sa.Text()))
    op.create_table("locked_envelopes",sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("digest",sa.Text(),unique=True,nullable=False),sa.Column("payload",sa.Text(),nullable=False),
        sa.Column("created_at",sa.Text(),nullable=False),sa.Column("status",sa.Text(),nullable=False))


def downgrade():
    raise RuntimeError("Restore a verified backup to downgrade")
