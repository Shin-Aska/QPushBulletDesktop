"""The permanent-history protocol is exercised with fake phones, never real SMS."""
import asyncio
import json
import stat
import pytest
import httpx
from pbdesk.config import Settings
from pbdesk.storage.database import Store
from pbdesk.pushbullet.provider import Provider, RateLimited, SubmissionError
from pbdesk.pushbullet.normalize import DEMO, normalize
from pbdesk.pushbullet.crypto import EncryptionService
from test_crypto_pushbullet import KEY,encrypt_object

PHONE=dict(id='phone',nickname='Test phone',type='phone',manufacturer=None,model='Test',active=True,can_push=True,can_sms=True)
THREAD=dict(id='conversation',recipients=[{'address':'+15550000001','name':'Fictional'}],latest={'id':'two','timestamp':1700000001})
ROWS=[dict(id='same',direction='incoming',body='fixture received',timestamp=1700000000),
      dict(id='same',direction='outgoing',body='fixture sent',timestamp=1700000001)]


def setup_provider(tmp_path,handler):
    store=Store(Settings(tmp_path))
    store.save_devices([PHONE]);store.set_setting('pushbullet_account_id','account')
    async def ingest(value): pass
    provider=Provider('fake-token',store,ingest,settings=Settings(tmp_path))
    original=provider.client
    provider.client=httpx.AsyncClient(base_url='https://api.pushbullet.com/v2',transport=httpx.MockTransport(handler))
    provider.account_id='account';provider.sms.pace=0
    return store,provider,original


def test_permanent_history_incremental_identity_and_failure_retention(tmp_path):
    async def run():
        requests=[];fail=False;changed=False
        def handler(request):
            requests.append(request)
            if fail:
                return httpx.Response(503)
            if request.url.path.endswith('_threads'):
                thread={**THREAD,'latest':{'id':'three' if changed else 'two','timestamp':1700000002 if changed else 1700000001}}
                return httpx.Response(200,json={'threads':[thread]})
            return httpx.Response(200,json={'thread':ROWS+([dict(id='three',direction='incoming',body='new fixture',timestamp=1700000002)] if changed else [])})
        store,provider,original=setup_provider(tmp_path,handler)
        await original.aclose()
        assert await provider.sms.sync_device('phone')
        snapshot=store.sms_snapshot('phone')
        assert len(snapshot['messages'])==2
        assert {m['direction'] for m in snapshot['messages']}=={'incoming','outgoing'}
        assert all(m['timestamp']<100000000000 for m in snapshot['messages'])
        assert snapshot['sync']['last_success_at']
        assert len(store.sms_snapshot('phone','outgoing')['messages'])==1
        assert await provider.sms.sync_device('phone')
        assert len(requests)==3  # unchanged conversation index skips the thread
        changed=True
        assert await provider.sms.sync_device('phone')
        last=store.sms_snapshot('phone')['sync']['last_success_at']
        fail=True
        assert not await provider.sms.sync_device('phone')
        snapshot=store.sms_snapshot('phone')
        assert len(snapshot['messages'])==3 and snapshot['sync']['last_success_at']==last
        assert snapshot['sync']['state']=='error'
        assert all(r.method=='GET' and '/permanents/' in r.url.path for r in requests)
        store.set_setting('pushbullet_account_id','another-account')
        assert not store.sms_snapshot('phone')['messages']
        store.set_setting('pushbullet_account_id','account')
        store.deactivate_device('phone')
        assert len(store.sms_snapshot('phone')['messages'])==3
        await provider.close();store.close()
        reopened=Store(Settings(tmp_path))
        assert len(reopened.sms_snapshot('phone')['messages'])==3
        assert reopened.sms_snapshot('phone')['sync']['last_success_at']==last
        reopened.close()
    asyncio.run(run())


def test_encrypted_permanents_recover_wrong_key_and_validate_association(tmp_path):
    async def run():
        def handler(request):
            data={'threads':[THREAD]} if request.url.path.endswith('_threads') else {'thread':ROWS}
            return httpx.Response(200,json={'encrypted':True,'ciphertext':encrypt_object(data)})
        store,provider,original=setup_provider(tmp_path,handler);await original.aclose()
        assert not await provider.sms.sync_device('phone')
        assert len(store.sms_envelopes('account','phone'))==1
        assert store.sms_snapshot('phone')['sync']['state']=='locked'
        provider.device_keys={'phone':b'x'*32}
        assert not await provider.sms.sync_device('phone')
        provider.device_keys={'phone':KEY}
        assert await provider.sms.sync_device('phone')
        assert not store.sms_envelopes('account','phone')
        assert provider.key_states['phone']=='verified'
        assert all(row['decrypted'] for row in store.sms_snapshot('phone')['messages'])
        bad={'encrypted':True,'ciphertext':encrypt_object({'source_device_iden':'different','thread':ROWS})}
        with pytest.raises(ValueError,match='association'):
            provider.sms.decode('account','phone',bad)
        await provider.close();store.close()
    asyncio.run(run())


def test_encrypted_hidden_source_tries_device_keys_and_control_has_no_inbox_row(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));controls=[]
        async def consumer(value):
            if value['push']['type']=='sms_changed':
                controls.append(value);return None
            return store.ingest(normalize(value))
        service=EncryptionService(store,consumer)
        states={'phone':'key_ready','other':'key_ready'}
        await service.set_device_keys({'other':b'x'*32,'phone':KEY},states)
        envelope={'type':'push','push':{'encrypted':True,'ciphertext':encrypt_object({'type':'sms_changed','source_device_iden':'phone'})}}
        await service.ingest(envelope)
        assert len(controls)==1 and not store.list_items() and not store.encrypted_count()
        assert states['phone']=='verified'
        mismatch={'type':'push','push':{'encrypted':True,'ciphertext':encrypt_object({'type':'sms_changed','source_device_iden':'unassociated'})}}
        await service.ingest(mismatch)
        assert store.encrypted_count()==1 and len(controls)==1
        store.close()
    asyncio.run(run())


def test_sqlite_application_path_explicit_migration_owner_lock_and_permissions(tmp_path):
    import sqlite3
    from test_history_security import plaintext
    from pbdesk.services.history_security import HistorySecurity
    state=tmp_path/'state';application=tmp_path/'application'
    plaintext(state/'pbdesk.sqlite3')
    item={**normalize(DEMO), 'fingerprint':'fixture', 'notification_claimed':0, 'account_id':''}
    with sqlite3.connect(state/'pbdesk.sqlite3') as conn:
        columns=','.join('"'+key+'"' for key in item)
        conn.execute(f'INSERT INTO items({columns}) VALUES ({",".join("?" for _ in item)})',tuple(item.values()))
    migrated=HistorySecurity(Settings(state,application_dir=application)).migrate()
    assert migrated.path==application/'pbdesk.sqlite3'
    assert migrated.list_items()[0]['source_id']=='stored:'+DEMO['iden']
    assert not (state/'pbdesk.sqlite3').exists()
    assert list(application.glob('*.archive'))
    assert stat.S_IMODE(migrated.path.stat().st_mode)==0o600
    with pytest.raises(RuntimeError,match='already owns'):
        Store(Settings(tmp_path/'other-state',application_dir=application))
    migrated.close()
    reopened=Store(Settings(state,application_dir=application));reopened.close()
    plaintext(state/'pbdesk.sqlite3')
    with pytest.raises(RuntimeError,match='explicit verified migration'):
        Store(Settings(state,application_dir=application))
    assert (application/'pbdesk.sqlite3').exists() and (state/'pbdesk.sqlite3').exists()


def test_sms_key_references_are_account_partitioned(tmp_path):
    store=Store(Settings(tmp_path));store.set_setting('pushbullet_account_id','one')
    store.set_device_encryption('phone','device','phone-e2e')
    store.set_setting('pushbullet_account_id','two')
    assert store.device_encryption('phone')=={'mode':'shared','entry':'e2e-password'}
    store.set_setting('pushbullet_account_id','one')
    assert store.device_encryption('phone')['entry']=='phone-e2e'
    store.close()


def test_ambiguous_registration_is_not_retried_and_explicit_recovery(tmp_path):
    async def run():
        calls=[]
        def handler(request):
            calls.append(request)
            if request.method=='POST':
                raise httpx.ReadError('fake failure',request=request)
            return httpx.Response(200,json={'devices':[dict(iden='linux',nickname='Exact Linux',model='pbdesk Linux',active=True)]})
        store,provider,original=setup_provider(tmp_path,handler);await original.aclose()
        with pytest.raises(SubmissionError) as result:
            await provider.register_device('Exact Linux')
        assert result.value.state=='uncertain'
        with pytest.raises(SubmissionError):
            await provider.register_device('Exact Linux')
        assert len(calls)==1
        with pytest.raises(ValueError,match='Refresh'):
            await provider.resolve_registration('linux',True)
        await provider.refresh_devices()
        await provider.resolve_registration('linux',True)
        assert store.get_setting('current_device_id')=='linux'
        assert store.get_setting('device_registration_pending') is None
        assert len([c for c in calls if c.method=='POST'])==1
        await provider.close();store.close()
    asyncio.run(run())


def test_history_rate_limit_honors_retry_after(tmp_path):
    async def run():
        store,provider,original=setup_provider(tmp_path,lambda request:httpx.Response(429,headers={'Retry-After':'123'}))
        await original.aclose()
        with pytest.raises(RateLimited) as error:
            await provider.sms.sync_device('phone')
        assert error.value.delay==123
        assert store.sms_snapshot('phone')['sync']['state']=='rate_limited'
        await provider.close();store.close()
    asyncio.run(run())


def test_retained_history_can_recover_offline_without_changing_last_success(tmp_path):
    async def run():
        store,provider,original=setup_provider(tmp_path,lambda request:pytest.fail('Offline replay must never call provider'))
        await original.aclose()
        store.retain_sms_envelope('account','phone','phone_threads',{'encrypted':True,'ciphertext':encrypt_object({'threads':[THREAD]})})
        store.retain_sms_envelope('account','phone','phone_thread_conversation',{'encrypted':True,'ciphertext':encrypt_object({'thread':ROWS})})
        provider.device_keys={'phone':KEY}
        await provider.sms.replay_cached('phone')
        snapshot=store.sms_snapshot('phone')
        assert len(snapshot['messages'])==2 and snapshot['sync']['last_success_at'] is None
        assert provider.key_states['phone']=='verified'
        await provider.close();store.close()
    asyncio.run(run())


def test_settings_paths_are_explicit_and_python_directory_is_never_inferred(tmp_path,monkeypatch):
    from pbdesk.config import application_directory
    import sys
    settings=Settings(tmp_path)
    assert settings.application_dir==tmp_path.resolve()
    monkeypatch.setenv('PBDESK_APPLICATION_DIR',str(tmp_path/'launcher'))
    monkeypatch.setenv('PBDESK_STATE_DIR',str(tmp_path/'discovery'))
    assert Settings.from_env().application_dir==tmp_path/'launcher'
    monkeypatch.delenv('PBDESK_APPLICATION_DIR')
    monkeypatch.setattr(sys,'argv',['/usr/lib/python3.14/runpy.py'])
    assert application_directory()!=__import__('pathlib').Path(sys.executable).parent


def test_device_crud_uses_exact_ids_retains_history_and_registers_only_linux(tmp_path):
    async def run():
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'iden':'registered-linux'} if request.url.path.endswith('/devices') else {})
        store,provider,original=setup_provider(tmp_path,handler);await original.aclose()
        store.merge_sms_thread('account','phone','conversation',provider.sms.normalize_thread(THREAD,ROWS,False),'fixture')
        registered=await provider.register_device('My Linux')
        assert registered=='registered-linux'
        row=next(d for d in store.device_list() if d['id']==registered)
        assert row['model']=='pbdesk Linux' and row['can_sms'] is False
        payload=json.loads(requests[0].content)
        assert payload=={'nickname':'My Linux','model':'pbdesk Linux','manufacturer':'pbdesk','icon':'desktop'}
        await provider.rename_device('phone','Renamed phone')
        await provider.remove_device('phone')
        assert [(r.method,r.url.path) for r in requests]==[('POST','/v2/devices'),('POST','/v2/devices/phone'),('DELETE','/v2/devices/phone')]
        assert len(store.sms_snapshot('phone')['messages'])==2
        assert next(d for d in store.device_list() if d['id']=='phone')['active'] is False
        assert store.device_encryption('phone')['entry']=='e2e-password'
        await provider.close();store.close()
    asyncio.run(run())


def test_account_switch_quarantines_all_legacy_data_and_restores_it(tmp_path):
    from uuid import uuid4
    from pbdesk.domain.actions import ActionRequest,Approval
    store=Store(Settings(tmp_path))
    store.set_setting('pushbullet_account_id','account-A');store.save_devices([PHONE])
    store.ingest(normalize(DEMO))
    request_id=uuid4()
    action=store.create_action(ActionRequest(client_request_id=request_id,kind='note',device_id='phone',body='A content',transport='provider_plaintext'),'desktop')
    store.approve_action(action['id'],Approval(revision=action['revision'],fingerprint=action['fingerprint'],allow_provider_plaintext=True),'desktop')
    cipher={'type':'push','push':{'encrypted':True,'ciphertext':'retained-fake-cipher'}}
    envelope=store.retain_envelope(cipher)
    store.set_setting('current_device_id','linux-A')
    store.set_setting('device_registration_pending',{'nickname':'A Linux','state':'uncertain','at':1})
    store.set_setting('pushbullet_account_id','account-B')
    assert not store.device_list() and not store.list_items() and not store.action_list()
    assert not store.ready_actions() and store.action(action['id']) is None and not store.action_audit(action['id'])
    assert store.item(1) is None and not store.events_after(0)
    assert store.encrypted_count()==0 and not store.encrypted_envelopes()
    assert store.get_setting('current_device_id') is None and store.get_setting('device_registration_pending') is None
    store.envelope_status(envelope,'must-not-touch-A');store.delete_envelope(envelope)
    # Same provider identities / caller UUID in another account remain independent.
    store.save_devices([PHONE]);store.ingest(normalize(DEMO));store.retain_envelope(cipher)
    other=store.create_action(ActionRequest(client_request_id=request_id,kind='note',device_id='phone',body='B content',transport='provider_plaintext'),'desktop')
    assert other['id']!=action['id'] and store.list_items()[0]['source_id']=='stored:'+DEMO['iden']
    store.set_setting('pushbullet_account_id','account-A')
    assert len(store.device_list())==1 and len(store.list_items())==1 and len(store.action_list())==1
    assert store.ready_actions()==[action['id']] and store.action(other['id']) is None
    assert store.encrypted_count()==1 and store.encrypted_envelopes()[0]['status']=='locked'
    assert store.get_setting('current_device_id')=='linux-A'
    assert store.get_setting('device_registration_pending')['nickname']=='A Linux'
    store.close()
    reopened=Store(Settings(tmp_path))
    assert reopened.account_key()=='account-A' and len(reopened.list_items())==1
    reopened.close()


def test_timestamp_normalization_rejects_nonrepresentable_dates():
    for value in (1e300,float('inf'),float('nan'),True,-1):
        with pytest.raises(ValueError,match='timestamp'):
            __import__('pbdesk.pushbullet.sms',fromlist=['SmsHistory']).SmsHistory.normalize_thread(THREAD,[dict(ROWS[0],timestamp=value)],False)


def test_provider_does_not_publish_account_before_durable_switch(tmp_path,monkeypatch):
    async def run():
        store,provider,original=setup_provider(tmp_path,lambda request:httpx.Response(200,json={'iden':'account-B'}))
        await original.aclose();provider.account_id=None
        real=store.set_setting;failed=False
        def set_setting(key,value):
            nonlocal failed
            if key=='pushbullet_account_id' and not failed:
                failed=True;raise RuntimeError('fake disk interruption')
            return real(key,value)
        monkeypatch.setattr(store,'set_setting',set_setting)
        with pytest.raises(RuntimeError,match='interruption'):
            await provider.ensure_account()
        assert provider.account_id is None and store.sms_account()=='account'
        assert await provider.ensure_account()=='account-B'
        assert store.sms_account()=='account-B'
        await provider.close();store.close()
    asyncio.run(run())


def test_inflight_old_account_device_response_cannot_populate_new_partition(tmp_path):
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        async def handler(request):
            entered.set();await release.wait()
            return httpx.Response(200,json={'devices':[dict(iden='A-only',nickname='A device',active=True)]})
        store,provider,original=setup_provider(tmp_path,handler);await original.aclose()
        task=asyncio.create_task(provider.refresh_devices())
        await entered.wait()
        store.set_setting('pushbullet_account_id','account-B')
        release.set()
        with pytest.raises(ValueError,match='Account changed'):
            await task
        snapshot=store.devices_snapshot()
        assert snapshot['account_id']=='account-B' and not snapshot['devices']
        store.set_setting('pushbullet_account_id','account')
        assert [row['id'] for row in store.device_list()]==['phone']
        await provider.close();store.close()
    asyncio.run(run())


def test_management_initializes_account_before_registration(tmp_path):
    async def run():
        calls=[]
        def handler(request):
            calls.append((request.method,request.url.path))
            if request.url.path.endswith('/users/me'):
                return httpx.Response(200,json={'iden':'account-B'})
            assert store.sms_account()=='account-B'
            return httpx.Response(200,json={'iden':'new-linux'})
        store,provider,original=setup_provider(tmp_path,handler);await original.aclose();provider.account_id=None
        await provider.register_device('Fixture Linux')
        assert calls==[('GET','/v2/users/me'),('POST','/v2/devices')]
        assert store.devices_snapshot()['account_id']=='account-B'
        assert [d['id'] for d in store.device_list()]==['new-linux']
        await provider.close();store.close()
    asyncio.run(run())


def test_stale_index_404_continues_preserves_cache_and_recovers_next_pass(tmp_path):
    async def run():
        requests=[];recover=False;network_failure=False
        old_missing=dict(THREAD,id='missing',latest={'id':'new-latest','timestamp':1700000003})
        later=dict(THREAD,id='later',latest={'id':'later-id','timestamp':1700000004})
        def handler(request):
            requests.append(request.url.path)
            if request.url.path.endswith('_threads'):
                return httpx.Response(200,json={'threads':[THREAD,old_missing,later]})
            if request.url.path.endswith('_thread_missing') and not recover:
                return httpx.Response(404,json={'error_code':'not_found'})
            if request.url.path.endswith('_thread_later') and network_failure:
                return httpx.Response(503)
            return httpx.Response(200,json={'thread':ROWS})
        store,provider,original=setup_provider(tmp_path,handler);await original.aclose()
        store.merge_sms_thread('account','phone','missing',provider.sms.normalize_thread(old_missing,[dict(ROWS[0],id='older-cached')],False),'older-fingerprint')
        assert await provider.sms.sync_device('phone')
        snapshot=store.sms_snapshot('phone');last=snapshot['sync']['last_success_at']
        assert snapshot['sync']['state']=='partial' and last and snapshot['sync']['unavailable_count']==1
        assert 'currently unavailable' in snapshot['sync']['error']
        assert any(row['message_id']=='older-cached' for row in snapshot['messages'])
        assert any(row['conversation_id']=='later' for row in snapshot['messages'])
        assert store.sms_thread_fingerprint('account','phone','missing')=='older-fingerprint'
        assert requests[-1].endswith('_thread_later')
        # Unavailable metadata is partitioned, and the cache survives restart.
        store.set_setting('pushbullet_account_id','other-account')
        assert store.sms_snapshot('phone')['sync']['unavailable_count']==0
        store.set_setting('pushbullet_account_id','account')
        assert store.sms_snapshot('phone')['sync']['unavailable_count']==1
        recover=True
        assert await provider.sms.sync_device('phone')
        snapshot=store.sms_snapshot('phone')
        assert snapshot['sync']['state']=='idle' and snapshot['sync']['unavailable_count']==0 and snapshot['sync']['error'] is None
        assert snapshot['sync']['last_success_at']>=last
        assert len([p for p in requests if p.endswith('_thread_missing')])==2
        assert any(row['message_id']=='older-cached' for row in snapshot['messages'])
        # A non-404 failure still fails the pass and keeps its previous success.
        store.merge_sms_thread('account','phone','later',[], 'force-refresh')
        last=snapshot['sync']['last_success_at'];network_failure=True
        assert not await provider.sms.sync_device('phone')
        snapshot=store.sms_snapshot('phone')
        assert snapshot['sync']['state']=='error' and snapshot['sync']['last_success_at']==last
        await provider.close();store.close()
    asyncio.run(run())


def test_previously_missing_thread_is_revisited_even_when_index_fingerprint_rolls_back(tmp_path):
    async def run():
        original_thread=dict(THREAD,id='missing',latest={'id':'older','timestamp':1700000001})
        new_thread=dict(original_thread,latest={'id':'newer','timestamp':1700000002})
        rolled_back=False;recovered=False;thread_reads=0
        def handler(request):
            nonlocal thread_reads
            if request.url.path.endswith('_threads'):
                return httpx.Response(200,json={'threads':[original_thread if rolled_back else new_thread]})
            thread_reads+=1
            return httpx.Response(200,json={'thread':ROWS}) if recovered else httpx.Response(404)
        store,provider,original=setup_provider(tmp_path,handler);await original.aclose()
        import hashlib
        fingerprint=hashlib.sha256(json.dumps(original_thread,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        store.merge_sms_thread('account','phone','missing',provider.sms.normalize_thread(original_thread,ROWS,False),fingerprint)
        assert await provider.sms.sync_device('phone')
        assert store.sms_snapshot('phone')['sync']['unavailable_count']==1
        rolled_back=True
        assert await provider.sms.sync_device('phone')
        assert thread_reads==2 and store.sms_snapshot('phone')['sync']['state']=='partial'
        recovered=True
        assert await provider.sms.sync_device('phone')
        assert thread_reads==3 and store.sms_snapshot('phone')['sync']['unavailable_count']==0
        await provider.close();store.close()
    asyncio.run(run())
