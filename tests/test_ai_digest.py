"""Synthetic local stores, fake KWallet and MockTransport only: no live services."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import service_token
from pbdesk.pushbullet.normalize import normalize
from pbdesk.services.ai import AiService, AiError, DEFAULT_MODEL, load_settings, save_settings
from pbdesk.storage.database import Store


def output(text='Latest useful summary'):
    return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


def note(store, name='one', body='Please review this today', age=0, account='account'):
    store.ingest(normalize({'iden': name, 'type': 'note', 'title': 'Work', 'body': body,
                            'created': time.time() - age}), account=account)


def sms(store, account, device, thread, body, timestamp=None, name='Alice'):
    store.merge_sms_thread(account, device, thread, [dict(message_id='one', direction='incoming', body=body,
        timestamp=timestamp or time.time(), addresses=['+15555550000'], name=name, decrypted=False,
        image_urls=['https://private.invalid/?token=secret'], recipients=[])], body)


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    store = Store(settings)
    store.set_setting('pushbullet_account_id', 'account')
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key', lambda *a: 'sk-fake-test-only')
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_DEBOUNCE', .01)
    monkeypatch.setattr('pbdesk.services.ai.DIGEST_INTERVAL', .08)
    yield settings, store
    store.close()


def ai_for(synthetic, handler):
    settings, store = synthetic
    save_settings(settings, configured=True)
    return AiService(settings, lambda: store, lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))


async def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Synthetic AI work did not settle')
        await asyncio.sleep(.005)


def test_recent_scope_all_conversations_account_partition_bounds(synthetic):
    settings, store = synthetic
    note(store, body='Notification body https://private.invalid/?token=secret')
    note(store, name='old', body='Old notification secret', age=90000)
    note(store, name='other', body='Other account notification', account='other')
    sms(store, 'account', 'phone', 'a', 'First conversation')
    sms(store, 'account', 'phone', 'b', 'Second conversation', name='+15555550000')
    sms(store, 'account', 'phone', 'old', 'Old retained SMS', timestamp=time.time()-90000)
    sms(store, 'other', 'phone', 'a', 'Other account SMS')
    ai = AiService(settings, lambda: store)
    context = ai.recent_context('account', DEFAULT_MODEL)
    assert context['counts'] == {'notifications': 1, 'sms': 2, 'total': 3}
    assert 'First conversation' in context['text'] and 'Second conversation' in context['text']
    assert 'Alice' in context['text'] and 'incoming' in context['text']
    assert all(private not in context['text'] for private in (
        'Old notification secret', 'Other account notification', 'Old retained SMS', 'Other account SMS',
        '+15555550000', 'private.invalid', 'token=secret'))
    same = ai.recent_context('account', DEFAULT_MODEL, now=context['window_end'] + 30)
    assert context['fingerprint'] == same['fingerprint']
    assert context['window_start'] != same['window_start']
    note(store, name='huge', body='x' * 65000)
    bounded = ai.recent_context('account', DEFAULT_MODEL)
    assert len(bounded['text']) <= 24000 and bounded['truncated']
    with pytest.raises(AiError, match='account changed'):
        ai.recent_context('other', DEFAULT_MODEL)


def test_unconfigured_empty_and_historical_only_never_read_key_or_cloud(synthetic, monkeypatch):
    settings, store = synthetic
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key', lambda *a: pytest.fail('wallet read'))
    ai = AiService(settings, lambda: store, lambda **kw: pytest.fail('cloud call'))
    async def run():
        assert (await ai.digest('account'))['status'] == 'unconfigured'
        save_settings(settings, configured=True)
        for i in range(1026):
            sms(store, 'account', 'phone', str(i), 'Historical retained text', timestamp=1700000000+i)
        result = await ai.digest('account')
        assert result['status'] == 'ready'
        assert result['last_good']['counts']['total'] == 0
        assert 'last 24 hours' in result['last_good']['text']
        assert 'Older retained history has not been summarized' in result['last_good']['text']
        assert result['next_refresh_at'] is None
        await ai.close()
    asyncio.run(run())


def test_singleflight_repeated_polls_cache_and_restart(synthetic):
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json=output())
    ai = ai_for(synthetic, handler)
    note(synthetic[1])
    async def run():
        statuses = await asyncio.gather(*(ai.digest('account') for _ in range(20)))
        assert all(value['status'] in ('queued', 'updating', 'ready') for value in statuses)
        state = next(iter(ai._digests.values()))
        await wait_for(lambda: state.status == 'ready')
        result = await ai.digest('account')
        for _ in range(20):
            assert (await ai.digest('account', force=True))['last_good'] == result['last_good']
        assert len(calls) == 1
        payload = json.loads(calls[0].content)
        assert payload['model'] == DEFAULT_MODEL
        assert payload['reasoning'] == {'effort': 'none'}
        assert payload['text'] == {'verbosity': 'low'}
        assert payload['store'] is False and payload['max_output_tokens'] == 1000
        assert len(payload['input']) <= 24000 and 'tools' not in payload
        assert str(calls[0].url) == 'https://api.openai.com/v1/responses'
        cache_path = synthetic[0].state_dir / 'ai-runtime-state.json'
        assert not cache_path.exists()
        persisted = synthetic[1].get_setting('ai_runtime:ai-runtime-state.json')
        assert isinstance(persisted, dict) and 'last_attempt' in persisted
        assert 'Please review this today' not in json.dumps(persisted)
        await ai.close()
        restored = AiService(synthetic[0], lambda: synthetic[1], ai.client_factory)
        restarted_polls = await asyncio.gather(*(restored.digest('account') for _ in range(20)))
        after_restart = restarted_polls[-1]
        assert after_restart['status'] == 'ready'
        assert after_restart['last_good']['text'] == result['last_good']['text']
        assert after_restart['last_good']['history_entry_id'] == result['last_good']['history_entry_id']
        assert after_restart['last_good']['reuse_count'] == result['last_good']['reuse_count'] + 1
        assert len(calls) == 1
        await restored.close()
    asyncio.run(run())


def test_inflight_changes_coalesce_and_respect_paid_cadence(synthetic):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls, times = [], []
        async def handler(request):
            calls.append(json.loads(request.content))
            times.append(time.monotonic())
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return httpx.Response(200, json=output(f'Summary {len(calls)}'))
        ai = ai_for(synthetic, handler)
        note(synthetic[1], body='Original request')
        await ai.digest('account')
        await entered.wait()
        assert (await ai.digest('account'))['status'] == 'updating'
        note(synthetic[1], name='second', body='Second request')
        await ai.digest('account')
        note(synthetic[1], name='third', body='Newest request')
        await ai.digest('account', force=True)
        assert len(calls) == 1
        release.set()
        state = next(iter(ai._digests.values()))
        await wait_for(lambda: len(calls) == 2 and state.status == 'ready')
        assert 'Newest request' in calls[1]['input'] and 'Second request' in calls[1]['input']
        assert times[1] - times[0] >= .075
        assert (await ai.digest('account'))['last_good']['text'] == 'Summary 2'
        await ai.close()
    asyncio.run(run())


def test_bad_key_latch_preserves_good_result_retry_bounded_and_config_change(synthetic):
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=output()) if len(calls) == 1 else httpx.Response(401, text='sk-private')
        ai = ai_for(synthetic, handler)
        note(synthetic[1])
        await ai.digest('account')
        state = next(iter(ai._digests.values()))
        await wait_for(lambda: state.status == 'ready')
        good = state.last_good
        note(synthetic[1], name='changed', body='New text')
        await ai.digest('account')
        await wait_for(lambda: state.latched)
        for _ in range(10):
            result = await ai.digest('account')
            assert result['status'] == 'error' and result['last_good'] == good
        assert len(calls) == 2 and 'sk-private' not in result['error']
        await ai.digest('account', force=True)
        await wait_for(lambda: len(calls) == 3 and state.latched)
        for _ in range(10):
            await ai.digest('account', force=True)
        assert len(calls) == 3
        await ai.close()
        restored = AiService(synthetic[0], lambda: synthetic[1], ai.client_factory)
        assert (await restored.digest('account'))['status'] == 'error'
        await asyncio.sleep(.1)
        assert len(calls) == 3
        save_settings(synthetic[0], 'another-custom-model')
        await restored.digest('account')
        await wait_for(lambda: len(calls) == 4)
        request = json.loads(calls[-1].content)
        assert request['model'] == 'another-custom-model'
        assert 'reasoning' not in request and 'text' not in request
        await restored.close()
    asyncio.run(run())


def test_rate_limit_backoff_and_close_cancel_pending(synthetic):
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(429, text='secret-body')
        ai = ai_for(synthetic, handler)
        note(synthetic[1])
        await ai.digest('account')
        state = next(iter(ai._digests.values()))
        await wait_for(lambda: state.status == 'error')
        assert not state.latched and state.retry_at > time.monotonic()
        for _ in range(5):
            await ai.digest('account', force=True)
        assert len(calls) == 1
        task = state.task
        await ai.close()
        assert task.done()
        await asyncio.sleep(.1)
        assert len(calls) == 1
    asyncio.run(run())


def test_temporal_expiry_and_account_change_during_generation(synthetic):
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            synthetic[1].set_setting('pushbullet_account_id', 'other')
            return httpx.Response(200, json=output('Must not publish'))
        ai = ai_for(synthetic, handler)
        note(synthetic[1])
        context = ai.recent_context('account', DEFAULT_MODEL)
        expired = ai.recent_context('account', DEFAULT_MODEL, now=context['window_end']+86401)
        assert expired['counts']['total'] == 0 and context['fingerprint'] != expired['fingerprint']
        await ai.digest('account')
        state = next(iter(ai._digests.values()))
        await wait_for(lambda: state.task.done())
        assert state.last_good is None
        other = await ai.digest('other')
        assert other['status'] == 'ready' and other['last_good']['counts']['total'] == 0
        await ai.close()
    asyncio.run(run())


def test_models_explicit_catalog_filters_sanitizes_preserves_selection(synthetic, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'data': [
            {'id': 'gpt-6-luna'}, {'id': 'gpt-4.1'}, {'id': 'o3'}, {'id': 'o4-mini'},
            {'id': 'gpt-audio'}, {'id': 'gpt-image-1'}, {'id': 'gpt-realtime'}, {'id': 'text-embedding-3-large'},
            {'id': 'https://evil.invalid/model'}, {'id': 'gpt-bad\nname'}, {'id': '<script>'}, {}, 'bad']})
    ai = ai_for(synthetic, handler)
    save_settings(synthetic[0], 'custom-text-model')
    async def run():
        assert load_settings(synthetic[0])['model'] == 'custom-text-model' and not calls
        result = await ai.models()
        assert result['models'] == ['custom-text-model', 'gpt-4.1', 'gpt-6-luna', 'o3', 'o4-mini']
        assert result['selected'] == 'custom-text-model' and result['default'] == DEFAULT_MODEL
        assert 'do not guarantee' in result['notice']
        assert len(calls) == 1 and calls[0].method == 'GET'
        assert str(calls[0].url) == 'https://api.openai.com/v1/models'
        assert calls[0].headers['authorization'] == 'Bearer sk-fake-test-only'
        await ai.close()
    asyncio.run(run())


@pytest.mark.parametrize('status', [302, 401, 429, 500])
def test_models_errors_redacted(synthetic, status):
    ai = ai_for(synthetic, lambda request: httpx.Response(status, text='sk-secret https://secret.invalid'))
    async def run():
        with pytest.raises(AiError) as exc:
            await ai.models()
        assert 'sk-secret' not in str(exc.value) and 'secret.invalid' not in str(exc.value)
        await ai.close()
    asyncio.run(run())


def test_new_routes_auth_and_settings_remain_local(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    monkeypatch.setattr('pbdesk.services.kwallet.read_password', lambda *a: pytest.fail('wallet read'))
    with TestClient(create_app(settings, demo=True)) as client:
        for role in (None, 'web', 'mcp'):
            headers = {} if role is None else {'Authorization': 'Bearer '+service_token(settings, role)}
            expected = 401 if role is None else 403
            assert client.get('/api/v1/ai/models', headers=headers).status_code == expected
            assert client.post('/api/v1/ai/digest', headers=headers, json={'account_id': None}).status_code == expected
        headers = {'Authorization': 'Bearer '+service_token(settings, 'desktop')}
        for _ in range(5):
            assert client.get('/api/v1/ai/settings', headers=headers).status_code == 200
            result = client.post('/api/v1/ai/digest', headers=headers, json={'account_id': None})
            assert result.status_code == 200 and result.json()['status'] == 'unconfigured'
        assert client.get('/api/v1/ai/models', headers=headers).status_code == 409
        assert client.post('/api/v1/ai/digest', headers=headers, json={'account_id': None, 'force': 'yes'}).status_code == 422
        assert client.post('/api/v1/ai/digest', headers=headers, json={'account_id': None, 'scope': 'all'}).status_code == 422


def test_expiring_excluded_records_does_not_invalidate_identical_input(synthetic):
    settings, store = synthetic
    anchor = time.time()
    for i in range(101):
        store.ingest(normalize({'iden': f'item-{i}', 'type': 'note', 'title': 'Notice', 'body': str(i),
                                'created': anchor-86400+10+i}), account='account')
    ai = AiService(settings, lambda: store)
    before = ai.recent_context('account', DEFAULT_MODEL, now=anchor)
    after = ai.recent_context('account', DEFAULT_MODEL, now=anchor+10.5)
    assert before['truncated'] and not after['truncated']
    assert before['text'] == after['text']
    assert before['fingerprint'] == after['fingerprint']
