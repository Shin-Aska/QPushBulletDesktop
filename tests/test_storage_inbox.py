import json
from pbdesk.config import Settings
from pbdesk.storage.database import Store
from pbdesk.pushbullet.normalize import DEMO, normalize


def test_migration_search_restart_and_identity(tmp_path):
    settings=Settings(tmp_path)
    store=Store(settings)
    assert store.ingest(normalize(DEMO))["sequence"] == 1
    assert store.ingest(normalize(DEMO)) is None
    mirror={"type":"push","push":{"type":"mirror","source_device_iden":"phone-A","package_name":"org.example","notification_id":"7","notification_tag":"A","title":"<b>Mirror</b>","body":"unique phrase"}}
    item=normalize(mirror)
    change=store.ingest(item)
    assert change["item_id"] == 2
    assert store.ingest(normalize(mirror)) is None
    mirror["push"]["body"]="updated phrase"
    assert store.ingest(normalize(mirror))["type"] == "item.updated"
    assert store.list_items("updated")[0]["id"] == 2
    assert not store.list_items("unique")
    mirror["push"]["notification_tag"]="B"
    assert store.ingest(normalize(mirror))["item_id"] == 3
    with store.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0005"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
    store.close()
    store=Store(settings)
    assert store.ingest(normalize(DEMO)) is None
    assert len(store.list_items()) == 3
    store.close()


def test_bounded_durable_events(tmp_path):
    store=Store(Settings(tmp_path))
    for i in range(300):
        item=normalize(dict(DEMO,iden=str(i)))
        store.ingest(item)
    assert store.event_bounds() == (45,300)
    assert len(store.events_after(0)) == 256
    store.close()


def test_malformed_or_encrypted_events_not_silently_inbox():
    for value in (None, [], {}, {"type":"push","push":"bad"}, {"type":"mirror","notification_id":"1"}, {"type":"note","iden":"x","created":"broken"}, {"type":"mirror","encrypted":True}):
        assert normalize(value) is None


def test_untrusted_type_and_timestamp_shapes_are_skipped():
    for kind in ([], {}, True, None):
        assert normalize({"type":kind,"iden":"bad","created":0}) is None
    for value in (True, float("nan"), float("inf"), float("-inf"), -1, 1e300, "1"):
        assert normalize({"type":"note","iden":"bad","created":value}) is None
    assert normalize(DEMO)["source_id"] == "stored:pbdesk-sanitized-demo-v1"
    assert DEMO["type"] == "note" and "push" not in DEMO
