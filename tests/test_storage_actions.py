from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
import pytest
from pbdesk.config import Settings
from pbdesk.domain.actions import ActionRequest,ActionEdit,Approval,ActionConflict,InvalidDestination
from pbdesk.storage.database import Store
from pbdesk.storage.workflows import actions,utcnow
from sqlalchemy import update


def device(**overrides):
    return dict(id="fake-phone",nickname="fake",type="android",manufacturer=None,model=None,active=True,can_push=True,can_sms=True,**overrides)


def request(**overrides):
    values=dict(client_request_id=uuid4(),kind="note",device_id="fake-phone",body="exact body",transport="provider_plaintext")
    values.update(overrides)
    return ActionRequest(**values)


@pytest.fixture
def store(tmp_path):
    value=Store(Settings(tmp_path));value.save_devices([device()]);yield value;value.close()


def approve(store,action):
    return store.approve_action(action["id"],Approval(revision=action["revision"],fingerprint=action["fingerprint"],allow_provider_plaintext=True),"desktop")


def test_idempotency_preserves_original_duration_after_clock_and_edit(store,monkeypatch):
    original=request()
    action=store.create_action(original,"mcp")
    assert action["requested_by"]=="mcp" and action["state"]=="pending_approval"
    future=utcnow()+timedelta(seconds=120)
    monkeypatch.setattr("pbdesk.storage.workflows.utcnow",lambda:future)
    assert store.create_action(original,"mcp")==action
    edited=store.edit_action(action["id"],ActionEdit(expected_revision=1,kind="note",device_id="fake-phone",body="edited",transport="provider_plaintext"),"desktop")
    assert edited["revision"]==2 and edited["expires_at"]==action["expires_at"]
    assert store.create_action(original,"mcp")==edited
    with pytest.raises(ActionConflict):
        store.create_action(original.model_copy(update={"body":"different"}),"mcp")


def test_edit_invalidates_exact_approval_and_atomic_claim(store):
    action=store.create_action(request(),"desktop")
    ready=approve(store,action)
    edited=store.edit_action(action["id"],ActionEdit(expected_revision=1,kind="note",device_id="fake-phone",body="edited",transport="provider_plaintext"),"desktop")
    assert edited["state"]=="pending_approval" and edited["fingerprint"]!=action["fingerprint"]
    with pytest.raises(ActionConflict): approve(store,action)
    approve(store,edited)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(lambda _:store.claim_action(action["id"]),range(2)))
    assert sum(value is not None for value in claims)==1
    store.finish_action(action["id"],"upstream_accepted",upstream_id="fake-upstream")
    assert store.action(action["id"])["state"]=="upstream_accepted"
    assert [row["state"] for row in store.action_audit(action["id"])]==["pending_approval","ready","pending_approval","ready","submitting","upstream_accepted"]


def test_no_claim_expired_inactive_or_cancelled(store):
    action=store.create_action(request(),"desktop");approve(store,action)
    with store.engine.begin() as conn:
        conn.execute(update(actions).where(actions.c.id==action["id"]).values(expires_at=(utcnow()-timedelta(seconds=1)).isoformat()))
    assert store.claim_action(action["id"]) is None
    assert store.action(action["id"])["state"]=="expired"
    action=store.create_action(request(),"desktop");approve(store,action)
    store.save_devices([])
    assert store.claim_action(action["id"]) is None
    assert store.action(action["id"])["error_code"]=="device_unavailable"
    store.save_devices([device()])
    action=store.create_action(request(),"desktop");approve(store,action)
    store.cancel_action(action["id"],1,"desktop")
    assert store.claim_action(action["id"]) is None


def test_restart_submitting_uncertain_and_ready_preserved(tmp_path):
    settings=Settings(tmp_path);store=Store(settings);store.save_devices([device()])
    one=store.create_action(request(),"desktop");approve(store,one);store.claim_action(one["id"])
    two=store.create_action(request(),"desktop");approve(store,two)
    store.close();store=Store(settings)
    assert store.action(one["id"])["state"]=="uncertain"
    assert store.action(two["id"])["state"]=="ready"
    assert store.claim_action(two["id"]) is not None
    store.close()


def test_destination_validation_and_transport_are_explicit(store):
    from pydantic import ValidationError
    for values in ({"kind":"sms","recipient":"12345"},{"kind":"sms","recipient":"+12345","device_id":"missing"},{"kind":"link","url":"javascript:alert(1)"}):
        with pytest.raises((ValidationError,InvalidDestination)):
            store.create_action(request(**values),"mcp")
    with pytest.raises(ValidationError):
        ActionRequest(client_request_id=uuid4(),kind="note",device_id="fake-phone",body="x")
    with pytest.raises(ValidationError):
        ActionRequest(client_request_id=uuid4(),kind="note",device_id="fake-phone",body="x",transport="provider_plaintext",confirmed=True)


def test_migration_from_frozen_v1_preserves_history_and_search(tmp_path):
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import create_engine,inspect
    from pathlib import Path
    import pbdesk.storage.database as module
    settings=Settings(tmp_path)
    engine=create_engine(f"sqlite:///{tmp_path/'pbdesk.sqlite3'}")
    config=Config();config.set_main_option("script_location",str(Path(module.__file__).parent/'migrations'))
    with engine.begin() as conn:
        config.attributes['connection']=conn;command.upgrade(config,'0001')
        assert not {'devices','actions','action_audit','locked_envelopes'} & set(inspect(conn).get_table_names())
        conn.exec_driver_sql("INSERT INTO items(source_id,type,title,body,created_at,fingerprint,notification_claimed) VALUES('stored:existing','note','existing','retained history','2024-01-01T00:00:00+00:00','fp',1)")
    engine.dispose()
    store=Store(settings)
    assert store.list_items('retained')[0]['source_id']=='stored:existing'
    with store.engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar()=='0007'
        assert {'devices','actions','action_audit','locked_envelopes'} <= set(inspect(conn).get_table_names())
    store.close()


def test_ciphertext_history_is_not_silently_pruned_and_reads_are_bounded(store):
    for i in range(1005):
        store.retain_envelope({"type":"push","push":{"encrypted":True,"ciphertext":f"fake-{i}"}})
    assert store.encrypted_count()==1005
    first=store.encrypted_envelopes()
    assert len(first)==100 and first[0]['id']==1
    assert len(store.encrypted_envelopes(first[-1]['id']))==100


def test_expiry_is_durable_even_when_stale_approval_conflicts(store,monkeypatch):
    action=store.create_action(request(expires_in_seconds=1),"desktop")
    future=utcnow()+timedelta(seconds=2)
    monkeypatch.setattr("pbdesk.storage.workflows.utcnow",lambda:future)
    with pytest.raises(ActionConflict):approve(store,action)
    from sqlalchemy import select
    with store.engine.connect() as conn:
        assert conn.execute(select(actions.c.state).where(actions.c.id==action['id'])).scalar()=='expired'
    assert store.action_audit(action['id'])[-1]['state']=='expired'
