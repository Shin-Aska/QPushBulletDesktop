"""Core-owned SQLite persistence; clients must use the private API."""
from datetime import datetime, timezone
import hashlib
import fcntl
import os
import json
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer,
                        Text, ForeignKey, select, insert, update, func, text)
from pbdesk.storage.workflows import WorkflowStore
from pbdesk.storage.sms import SmsStore
from pbdesk.storage.summaries import SummaryRepository

metadata = MetaData()
items = Table("items", metadata,
    Column("id", Integer, primary_key=True), Column("account_id",Text,nullable=False,default=""), Column("source_id", Text, unique=True, nullable=False),
    Column("type", Text, nullable=False), Column("title", Text, nullable=False),
    Column("body", Text, nullable=False), Column("url", Text), Column("device_id", Text),
    Column("source_device_id",Text),Column("target_device_id",Text),Column("decrypted",Integer,default=0),
    *(Column(k, Text) for k in ("application_name", "icon", "file_name", "file_type", "file_url")),
    Column("created_at", Text, nullable=False), Column("fingerprint", Text, nullable=False),
    Column("notification_claimed", Integer, nullable=False, default=0))
events = Table("events", metadata, Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("type", Text, nullable=False), Column("item_id", ForeignKey("items.id"), nullable=False))
settings_table = Table("settings", metadata, Column("key", Text, primary_key=True), Column("value", Text, nullable=False))
PUBLIC = ("id", "source_id", "type", "title", "body", "url", "device_id", "created_at", "source_device_id", "target_device_id", "decrypted", "application_name", "icon", "file_name", "file_type", "file_url")

class Store(WorkflowStore, SmsStore):
    def __init__(self, settings, *, database_key=None, _lock_fd=None):
        self.settings = settings
        root = settings.application_dir
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pbdesk.sqlite3"
        if self.path.is_symlink():
            raise RuntimeError("Database must not be a symbolic link")
        from pbdesk.services.history_security import existing_key
        self._database_key = database_key if database_key is not None else existing_key(settings)
        if not isinstance(self._database_key, bytes) or len(self._database_key) != 32:
            raise RuntimeError("A verified history encryption key is required")
        self._database_lock = _lock_fd
        if self._database_lock is None:
            self._database_lock = os.open(root / ".pbdesk-database.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(self._database_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(self._database_lock)
                raise RuntimeError("A pbdesk core already owns this application database") from None
        try:
            legacy = settings.state_dir / "pbdesk.sqlite3"
            if legacy.resolve() != self.path.resolve() and legacy.exists():
                raise RuntimeError("Legacy history requires explicit verified migration")
            if self.path.exists() and self.path.stat().st_size:
                with self.path.open("rb") as stream:
                    if stream.read(16) == b"SQLite format 3\x00":
                        raise RuntimeError("Plaintext history requires explicit verified migration")
            if not self.path.exists():
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            if self.path.stat().st_uid != os.getuid():
                raise RuntimeError("Database must be owned by the current user")
            self.path.chmod(0o600)
            self._open_database()
        except BaseException:
            from pbdesk.configuration import unregister
            unregister(settings, self)
            if hasattr(self, "engine"):
                self.engine.dispose()
            os.close(self._database_lock)
            self._database_lock = None
            raise

    def _open_database(self):
        from pbdesk.storage.cipher import connect, driver
        from sqlalchemy.pool import QueuePool
        self.engine = create_engine("sqlite://", module=driver(), poolclass=QueuePool,
            creator=lambda: connect(self.path, self._database_key))
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE VIRTUAL TABLE temp.fts_probe USING fts5(value)")
            conn.exec_driver_sql("DROP TABLE temp.fts_probe")
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
        self.path.chmod(0o600)
        self.summaries = SummaryRepository(self)
        from pbdesk.configuration import import_legacy, register
        import_legacy(self.settings, self)
        register(self.settings, self)
        self.instance_id = str(uuid.uuid4())
        current=self.sms_account()
        if current:
            self.set_setting("pushbullet_account_id",current)
        self.recover_actions()
        self.recover_sms_sync()
        push_state=self.get_setting("push_sync")
        if isinstance(push_state,dict) and push_state.get("state")=="refreshing":
            self.set_setting("push_sync",{**push_state,"state":"idle","error":None})

    def close(self):
        from pbdesk.configuration import unregister
        unregister(self.settings, self)
        self.engine.dispose()
        self._database_key = None
        if self._database_lock is not None:
            os.close(self._database_lock)
            self._database_lock = None

    ACCOUNT_SETTINGS={'current_device_id','device_registration_pending','devices_synced_at','push_sync'}

    def account_key(self):
        return self.get_setting('pushbullet_account_id') or ''

    @staticmethod
    def scoped_identity(account,identity):
        return 'account:'+json.dumps([account,identity],separators=(',',':')) if account else identity

    @staticmethod
    def unscoped_identity(identity):
        if identity.startswith('account:'):
            try:
                value=json.loads(identity[8:])
                if isinstance(value,list) and len(value)==2:
                    return value[1]
            except (ValueError,TypeError):
                pass
        return identity

    def _setting_key(self,key):
        if key in self.ACCOUNT_SETTINGS:
            account=self.account_key()
            return key+':'+account if account else key
        return key

    def get_setting(self, key, default=None):
        key=self._setting_key(key)
        with self.engine.connect() as conn:
            result = conn.execute(select(settings_table.c.value).where(settings_table.c.key == key)).scalar_one_or_none()
            return default if result is None else json.loads(result)

    def set_setting(self, key, value):
        from sqlalchemy.dialects.sqlite import insert as upsert
        from pbdesk.storage.workflows import devices,actions,locked
        from pbdesk.storage.sms import encryption as key_references
        key=self._setting_key(key)
        with self.writing() as conn:
            if key=='pushbullet_account_id' and isinstance(value,str) and value:
                # Claim legacy unowned rows once. Already-owned rows never move
                # across accounts, including ready actions and cipher recovery.
                for row in conn.execute(select(items.c.id,items.c.source_id).where(items.c.account_id=='')).mappings():
                    conn.execute(update(items).where(items.c.id==row['id']).values(account_id=value,source_id=self.scoped_identity(value,row['source_id'])))
                for row in conn.execute(select(actions.c.id,actions.c.client_request_id).where(actions.c.account_id=='')).mappings():
                    conn.execute(update(actions).where(actions.c.id==row['id']).values(account_id=value,client_request_id=self.scoped_identity(value,row['client_request_id'])))
                for row in conn.execute(select(locked.c.id,locked.c.digest).where(locked.c.account_id=='')).mappings():
                    conn.execute(update(locked).where(locked.c.id==row['id']).values(account_id=value,digest=self.scoped_identity(value,row['digest'])))
                conn.execute(update(devices).where(devices.c.account_id=='').values(account_id=value))
                conn.execute(update(key_references).where(key_references.c.account_id=='').values(account_id=value))
                for name in self.ACCOUNT_SETTINGS:
                    existing=conn.execute(select(settings_table.c.value).where(settings_table.c.key==name)).scalar_one_or_none()
                    if existing is not None:
                        statement=upsert(settings_table).values(key=name+':'+value,value=existing)
                        conn.execute(statement.on_conflict_do_nothing(index_elements=['key']))
                        conn.execute(settings_table.delete().where(settings_table.c.key==name))
            statement = upsert(settings_table).values(key=key, value=json.dumps(value))
            conn.execute(statement.on_conflict_do_update(index_elements=['key'], set_={'value': statement.excluded.value}))

    def ingest(self, item,account=None):
        item=dict(item)
        account=self.account_key() if account is None else account
        item["source_id"]=self.scoped_identity(account,item["source_id"])
        fingerprint_data = {k: v for k, v in item.items() if not (item["type"] == "mirror" and k == "created_at")}
        fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
        with self.engine.begin() as conn:
            previous = conn.execute(select(items).where(items.c.source_id == item["source_id"])).mappings().first()
            if previous and previous["fingerprint"] == fingerprint:
                return None
            if previous:
                item_id = previous["id"]
                values = {**item, "fingerprint": fingerprint, "decrypted": 0}
                values.pop("created_at", None)
                conn.execute(update(items).where(items.c.id == item_id).values(**values))
                kind = "item.updated"
            else:
                result = conn.execute(insert(items).values(**item, account_id=account, fingerprint=fingerprint, notification_claimed=0))
                item_id = result.inserted_primary_key[0]
                kind = "item.created"
            seq = conn.execute(insert(events).values(type=kind, item_id=item_id)).inserted_primary_key[0]
            # One global retained replay window; item history remains authoritative.
            conn.execute(events.delete().where(events.c.sequence <= seq - 256))
            return {"sequence": seq, "type": kind, "item_id": item_id, "instance_id": self.instance_id}

    def mark_decrypted(self,item_id):
        with self.engine.begin() as conn:
            conn.execute(update(items).where(items.c.id==item_id).values(decrypted=1))

    def claim_notification(self, item_id):
        with self.engine.begin() as conn:
            result = conn.execute(update(items).where(items.c.id == item_id, items.c.notification_claimed == 0).values(notification_claimed=1))
            return result.rowcount == 1

    @staticmethod
    def public(row):
        return {**{key: row[key] for key in PUBLIC}, "source_id":Store.unscoped_identity(row["source_id"]), "decrypted": bool(row["decrypted"])}

    def item(self, item_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(items).where(items.c.id == item_id,items.c.account_id==self.account_key())).mappings().first()
            return self.public(row) if row else None

    @staticmethod
    def _account_at(conn):
        value=conn.execute(select(settings_table.c.value).where(settings_table.c.key=='pushbullet_account_id')).scalar_one_or_none()
        return json.loads(value) if value else ''

    def _list_items(self, conn, query=None,account=None):
        account=self._account_at(conn) if account is None else account
        statement = select(items).where(items.c.account_id==account)
        if query:
            # Treat user input as literal terms, never raw FTS operators.
            terms = [f'"{part.replace(chr(34), chr(34)*2)}"' for part in query.split()[:32]]
            if terms:
                matches = text("SELECT rowid FROM items_fts WHERE items_fts MATCH :q").bindparams(q=" AND ".join(terms))
                statement = statement.where(items.c.id.in_(matches))
        rows = conn.execute(statement.order_by(items.c.id.desc()).limit(500)).mappings()
        return [self.public(row) for row in rows]

    def list_items(self, query=None):
        with self.engine.connect() as conn:
            return self._list_items(conn, query)

    def snapshot(self, query=None):
        # Explicit BEGIN gives both SELECTs one SQLite snapshot even with the
        # driver's legacy transaction behavior; ingestion cannot fall between them.
        with self.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN")
            account=self._account_at(conn)
            values = self._list_items(conn, query,account)
            latest = conn.execute(select(func.max(events.c.sequence)).select_from(events.join(items)).where(items.c.account_id==account)).scalar() or 0
            return {"account_id":account or None,"items": values, "latest_sequence": latest, "instance_id": self.instance_id}

    def event_bounds(self):
        with self.engine.connect() as conn:
            lo, hi = conn.execute(select(func.min(events.c.sequence), func.max(events.c.sequence)).select_from(events.join(items)).where(items.c.account_id==self.account_key())).one()
            return lo or 0, hi or 0

    def events_after(self, sequence):
        with self.engine.connect() as conn:
            return [{**dict(row), "instance_id": self.instance_id} for row in conn.execute(select(events).select_from(events.join(items)).where(events.c.sequence > sequence,items.c.account_id==self.account_key()).order_by(events.c.sequence).limit(256)).mappings()]
