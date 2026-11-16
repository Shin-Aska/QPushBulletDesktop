"""Synthetic media metadata and credential-free fake HTTP; no external requests."""
import asyncio
import base64
import hashlib
import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
import httpx
import pytest
from sqlalchemy import create_engine

from pbdesk.api.media import MAX_IMAGE_BYTES, MEDIA_HOSTS, fetch_image, install_media_routes, safe_media_url
from pbdesk.config import Settings
from pbdesk.pushbullet.normalize import normalize, MAX_INLINE_IMAGE
from pbdesk.pushbullet.sms import SmsHistory, thread_fingerprint
from pbdesk.storage.database import Store
from pbdesk.services.history_security import HistorySecurity


def test_public_media_metadata_is_bounded_and_whitelisted():
    item = normalize({'type': 'mirror', 'source_device_iden': 'phone', 'package_name': 'fixture',
        'notification_id': 7, 'application_name': 'Fixture App', 'icon': 'a' * 100,
        'file_url': 'https://static.pushbullet.com/fixture.png', 'file_type': 'image/png',
        'file_name': 'fixture.png', 'secret': 'not public'})
    assert item['application_name'] == 'Fixture App' and item['icon'] == 'a' * 100
    assert item['file_name'] == 'fixture.png' and 'secret' not in item
    assert normalize({'type': 'note', 'iden': 'one', 'icon': 'x' * (MAX_INLINE_IMAGE + 1)})['icon'] is None
    thread = {'recipients': [{'address': '+15550000001', 'name': 'Synthetic', 'thumbnail': 'dGVzdA==',
        'image_url': 'https://lh6.googleusercontent.com/a', 'secret': 'no'}]}
    messages = SmsHistory.normalize_thread(thread, [{'id': 1, 'direction': 'incoming', 'body': None,
        'image_urls': ['https://dl.pushbulletusercontent.com/x', 123, 'x' * 4097], 'timestamp': 1700000000}], True)
    assert messages[0]['body'] == ''
    assert messages[0]['image_urls'] == ['https://dl.pushbulletusercontent.com/x']
    assert messages[0]['recipients'] == [{k: v for k, v in thread['recipients'][0].items() if k != 'secret'}]
    assert thread_fingerprint(thread) != hashlib.sha256(json.dumps(thread, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def test_media_migration_retains_identity_history_keys_ciphertext_and_accounts(tmp_path):
    path = tmp_path / 'pbdesk.sqlite3'
    engine = create_engine(f'sqlite:///{path}')
    config = Config()
    config.set_main_option('script_location', str(Path('src/pbdesk/storage/migrations').resolve()))
    with engine.begin() as conn:
        config.attributes['connection'] = conn
        command.upgrade(config, '0005')
        conn.exec_driver_sql("INSERT INTO settings VALUES ('pushbullet_account_id','\"a\"')")
        conn.exec_driver_sql("INSERT INTO items(id,account_id,source_id,type,title,body,created_at,fingerprint,notification_claimed,decrypted) VALUES(41,'a','old','note','old','kept','2024-01-01','fingerprint',1,1)")
        conn.exec_driver_sql("INSERT INTO sms_messages VALUES (52,'a','phone','thread','message','incoming','retained',1700000000,'[\"+15550000001\"]','Synthetic',1)")
        conn.exec_driver_sql("INSERT INTO device_encryption VALUES ('a','phone','device','fixture-key-reference')")
        conn.exec_driver_sql("INSERT INTO sms_envelopes VALUES ('a','phone','phone_threads','{\"encrypted\":true,\"ciphertext\":\"fixture-cipher\"}')")
    engine.dispose()
    security = HistorySecurity(Settings(tmp_path))
    store = security.migrate()
    security.finish_recovery_offer(store, saved=False)
    with sqlite3.connect(path) as plaintext:
        with pytest.raises(sqlite3.DatabaseError):
            plaintext.execute('SELECT * FROM items').fetchall()
    assert store.item(41)['body'] == 'kept' and store.item(41)['icon'] is None
    old = store.sms_snapshot('phone')['messages'][0]
    assert old['id'] == 52 and old['image_urls'] == [] and old['recipients'] == []
    assert store.device_encryption('phone')['entry'] == 'fixture-key-reference'
    assert 'fixture-cipher' in store.sms_envelopes('a', 'phone')[0]['payload']
    store.merge_sms_thread('a', 'phone', 'thread', [dict(message_id='message', direction='incoming', body='retained',
        timestamp=1700000000, addresses=['+15550000001'], name='Synthetic', decrypted=1,
        image_urls=['https://static.pushbullet.com/a.png'], recipients=[{'address': '+15550000001'}])], 'new-version')
    assert store.sms_message(52)['image_urls'] == ['https://static.pushbullet.com/a.png']
    store.set_setting('pushbullet_account_id', 'b')
    assert store.sms_message(52) is None and store.item(41) is None
    store.close()
    store = Store(Settings(tmp_path))
    assert not store.sms_snapshot('phone')['messages']
    store.set_setting('pushbullet_account_id', 'a')
    assert store.sms_snapshot('phone')['messages'][0]['id'] == 52
    store.close()


@pytest.mark.parametrize('url', ['http://static.pushbullet.com/x', 'https://evil.example/x',
    'https://static.pushbullet.com.evil.example/x', 'https://u:p@static.pushbullet.com/x',
    'https://dl3.pushbulletusercontent.com.evil.example/x', 'https://dl4.pushbulletusercontent.com/x',
    'http://dl3.pushbulletusercontent.com/x', 'https://dl3.pushbulletusercontent.com:444/x',
    'https://static.pushbullet.com:444/x', 'https://127.0.0.1/x', 'file:///tmp/x',
    'https://static.pushbullet.com/x#fragment', 'https://static.pushbullet.com/\nx'])
def test_media_url_guards(url):
    assert not safe_media_url(url)


def test_numbered_pushbullet_image_host_is_fetched_without_credentials():
    async def run():
        url = 'https://dl3.pushbulletusercontent.com/synthetic/contact.png'
        requests = []
        def handler(request):
            requests.append(request)
            assert str(request.url) == url
            assert not any(key in request.headers for key in ('authorization', 'access-token', 'cookie'))
            return httpx.Response(200, content=b'synthetic-raster', headers={'content-type': 'image/png'})
        result = await fetch_image(url, httpx.MockTransport(handler))
        assert result['data'] == base64.b64encode(b'synthetic-raster').decode()
        assert len(requests) == 1
    asyncio.run(run())


def test_media_fetch_has_no_credentials_no_redirects_and_bounded_bytes():
    async def run():
        requests = []
        def handler(request):
            requests.append(request)
            assert not any(key in request.headers for key in ('authorization', 'access-token', 'cookie'))
            return httpx.Response(200, content=b'synthetic-raster', headers={'content-type': 'image/png'})
        assert (await fetch_image('https://static.pushbullet.com/image', httpx.MockTransport(handler)))['data'] == base64.b64encode(b'synthetic-raster').decode()
        for status, headers, content, expected in [
            (302, {'location': 'https://evil.example/x'}, b'', 502),
            (200, {'content-type': 'image/svg+xml'}, b'<svg/>', 415),
            (200, {'content-type': 'image/png', 'content-length': str(MAX_IMAGE_BYTES + 1)}, b'', 413),
            (200, {'content-type': 'image/png'}, b'x' * (MAX_IMAGE_BYTES + 1), 413),
            (403, {}, b'', 410),
        ]:
            with pytest.raises(HTTPException) as caught:
                await fetch_image('https://static.pushbullet.com/signed-secret', httpx.MockTransport(lambda request: httpx.Response(status, headers=headers, content=content)))
            assert caught.value.status_code == expected
            assert 'signed-secret' not in str(caught.value.detail)
        assert len(requests) == 1
        assert all(safe_media_url('https://' + host + '/fixture') for host in MEDIA_HOSTS)
    asyncio.run(run())


def test_media_route_uses_only_account_retained_reference(tmp_path, monkeypatch):
    import pbdesk.api.media as media
    store = Store(Settings(tmp_path))
    store.set_setting('pushbullet_account_id', 'a')
    store.ingest(normalize({'iden': 'image', 'type': 'file', 'file_type': 'image/png',
        'file_url': 'https://static.pushbullet.com/fixture.png'}))
    item_id = store.list_items()[0]['id']
    app = FastAPI()
    app.state.store = store
    async def authorization(request: Request):
        return request.headers.get('x-role', 'desktop')
    calls = []
    async def fake_fetch(url):
        calls.append(url)
        return {'data': 'dGVzdA==', 'content_type': 'image/png'}
    monkeypatch.setattr(media, 'fetch_image', fake_fetch)
    install_media_routes(app, authorization, Settings(tmp_path))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            params = {'account_id': 'a', 'kind': 'item', 'item_id': item_id}
            assert (await client.get('/api/v1/media/image', params=params, headers={'x-role': 'mcp'})).status_code == 403
            assert not calls
            assert (await client.get('/api/v1/media/image', params={**params, 'account_id': 'b'})).status_code == 409
            assert (await client.get('/api/v1/media/image', params={**params, 'item_id': item_id + 1})).status_code == 404
            result = await client.get('/api/v1/media/image', params=params)
            assert result.status_code == 200 and result.json()['account_id'] == 'a'
            assert calls == ['https://static.pushbullet.com/fixture.png']
            store.set_setting('pushbullet_account_id', 'b')
            assert (await client.get('/api/v1/media/image', params={**params, 'account_id': 'b'})).status_code == 404
    asyncio.run(run())
    store.close()


def test_unchanged_pre_media_thread_is_reread_once_on_normal_sync(tmp_path):
    from test_sms_history import setup_provider, THREAD, ROWS
    async def run():
        requests = []
        def handler(request):
            requests.append(request.url.path)
            return httpx.Response(200, json={'threads': [THREAD]} if request.url.path.endswith('_threads') else
                {'thread': [{**row, 'image_urls': ['https://static.pushbullet.com/fixture.png']} for row in ROWS]})
        store, provider, original = setup_provider(tmp_path, handler)
        await original.aclose()
        old_fingerprint = hashlib.sha256(json.dumps(THREAD, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        old_messages = SmsHistory.normalize_thread(THREAD, ROWS, False)
        for item in old_messages:
            item.pop('image_urls')
            item.pop('recipients')
        store.merge_sms_thread('account', 'phone', 'conversation', old_messages, old_fingerprint)
        assert await provider.sms.sync_device('phone')
        assert len(requests) == 2
        assert store.sms_snapshot('phone')['messages'][0]['image_urls']
        assert await provider.sms.sync_device('phone')
        assert len(requests) == 3
        await provider.close()
        store.close()
    asyncio.run(run())


def test_sms_snapshots_do_not_repeat_inline_contact_bytes(tmp_path):
    store = Store(Settings(tmp_path))
    store.set_setting('pushbullet_account_id', 'acct')
    values = [dict(message_id=str(index), direction='incoming', body='fixture', timestamp=1700000000 + index,
        addresses=['+15550000001'], name='Synthetic', decrypted=0, image_urls=[],
        recipients=[{'address': '+15550000001', 'thumbnail': 'dGVzdA=='}]) for index in range(10)]
    store.merge_sms_thread('acct', 'phone', 'conversation', values, 'fingerprint')
    rows = store.sms_snapshot('phone')['messages']
    assert sum(bool(row['recipients'][0].get('thumbnail')) for row in rows) == 1
    assert rows[-1]['recipients'][0]['thumbnail'] == 'dGVzdA=='
    assert all(row['addresses'] == ['+15550000001'] for row in rows)
    store.close()


def test_contact_media_resolves_sole_recipient_and_rechecks_account_after_fetch(tmp_path, monkeypatch):
    import pbdesk.api.media as media
    store = Store(Settings(tmp_path))
    store.set_setting('pushbullet_account_id', 'a')
    row = dict(message_id='one', direction='incoming', body='fixture', timestamp=1700000000,
        addresses=['+15550000001'], name='Synthetic', decrypted=0, image_urls=[],
        recipients=[{'address': '+15550000001', 'image_url': 'https://lh6.googleusercontent.com/fixture'}])
    store.merge_sms_thread('a', 'phone', 'thread', [row], 'fingerprint')
    message_id = store.sms_snapshot('phone')['messages'][0]['id']
    app = FastAPI()
    app.state.store = store
    async def authorization():
        return 'desktop'
    fetched = []
    change_account = False
    async def fake_fetch(url):
        fetched.append(url)
        if change_account:
            store.set_setting('pushbullet_account_id', 'b')
        return {'data': 'dGVzdA==', 'content_type': 'image/jpeg'}
    monkeypatch.setattr(media, 'fetch_image', fake_fetch)
    install_media_routes(app, authorization, Settings(tmp_path))
    async def run():
        nonlocal change_account
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            params = {'account_id': 'a', 'kind': 'contact', 'message_id': message_id}
            assert (await client.get('/api/v1/media/image', params=params)).status_code == 200
            assert fetched == ['https://lh6.googleusercontent.com/fixture']
            group = {**row, 'recipients': row['recipients'] + [{'address': '+15550000002'}]}
            store.merge_sms_thread('a', 'phone', 'thread', [group], 'group')
            assert (await client.get('/api/v1/media/image', params=params)).status_code == 404
            assert len(fetched) == 1
            store.merge_sms_thread('a', 'phone', 'thread', [row], 'sole')
            change_account = True
            result = await client.get('/api/v1/media/image', params=params)
            assert result.status_code == 409 and 'data' not in result.json()
    asyncio.run(run())
    store.close()
