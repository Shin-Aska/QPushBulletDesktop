import asyncio
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import service_token, device_e2e_password, create_device_e2e_password, use_kwallet
from pbdesk.services.ai import AiService, AiError, load_settings, save_settings, configure_key, AI_ENTRY
from pbdesk.storage.database import Store
from pbdesk.pushbullet.normalize import normalize, DEMO


def auth(settings, role='desktop'):
    return {'Authorization': 'Bearer ' + service_token(settings, role)}


@pytest.fixture
def local(tmp_path):
    settings = Settings(tmp_path)
    store = Store(settings)
    store.ingest(normalize(DEMO))
    yield settings, store
    store.close()


def selection(store):
    return dict(account_id=store.sms_account(), mode='summary', source='notifications', item_ids=[r['id'] for r in store.list_items()])


def response(text='A useful summary'):
    return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


def service(local, monkeypatch, handler):
    settings, store = local
    save_settings(settings, configured=True)
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key', lambda *args: 'sk-fake-only-testing')
    transport = httpx.MockTransport(handler)
    return AiService(settings, lambda: store, lambda **kw: httpx.AsyncClient(transport=transport, **kw))


def test_settings_and_prepare_never_open_wallet(local, monkeypatch):
    settings, store = local
    def forbidden(*args, **kwargs):
        pytest.fail('No wallet or cloud in local preview/settings')
    monkeypatch.setattr('pbdesk.services.kwallet.read_password', forbidden)
    monkeypatch.setattr('pbdesk.services.kwallet.write_new_password', forbidden)
    ai = AiService(settings, lambda: store, forbidden)
    assert not load_settings(settings)['configured']
    preview = ai.prepare(selection(store))
    assert preview['count'] == 1
    assert preview['context_id']
    assert preview['text']
    assert preview['scope'] == 'Selected retained notifications'
    save_settings(settings, 'gpt-6-luna')
    assert (settings.state_dir / 'ai-settings.json').stat().st_mode & 0o777 == 0o600


def test_api_auth_and_capabilities(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    monkeypatch.setattr('pbdesk.services.kwallet.read_password', lambda *a: pytest.fail('wallet'))
    with TestClient(create_app(settings, demo=True)) as client:
        assert client.get('/api/v1/ai/settings').status_code == 401
        for role in ('web', 'mcp'):
            assert client.get('/api/v1/ai/settings', headers=auth(settings, role)).status_code == 403
            assert client.post('/api/v1/ai/prepare', headers=auth(settings, role), json={}).status_code == 403
        assert client.get('/api/v1/ai/settings', headers=auth(settings)).json()['model'] == 'gpt-6-luna'
        caps = client.get('/api/v1/capabilities', headers=auth(settings)).json()
        assert caps['features']['ai'] and caps['features']['ai_summary'] and caps['features']['ai_draft_reply']
        items = client.get('/api/v1/items', headers=auth(settings)).json()['items']
        value = dict(account_id=caps['account_id'], mode='summary', source='notifications', item_ids=[items[0]['id']])
        assert client.post('/api/v1/ai/prepare', headers=auth(settings), json=value).status_code == 200
        assert client.post('/api/v1/ai/settings', headers=auth(settings), json={'model': 'https://evil', 'endpoint': 'https://evil'}).status_code == 422


def test_exact_response_request(local, monkeypatch):
    captured = []
    def handle(request):
        captured.append(request)
        return httpx.Response(200, json=response())
    ai = service(local, monkeypatch, handle)
    preview = ai.prepare(selection(local[1]))
    result = asyncio.run(ai.generate(preview['context_id']))
    assert result['text'] == 'A useful summary'
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == 'https://api.openai.com/v1/responses'
    assert request.headers['authorization'] == 'Bearer sk-fake-only-testing'
    payload = json.loads(request.content)
    assert payload['input'] == preview['text']
    assert payload['store'] is False
    assert payload['model'] == 'gpt-6-luna'
    assert not {'tools', 'previous_response_id', 'conversation'} & payload.keys()
    with pytest.raises(AiError, match='expired'):
        asyncio.run(ai.generate(preview['context_id']))


@pytest.mark.parametrize('body', [response(''), {}, {'status': 'incomplete', 'output': []},
    {'status': 'completed', 'output': [{'type': 'function_call'}]},
    {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'secret'}]}]},
    {'status': 'completed', 'output': [{'type': 'message', 'content': 'bad'}]}])
def test_unusable_output(local, monkeypatch, body):
    ai = service(local, monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(AiError):
        asyncio.run(ai.generate(ai.prepare(selection(local[1]))['context_id']))


@pytest.mark.parametrize('status', [400, 401, 403, 404, 429, 500, 302])
def test_remote_errors_redacted_no_retry(local, monkeypatch, status):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, text='sk-secret https://signed.example/private?token=secret')
    ai = service(local, monkeypatch, handle)
    with pytest.raises(AiError) as exc:
        asyncio.run(ai.generate(ai.prepare(selection(local[1]))['context_id']))
    assert 'secret' not in str(exc.value) and 'https:' not in str(exc.value)
    assert len(calls) == 1


def test_timeout_sanitized(local, monkeypatch):
    def handle(request):
        raise httpx.ReadTimeout('sk-secret signed url')
    ai = service(local, monkeypatch, handle)
    with pytest.raises(AiError, match='timed out'):
        asyncio.run(ai.generate(ai.prepare(selection(local[1]))['context_id']))


def test_account_change_and_expired_preview_before_key(local, monkeypatch):
    settings, store = local
    ai = AiService(settings, lambda: store)
    preview = ai.prepare(selection(store))
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key', lambda *a: pytest.fail('key read'))
    ai.prepared[preview['context_id']].expires = 0
    with pytest.raises(AiError, match='expired'):
        asyncio.run(ai.generate(preview['context_id']))
    preview = ai.prepare(selection(store))
    store.set_setting('pushbullet_account_id', 'another-account')
    with pytest.raises(AiError, match='account changed'):
        asyncio.run(ai.generate(preview['context_id']))


def test_account_change_during_cloud_discards(local, monkeypatch):
    def handle(request):
        local[1].set_setting('pushbullet_account_id', 'new-account')
        return httpx.Response(200, json=response())
    ai = service(local, monkeypatch, handle)
    with pytest.raises(AiError, match='account changed'):
        asyncio.run(ai.generate(ai.prepare(selection(local[1]))['context_id']))


def test_context_change_and_scope_bounds(local, monkeypatch):
    settings, store = local
    original = store.item
    monkeypatch.setattr(store, 'item', lambda identity: dict(original(identity), body='hello https://signed.host/?secret=123 api-key=SECRET user@example.com ' + 'x' * 40000))
    ai = AiService(settings, lambda: store)
    preview = ai.prepare(selection(store))
    assert preview['truncated'] and len(preview['text']) <= 24000
    assert 'signed.host' not in preview['text'] and 'SECRET' not in preview['text'] and 'user@example.com' not in preview['text']
    monkeypatch.setattr(store, 'item', original)
    with pytest.raises(AiError, match='changed'):
        asyncio.run(ai.generate(preview['context_id']))


def test_wallet_dedicated_and_overwrite_rules(local, monkeypatch):
    settings, store = local
    calls = []
    monkeypatch.setattr('pbdesk.services.kwallet.write_new_password', lambda *a: calls.append(a))
    assert configure_key(settings, store, 'sk-test-fake')['configured']
    assert calls == [('Pushbullet', AI_ENTRY, 'sk-test-fake', 'kdewallet')]
    monkeypatch.setattr('pbdesk.services.kwallet.read_password', lambda *a: calls.append(a) or 'sk-test-fake')
    configure_key(settings, store)
    assert calls[-1] == ('Pushbullet', AI_ENTRY, 'kdewallet')
    for fn, args in [(device_e2e_password, (settings, AI_ENTRY)), (create_device_e2e_password, (settings, AI_ENTRY, 'test')), (use_kwallet, (settings, 'Pushbullet', AI_ENTRY, 'kdewallet'))]:
        with pytest.raises((ValueError, RuntimeError)):
            fn(*args)
    assert len(calls) == 2
    store.set_device_encryption('inactive-device', 'device', AI_ENTRY)
    with pytest.raises(AiError, match='encryption'):
        configure_key(settings, store, 'sk-fake')
    assert len(calls) == 2


def test_sms_scope_directions_latest_bounds_excludes_other_accounts(local):
    settings, store = local
    store.set_setting('pushbullet_account_id', 'acct')
    def rows(label, count):
        return [dict(message_id=str(i), direction='outgoing' if i % 2 else 'incoming', body=f'{label}-{i}', timestamp=1700000000+i,
                     addresses=['+15550000000'], name='Private contact name', decrypted=False,
                     image_urls=['https://signed.example/private?token=secret'], recipients=[{'address': '+15550000000'}]) for i in range(count)]
    store.merge_sms_thread('acct', 'phone', 'thread', rows('selected', 105), 'fingerprint')
    store.merge_sms_thread('acct', 'other-phone', 'thread', rows('other-phone-private', 1), 'other')
    store.merge_sms_thread('acct', 'phone', 'other-thread', rows('other-thread-private', 1), 'other2')
    store.merge_sms_thread('other-account', 'phone', 'thread', rows('other-account-private', 1), 'other3')
    ai = AiService(settings, lambda: store)
    preview = ai.prepare(dict(account_id='acct', mode='draft_reply', source='sms', device_id='phone', conversation_id='thread'))
    assert preview['has_older_messages'] and preview['count'] == 20
    assert len(preview['candidates']) == 20 and len(preview['selected_ids']) == 20
    assert 'selected-104' in preview['text'] and 'selected-0\n' not in preview['text']
    assert 'incoming:' in preview['text'] and 'outgoing:' in preview['text']
    for private in ('Private contact name', '+15550000000', 'signed.example', 'other-phone-private', 'other-thread-private', 'other-account-private'):
        assert private not in preview['text']


def test_busy_generation_and_cancellation_release(local, monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=response())
        ai = service(local, monkeypatch, handler)
        one = ai.prepare(selection(local[1]))
        two = ai.prepare(selection(local[1]))
        task = asyncio.create_task(ai.generate(one['context_id']))
        await entered.wait()
        with pytest.raises(AiError, match='already running'):
            await ai.generate(two['context_id'])
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not ai.busy
        release.set()
        assert (await ai.generate(two['context_id']))['text'] == 'A useful summary'
    asyncio.run(run())


def test_api_explicit_generation_through_router(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    calls = []
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key', lambda *a: 'sk-fake-api-test')
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response())
    with TestClient(create_app(settings, demo=True)) as client:
        client.app.state.ai.client_factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)
        save_settings(settings, configured=True)
        values = client.get('/api/v1/items', headers=auth(settings)).json()
        prepared = client.post('/api/v1/ai/prepare', headers=auth(settings), json=dict(account_id=values['account_id'], source='notifications', mode='summary', item_ids=[values['items'][0]['id']])).json()
        assert not calls
        result = client.post('/api/v1/ai/generate', headers=auth(settings), json={'context_id': prepared['context_id']})
        assert result.status_code == 200 and result.json()['text'] == 'A useful summary'
        assert len(calls) == 1


def test_ai_key_conflicting_provider_reference_never_read(local, monkeypatch):
    from pbdesk.credentials import _save_provider_source, provider_token, CredentialLocked
    settings, store = local
    _save_provider_source(settings, {'backend': 'kwallet', 'wallet': 'kdewallet', 'folder': 'Pushbullet', 'entry': AI_ENTRY})
    monkeypatch.setattr('pbdesk.services.kwallet.read_password', lambda *a: pytest.fail('conflicting key read'))
    with pytest.raises(AiError, match='provider credential'):
        configure_key(settings, store)
    with pytest.raises(CredentialLocked):
        provider_token(settings)
