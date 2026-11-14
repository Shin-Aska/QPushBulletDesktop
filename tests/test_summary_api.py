"""Saved summaries use only temporary SQLite, fake credentials and mock HTTP."""
import asyncio
import json
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import service_token
from pbdesk.pushbullet.normalize import normalize
from pbdesk.services.ai import AiService, DEFAULT_MODEL, PROMPT_VERSION, save_settings
from pbdesk.storage.database import Store


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def config(name='Saved scope', **values):
    return dict(name=name, sources=['notifications', 'sms'], device_ids=[], conversations=[],
                conversation_filter='all', window_hours=24, model_override=None) | values


def add_note(store, identity='one', **values):
    store.ingest(normalize(dict(iden=identity, type='note', title='Work', body='Please review',
                               created=time.time()) | values), account='account')


def generation(repo, scope, at=None):
    return dict(canonical_config_hash=repo.canonical_config_hash(scope), resolved_model=DEFAULT_MODEL,
        prompt_version=PROMPT_VERSION, input_fingerprint='a'*64, markdown='Saved **summary** 100% literal_',
        generated_at=iso(at or time.time()))


def test_routes_crud_account_versions_metadata_and_history_no_cloud(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key', lambda *a: pytest.fail('wallet'))
    with TestClient(create_app(settings, demo=True)) as client:
        store = client.app.state.store
        store.set_setting('pushbullet_account_id', 'account')
        store.merge_sms_thread('account', 'history-phone', 'thread', [dict(message_id='one', direction='incoming',
            body='This body must not occur in options', timestamp=time.time(), addresses=[], name='Alice',
            decrypted=False, image_urls=[], recipients=[])], 'body')
        headers = {'Authorization': 'Bearer '+service_token(settings, 'desktop')}
        def request(method, path, **kw):
            return client.request(method, '/api/v1/ai'+path, headers=headers, **kw)
        result = request('GET', '/presets', params={'account_id':'account'})
        assert result.status_code == 200
        defaults = result.json()['presets']
        assert len(defaults) == 2
        options = request('GET', '/preset-options', params={'account_id':'account'}).json()
        assert options['devices'] == [{'id':'history-phone','nickname':'history-phone'}]
        assert options['conversations'] == [{'device_id':'history-phone','conversation_id':'thread','name':'Alice'}]
        assert 'This body' not in json.dumps(options)
        # History imports independently of digest/configured state; opening it is local only.
        legacy_path=settings.state_dir/'ai-digest-cache.json'
        stamp=time.time()
        legacy={'model':DEFAULT_MODEL,'text':'Old valid cache','generated_at':stamp-1,
            'window_start':stamp-86400,'window_end':stamp-1,'counts':{'notifications':1,'sms':0,'total':1},
            'fingerprint':'b'*64,'truncated':False}
        legacy_path.write_text(json.dumps({'entries':[{'account_id':'account','model':DEFAULT_MODEL,
            'scope':'recent','result':legacy,'valid_until':stamp+60}]}))
        legacy_path.chmod(0o600)
        # New process normally loads the legacy file before its first HTTP read.
        client.app.state.ai._restored=client.app.state.ai._load_digest_cache()
        imported=request('GET','/history',params={'account_id':'account'}).json()['items']
        assert len(imported)==1 and imported[0]['origin']=='legacy'
        assert imported[0]['notification_count'] is None

        assert request('GET', '/history', params={'account_id':'other'}).status_code == 409
        assert request('GET', '/history').status_code == 422
        for role in ('web','mcp'):
            assert client.get('/api/v1/ai/presets', params={'account_id':'account'},
                headers={'Authorization':'Bearer '+service_token(settings,role)}).status_code == 403
        assert request('POST','/presets',json={'account_id':'account','expected_revision':0,
            'config':config(conversation_filter='selected')}).status_code == 422
        assert request('POST','/presets',json={'account_id':'account','expected_revision':0,
            'config':config(device_ids=['foreign'])}).status_code == 422
        preset = request('POST','/presets',json={'account_id':'account','expected_revision':0,
            'config':config(conversation_filter='selected', conversations=[{'device_id':'history-phone','conversation_id':'thread'}])}).json()
        assert preset['revision'] == 1
        import uuid
        assert request('PATCH','/presets/'+str(uuid.uuid4()),json={'account_id':'account',
            'expected_revision':0,'config':config()}).status_code == 404
        old_active = request('GET','/active-preset',params={'account_id':'account'}).json()
        active = request('PUT','/active-preset',json={'account_id':'account','preset_id':preset['id'],
            'expected_revision':old_active['revision']}).json()
        assert active['preset']['id'] == preset['id']
        assert request('PUT','/active-preset',json={'account_id':'account','preset_id':defaults[0]['id'],
            'expected_revision':old_active['revision']}).status_code == 409
        entry = store.summaries.save_generation('account', generation(store.summaries, preset, time.time()-10*86400),
            preset, saved_at=iso(time.time()-10*86400))
        assert request('PATCH','/history/'+entry['id'],json={'account_id':'account','pinned':True}).json()['pinned']
        policy = request('GET','/history-settings',params={'account_id':'account'}).json()
        payload = {'account_id':'account','retention_days':7,'expected_revision':policy['revision'],'preview':True}
        assert request('PUT','/history-settings',json=payload).json()['affected_count'] == 0
        assert request('PUT','/history-settings',json=payload|{'preview':False}).status_code == 422
        applied = request('PUT','/history-settings',json=payload|{'preview':False,'confirmed_retention_days':7})
        assert applied.status_code == 200 and applied.json()['removed_count'] == 0
        assert request('GET','/history',params={'account_id':'account','query':'100% literal_'}).json()['total'] == 1
        assert request('GET','/history/'+entry['id'],params={'account_id':'account'}).json()['markdown'] == entry['markdown']
        assert request('GET','/active-preset',params={'account_id':'account'}).json() == active
        assert request('DELETE','/presets/'+preset['id'],json={'account_id':'account','expected_revision':1}).json()['archived']
        assert request('GET','/history',params={'account_id':'account','preset_id':preset['id']}).json()['total'] == 1


def test_generation_metrics_dedup_clone_and_rapid_activation_capture(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    store = Store(settings)
    store.set_setting('pushbullet_account_id','account')
    add_note(store)
    save_settings(settings, configured=True)
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key', lambda *a:'fake')
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_DEBOUNCE', .001)
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_INTERVAL', .04)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handler(request):
            calls.append(json.loads(request.content))
            entered.set()
            await release.wait()
            return httpx.Response(200,json={'status':'completed','model':'actual-model','usage':{
                'input_tokens':321,'output_tokens':19,'input_tokens_details':{'cached_tokens':128}},
                'output':[{'type':'message','content':[{'type':'output_text','text':'Captured summary'}]}]})
        ai = AiService(settings,lambda:store, lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw))
        original = store.summaries.get_active_preset('account')
        clone = store.summaries.save_preset('account',config('Same scope clone'))
        await ai.digest('account')
        await entered.wait()
        store.summaries.set_active_preset('account',clone['id'],original['revision'])
        await ai.digest('account')
        release.set()
        deadline = time.monotonic()+3
        while store.summaries.list_history('account')['total'] < 2:
            assert time.monotonic()<deadline
            await ai.digest('account')
            await asyncio.sleep(.005)
        history=store.summaries.list_history('account')['items']
        assert len(calls)==1
        assert len({item['generation_id'] for item in history})==1
        assert {item['preset_id'] for item in history}=={original['preset']['id'],clone['id']}
        for entry in history:
            assert entry['input_tokens']==321 and entry['output_tokens']==19 and entry['cached_input_tokens']==128
            assert entry['actual_response_model']=='actual-model' and entry['latency_ms']>=0
        reuse = [(item['id'],item['reuse_count']) for item in history]
        for _ in range(10):
            assert (await ai.digest('account'))['last_good']['text']=='Captured summary'
        assert [(item['id'],item['reuse_count']) for item in store.summaries.list_history('account')['items']]==reuse
        await ai.close()
        assert not ai._threads
        restarted=AiService(settings,lambda:store, lambda **kw:pytest.fail('restarted cloud'))
        assert (await restarted.digest('account'))['status']=='ready'
        await restarted.close()
    try:
        asyncio.run(run())
    finally:
        store.close()


def test_legacy_import_private_idempotent_unknown_metrics_preserves_file(tmp_path):
    settings=Settings(tmp_path)
    store=Store(settings)
    store.set_setting('pushbullet_account_id','account')
    now=time.time()
    result={'model':DEFAULT_MODEL,'text':'Legacy saved output','generated_at':now-100,'window_start':now-86400,
        'window_end':now-100,'counts':{'notifications':2,'sms':3,'total':5},'fingerprint':'f'*64,'truncated':False}
    path=settings.state_dir/'ai-digest-cache.json'
    path.write_text(json.dumps({'entries':[{'account_id':'account','model':DEFAULT_MODEL,'scope':'recent',
        'result':result,'valid_until':now+100}]}))
    path.chmod(0o600)
    before=path.read_bytes()
    async def run():
        for _ in range(2):
            ai=AiService(settings,lambda:store,lambda **kw:pytest.fail('cloud'))
            assert (await ai.digest('account'))['status']=='unconfigured'
            await ai.close()
        items=store.summaries.list_history('account')['items']
        assert len(items)==1 and items[0]['origin']=='legacy'
        assert all(items[0][key] is None for key in ('notification_count','sms_count','input_tokens','latency_ms'))
        assert path.read_bytes()==before
    try:
        asyncio.run(run())
    finally:
        store.close()


def test_close_drains_cancelled_sql_thread_before_store_close(tmp_path):
    store=Store(Settings(tmp_path))
    ai=AiService(store.settings if hasattr(store,'settings') else Settings(tmp_path),lambda:store)
    entered,release=threading.Event(),threading.Event()
    def slow():
        entered.set()
        release.wait(3)
        store.set_setting('drained','yes')
    async def run():
        task=asyncio.create_task(ai.local(slow))
        await asyncio.to_thread(entered.wait,1)
        task.cancel()
        closing=asyncio.create_task(ai.close())
        await asyncio.sleep(.02)
        assert not closing.done()
        release.set()
        await asyncio.gather(task,closing,return_exceptions=True)
        assert store.get_setting('drained')=='yes' and not ai._threads
    try:
        asyncio.run(run())
    finally:
        store.close()


def test_exact_tuple_device_notification_filters_and_fingerprint(tmp_path):
    settings=Settings(tmp_path)
    store=Store(settings)
    store.set_setting('pushbullet_account_id','account')
    anchor=time.time()
    try:
        add_note(store,'associated',source_device_iden='old-notification-phone',body='Retained notice')
        add_note(store,'unassociated',body='Unassociated notice')
        for device,thread,body in [('phone-a','same','Selected SMS'),('phone-b','same','Other phone SMS'),
                ('phone-a','different','Other thread SMS')]:
            store.merge_sms_thread('account',device,thread,[dict(message_id='one',direction='incoming',body=body,
                timestamp=anchor,addresses=[],name='Alice',decrypted=False,image_urls=[],recipients=[])],body)
        ai=AiService(settings,lambda:store)
        scoped=config(sources=['sms'],conversation_filter='selected',
            conversations=[{'device_id':'phone-a','conversation_id':'same'}],window_hours=1)
        ai.validate_scope('account',scoped)
        context=ai.recent_context('account',DEFAULT_MODEL,now=anchor+1,preset=scoped)
        assert context['counts']=={'notifications':0,'sms':1,'total':1}
        assert context['conversation_count']==1 and 'Selected SMS' in context['text']
        assert 'Other phone' not in context['text'] and 'Other thread' not in context['text']
        same=ai.recent_context('account',DEFAULT_MODEL,now=anchor+2,preset=scoped|{'name':'Renamed'})
        assert context['fingerprint']==same['fingerprint']
        changed=ai.recent_context('account',DEFAULT_MODEL,now=anchor+2,preset=scoped|{'window_hours':6})
        assert context['fingerprint']!=changed['fingerprint']
        notification=config(sources=['notifications'],device_ids=['old-notification-phone'])
        ai.validate_scope('account',notification)
        options=ai.preset_options('account')
        assert {'id':'old-notification-phone','nickname':'old-notification-phone'} in options['devices']
        text=ai.recent_context('account',DEFAULT_MODEL,preset=notification)
        assert text['counts']['notifications']==1 and 'Retained notice' in text['text']
        assert 'Unassociated' not in text['text']
    finally:
        store.close()


def test_invalid_key_latch_shared_across_preset_switches_and_restart_cadence(tmp_path, monkeypatch):
    settings=Settings(tmp_path)
    store=Store(settings)
    store.set_setting('pushbullet_account_id','account')
    add_note(store)
    save_settings(settings,configured=True)
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key',lambda *a:'fake')
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_DEBOUNCE',.001)
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_INTERVAL',5)
    calls=[]
    async def handler(request):
        calls.append(request)
        return httpx.Response(401)
    factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw)
    async def run():
        ai=AiService(settings,lambda:store,factory)
        await ai.digest('account')
        deadline=time.monotonic()+3
        while not any(state.latched for state in ai._digests.values()):
            assert time.monotonic()<deadline
            await asyncio.sleep(.005)
        old=store.summaries.get_active_preset('account')
        clone=store.summaries.save_preset('account',config('Clone'))
        store.summaries.set_active_preset('account',clone['id'],old['revision'])
        assert (await ai.digest('account'))['status']=='error'
        await asyncio.sleep(.02)
        assert len(calls)==1
        # A single explicit retry may clear the latch but must retain paid cadence.
        await ai.digest('account',force=True)
        await asyncio.sleep(.02)
        assert len(calls)==1
        await ai.close()
        restored=AiService(settings,lambda:store,factory)
        await restored.digest('account')
        await asyncio.sleep(.02)
        assert len(calls)==1
        await restored.close()
    try:
        asyncio.run(run())
    finally:
        store.close()


def test_restart_restores_original_last_good_while_new_context_is_queued(tmp_path, monkeypatch):
    settings=Settings(tmp_path)
    store=Store(settings)
    store.set_setting('pushbullet_account_id','account')
    add_note(store)
    save_settings(settings,configured=True)
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_DEBOUNCE',5)
    scope=store.summaries.get_active_preset('account')['preset']
    entry=store.summaries.save_generation('account',generation(store.summaries,scope),scope)
    async def run():
        ai=AiService(settings,lambda:store,lambda **kw:pytest.fail('no cloud before debounce'))
        value=await ai.digest('account')
        assert value['status']=='queued'
        assert value['last_good']['text']==entry['markdown']
        assert value['last_good']['history_entry_id']==entry['id']
        assert value['last_good']['generated_at']==datetime.fromisoformat(entry['generated_at']).timestamp()
        assert value['last_good']['reuse_count']==0
        await ai.close()
    try:
        asyncio.run(run())
    finally:
        store.close()


def test_many_inactive_preset_scopes_keep_sql_history_and_bounded_memoization(tmp_path):
    settings=Settings(tmp_path)
    store=Store(settings)
    store.set_setting('pushbullet_account_id','account')
    original=store.summaries.get_active_preset('account')
    entry=store.summaries.save_generation('account',generation(store.summaries,original['preset']),original['preset'])
    async def run():
        ai=AiService(settings,lambda:store,lambda **kw:pytest.fail('unconfigured cloud'))
        active=original
        for index in range(70):
            scope=store.summaries.save_preset('account',config(str(index)))
            active=store.summaries.set_active_preset('account',scope['id'],active['revision'])
            assert (await ai.digest('account'))['status']=='unconfigured'
        assert len(ai._digests)<=64
        assert store.summaries.get_history('account',entry['id'])['markdown']==entry['markdown']
        assert not ai._reuse_events
        await ai.close()
    try:
        asyncio.run(run())
    finally:
        store.close()


def test_active_edit_applies_next_automatic_scope_within_cadence_inactive_edit_is_local(tmp_path, monkeypatch):
    settings=Settings(tmp_path)
    store=Store(settings)
    store.set_setting('pushbullet_account_id','account')
    add_note(store)
    store.merge_sms_thread('account','phone','thread',[dict(message_id='one',direction='incoming',
        body='SMS scope marker',timestamp=time.time(),addresses=[],name='Alice',decrypted=False,
        image_urls=[],recipients=[])],'body')
    save_settings(settings,configured=True)
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key',lambda *a:'fake')
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_DEBOUNCE',.005)
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_INTERVAL',.2)
    calls,times=[],[]
    async def handler(request):
        calls.append(json.loads(request.content))
        times.append(time.monotonic())
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message',
            'content':[{'type':'output_text','text':f'Summary {len(calls)}'}]}]})
    async def settled(ai,expected):
        deadline=time.monotonic()+3
        while True:
            value=await ai.digest('account')
            if len(calls)==expected and value['status']=='ready':
                return value
            assert time.monotonic()<deadline
            await asyncio.sleep(.005)
    async def run():
        ai=AiService(settings,lambda:store,lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw))
        original=store.summaries.get_active_preset('account')
        inactive=store.summaries.save_preset('account',config('Inactive',sources=['sms']))
        initial=await settled(ai,1)
        assert 'SMS scope marker' in calls[0]['input']
        ai.update_preset('account',inactive['id'],config('Edited inactive',sources=['notifications'],window_hours=6),1)
        unchanged=await ai.digest('account')
        assert len(calls)==1 and unchanged['active_preset_id']==original['preset']['id']
        assert unchanged['active_preset_revision']==initial['active_preset_revision']==1
        edited=ai.update_preset('account',original['preset']['id'],
            config('Edited active',sources=['notifications']),1)
        assert len(calls)==1  # Save itself performs only local persistence.
        queued=await ai.digest('account')
        assert queued['status']=='queued' and queued['active_preset_revision']==edited['revision']==2
        assert queued['active_revision']==original['revision']
        assert len(calls)==1
        updated=await settled(ai,2)
        assert times[1]-times[0]>=.195
        assert 'SMS scope marker' not in calls[1]['input']
        assert updated['last_good']['preset_snapshot']['revision']==2
        assert updated['last_good']['preset_snapshot']['sources']==['notifications']
        history=store.summaries.list_history('account')['items']
        assert {entry['preset_revision'] for entry in history}=={1,2}
        assert {entry['preset_id'] for entry in history}=={original['preset']['id']}
        await ai.close()
    try:
        asyncio.run(run())
    finally:
        store.close()
