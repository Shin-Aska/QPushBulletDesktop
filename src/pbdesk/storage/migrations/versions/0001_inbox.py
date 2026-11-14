"""Initial private inbox, durable events and singleton settings.

Frozen definitions: later application metadata must never alter this revision.
"""
from alembic import op
import sqlalchemy as sa
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("items", sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("source_id",sa.Text(),nullable=False,unique=True),
        sa.Column("type",sa.Text(),nullable=False), sa.Column("title",sa.Text(),nullable=False),
        sa.Column("body",sa.Text(),nullable=False),sa.Column("url",sa.Text()),sa.Column("device_id",sa.Text()),
        sa.Column("created_at",sa.Text(),nullable=False),sa.Column("fingerprint",sa.Text(),nullable=False),
        sa.Column("notification_claimed",sa.Integer(),nullable=False))
    op.create_table("events",sa.Column("sequence",sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column("type",sa.Text(),nullable=False),sa.Column("item_id",sa.Integer(),sa.ForeignKey("items.id"),nullable=False))
    op.create_table("settings",sa.Column("key",sa.Text(),primary_key=True),sa.Column("value",sa.Text(),nullable=False))
    op.execute("CREATE VIRTUAL TABLE items_fts USING fts5(title, body, content='items', content_rowid='id')")
    op.execute("CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN INSERT INTO items_fts(rowid,title,body) VALUES(new.id,new.title,new.body); END")
    op.execute("CREATE TRIGGER items_au AFTER UPDATE ON items BEGIN INSERT INTO items_fts(items_fts,rowid,title,body) VALUES('delete',old.id,old.title,old.body); INSERT INTO items_fts(rowid,title,body) VALUES(new.id,new.title,new.body); END")
    op.execute("CREATE TRIGGER items_ad AFTER DELETE ON items BEGIN INSERT INTO items_fts(items_fts,rowid,title,body) VALUES('delete',old.id,old.title,old.body); END")


def downgrade():
    raise RuntimeError("Restore a verified backup to downgrade")
