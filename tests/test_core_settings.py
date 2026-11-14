"""Core configuration/restart tests use private temporary state and fake adapters."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import service_token
from pbdesk.domain.actions import ActionRequest, Approval
from pbdesk.services.client_lifecycle import stop_generation
from pbdesk.services.core_settings import (RESTART_RECORD, CoreSettingsService, finish_mutation,
    inspect_restart, pending_restart, write_restart)
from pbdesk.storage.workflows import actions
from sqlalchemy import update

DEVICE = dict(id='fake', nickname='fake', type=None, manufacturer=None, model=None,
              active=True, can_push=True, can_sms=True)


@pytest.fixture(autouse=True)
def idle_background_dispatcher(monkeypatch):
    # Every interlock test schedules dispatch_once explicitly when needed; an
    # unrelated initial empty poll must not race the deterministic gate assertions.
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr('pbdesk.services.outbox.Dispatcher.run', idle)


def auth(settings, role='desktop'):
    return {'Authorization': 'Bearer ' + service_token(settings, role)}


def profile():
    return {'demo': True, 'fixture': None, 'standalone': False, 'notifications': False,
            'timing': {'renew': 5, 'expiry': 45, 'release_grace': 3, 'startup_grace': 20}}


async def fake_launcher(*args):
    return None


def current(client):
    return client.app.state.store.instance_id


def prepare(store, *, approve=False):
    store.save_devices([DEVICE])
    action = store.create_action(ActionRequest(client_request_id=uuid4(), kind='note', device_id='fake',
        body='preserved exact draft', transport='provider_plaintext'), 'desktop')
    if approve:
        store.approve_action(action['id'], Approval(revision=1, fingerprint=action['fingerprint'],
            allow_provider_plaintext=True), 'desktop')
    return action


def test_settings_roles_methods_readonly_and_strict_update(tmp_path, monkeypatch):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'application')
    monkeypatch.setattr('pbdesk.services.notifications.shutil.which', lambda *a: '/fake/notify-send')
    with TestClient(create_app(settings, demo=True)) as client:
        url = '/api/v1/core/settings'
        assert client.get(url).status_code == 401
        for role in ['web', 'mcp']:
            assert client.get(url, headers=auth(settings, role)).status_code == 403
            assert client.patch(url, headers=auth(settings, role), json={}).status_code == 403
            assert client.post('/api/v1/core/restart', headers=auth(settings, role), json={}).status_code == 403
        response = client.get(url, headers=auth(settings))
        data = response.json()
        assert data['instance_id'] == current(client)
        assert data['lifecycle'] == {'mode': 'coupled', 'desktop_clients': 0, 'web_clients': 0,
            'renew_seconds': 5, 'expiry_seconds': 45, 'release_grace_seconds': 3}
        assert data['paths'] == {'application': str(settings.application_dir), 'state': str(settings.state_dir),
                                 'database': str(settings.application_dir / 'pbdesk.sqlite3')}
        assert data['addresses']['core'] == settings.core_url
        assert data['notifications'] == {'requested': False, 'enabled': False, 'available': True, 'outcome': 'disabled'}
        assert not data['restarting']
        for method in ['POST', 'PUT', 'DELETE']:
            assert client.request(method, url, headers=auth(settings)).status_code == 405
        payload = {'expected_instance_id': current(client), 'notifications_enabled': True}
        for value in [1, 'true', None]:
            assert client.patch(url, headers=auth(settings), json={**payload, 'notifications_enabled': value}).status_code == 422
        for extra in ['core_port', 'state_dir', 'mode', 'expiry_seconds']:
            assert client.patch(url, headers=auth(settings), json={**payload, extra: 1}).status_code == 422
        assert client.patch(url, headers=auth(settings), json={**payload, 'expected_instance_id': str(uuid4())}).status_code == 409
        assert client.patch(url, headers=auth(settings), json={**payload, 'expected_instance_id': 'bad'}).status_code == 422
        assert client.patch(url, headers={**auth(settings), 'Origin': 'http://evil.example'}, json=payload).status_code == 403


def test_notifications_persist_override_cli_and_truthful_availability(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    monkeypatch.setattr('pbdesk.services.notifications.shutil.which', lambda *a: None)
    with TestClient(create_app(settings, demo=True, notifications=False)) as client:
        payload = {'expected_instance_id': current(client), 'notifications_enabled': True}
        data = client.patch('/api/v1/core/settings', headers=auth(settings), json=payload).json()
        assert data['notifications'] == {'requested': True, 'enabled': False, 'available': False, 'outcome': 'unavailable'}
        assert client.app.state.store.get_setting('core_notifications_enabled') is True
    monkeypatch.setattr('pbdesk.services.notifications.shutil.which', lambda *a: '/fake/notify-send')
    with TestClient(create_app(settings, demo=True, notifications=False)) as client:
        data = client.get('/api/v1/core/settings', headers=auth(settings)).json()
        assert data['notifications']['requested'] and data['notifications']['enabled']
        payload = {'expected_instance_id': current(client), 'notifications_enabled': False}
        assert client.patch('/api/v1/core/settings', headers=auth(settings), json=payload).json()['notifications']['outcome'] == 'disabled'
    with TestClient(create_app(settings, demo=True, notifications=True)) as client:
        assert not client.app.state.notifier.requested


def test_restart_idempotency_validation_and_pending_draft_preserved(tmp_path):
    settings = Settings(tmp_path)
    launches, shutdowns = [], []
    async def launch(*args): launches.append(args)
    with TestClient(create_app(settings, demo=True, restart_launcher=launch)) as client:
        app = client.app
        app.state.shutdown_callback = lambda: shutdowns.append(True)
        draft = prepare(app.state.store)
        instance = current(client)
        generation = stop_generation(settings)
        payload = {'expected_instance_id': instance, 'request_id': 'restart-one'}
        for extra in ['demo', 'fixture', 'argv', 'env']:
            assert client.post('/api/v1/core/restart', headers=auth(settings), json={**payload, extra: 'bad'}).status_code == 422
        assert client.post('/api/v1/core/restart', headers=auth(settings), json={**payload, 'expected_instance_id': str(uuid4())}).status_code == 409
        response = client.post('/api/v1/core/restart', headers=auth(settings), json=payload)
        assert response.status_code == 202, response.text
        assert response.json() == {'accepted': True, 'instance_id': instance, 'request_id': 'restart-one'}
        assert client.post('/api/v1/core/restart', headers=auth(settings), json=payload).json() == response.json()
        assert client.post('/api/v1/core/restart', headers=auth(settings), json={**payload, 'request_id': 'different'}).status_code == 409
        assert len(launches) == 1 and stop_generation(settings) == generation
        assert app.state.dispatcher.paused and app.state.restart_intent
        assert app.state.store.action(draft['id'])['state'] == 'pending_approval'
        assert client.get('/api/v1/core/settings', headers=auth(settings)).json()['restarting']
        assert client.patch('/api/v1/core/settings', headers=auth(settings), json={'expected_instance_id': instance, 'notifications_enabled': False}).status_code == 409
        assert client.post(f"/api/v1/actions/{draft['id']}/approve", headers=auth(settings), json={
            'revision': 1, 'fingerprint': draft['fingerprint'], 'allow_provider_plaintext': True}).status_code == 409
        assert client.post('/api/v1/devices/refresh', headers=auth(settings)).status_code == 409
        record = json.loads((settings.state_dir / RESTART_RECORD).read_text())
        assert record['instance_id'] == instance and record['generation'] == generation
        assert record['profile']['demo'] is True
        assert (settings.state_dir / RESTART_RECORD).stat().st_mode & 0o777 == 0o600
        time.sleep(.25)
        assert shutdowns == [True]


@pytest.mark.parametrize('state', ['ready', 'submitting'])
def test_approved_outbox_prevents_restart(tmp_path, state):
    settings = Settings(tmp_path)
    with TestClient(create_app(settings, demo=True, restart_launcher=fake_launcher)) as client:
        app = client.app
        app.state.shutdown_callback = lambda: None
        draft = prepare(app.state.store, approve=True)
        if state == 'submitting':
            app.state.store.claim_action(draft['id'])
        response = client.post('/api/v1/core/restart', headers=auth(settings), json={
            'expected_instance_id': current(client), 'request_id': 'busy'})
        assert response.status_code == 409 and 'Approved' in response.json()['detail']
        assert not app.state.core_settings.restarting and not app.state.dispatcher.paused
        assert not (settings.state_dir / RESTART_RECORD).exists()
        assert app.state.store.action(draft['id'])['state'] == state


def test_no_callback_and_helper_failure_roll_back(tmp_path):
    settings = Settings(tmp_path)
    async def failing(*args): raise RuntimeError('fake helper failure')
    with TestClient(create_app(settings, demo=True, restart_launcher=failing)) as client:
        app = client.app
        payload = {'expected_instance_id': current(client), 'request_id': 'one'}
        assert client.post('/api/v1/core/restart', headers=auth(settings), json=payload).status_code == 409
        app.state.shutdown_callback = lambda: None
        response = client.post('/api/v1/core/restart', headers=auth(settings), json=payload)
        assert response.status_code == 409
        assert not app.state.core_settings.restarting and not app.state.dispatcher.paused
        assert not (settings.state_dir / RESTART_RECORD).exists()
        assert not app.state.restart_intent


def test_custom_embedded_core_restart_is_clear_unsupported(tmp_path):
    from test_core_actions_api import FakeProvider
    settings = Settings(tmp_path)
    with TestClient(create_app(settings, demo=True, provider_factory=FakeProvider)) as client:
        client.app.state.shutdown_callback = lambda: None
        response = client.post('/api/v1/core/restart', headers=auth(settings), json={
            'expected_instance_id': current(client), 'request_id': 'one'})
        assert response.status_code == 409 and 'custom launch settings' in response.json()['detail']


def test_restart_refuses_active_provider_mutation_and_no_duplicate_send(tmp_path):
    settings = Settings(tmp_path)
    entered, release = threading.Event(), threading.Event()
    with TestClient(create_app(settings, demo=True, restart_launcher=fake_launcher)) as client:
        app = client.app
        app.state.shutdown_callback = lambda: None
        class Fake:
            state = 'connected'
            send_ready = False
            async def refresh_devices(self):
                entered.set()
                await asyncio.to_thread(release.wait)
            async def close(self): pass
        app.state.provider = Fake()
        with ThreadPoolExecutor(max_workers=2) as pool:
            mutation = pool.submit(client.post, '/api/v1/devices/refresh', headers=auth(settings))
            assert entered.wait(2)
            response = client.post('/api/v1/core/restart', headers=auth(settings), json={
                'expected_instance_id': current(client), 'request_id': 'busy'})
            assert response.status_code == 409 and 'active' in response.json()['detail']
            release.set()
            assert mutation.result(timeout=3).status_code == 200
        assert not app.state.core_settings.restarting


@pytest.mark.parametrize('cancel', [False, True])
def test_approval_in_flight_retains_gate_until_thread_commit(tmp_path, monkeypatch, cancel):
    async def run():
        settings = Settings(tmp_path)
        app = create_app(settings, demo=True, restart_launcher=fake_launcher)
        async with app.router.lifespan_context(app):
            app.state.shutdown_callback = lambda: None
            draft = prepare(app.state.store)
            entered, release = threading.Event(), threading.Event()
            original = app.state.store.approve_action
            def blocked(*args):
                entered.set()
                release.wait(4)
                return original(*args)
            monkeypatch.setattr(app.state.store, 'approve_action', blocked)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
                approval = asyncio.create_task(client.post(f"/api/v1/actions/{draft['id']}/approve", headers=auth(settings), json={
                    'revision': 1, 'fingerprint': draft['fingerprint'], 'allow_provider_plaintext': True}))
                assert await asyncio.to_thread(entered.wait, 2)
                if cancel:
                    approval.cancel()
                    await asyncio.sleep(.05)
                assert app.state.core_settings.gate.active == 1
                with pytest.raises(HTTPException) as error:
                    await app.state.core_settings.restart(app.state.store.instance_id, 'before-commit')
                assert error.value.status_code == 409
                release.set()
                if cancel:
                    with pytest.raises(asyncio.CancelledError): await approval
                else:
                    assert (await approval).status_code == 200
                assert app.state.core_settings.gate.active == 0
                with pytest.raises(HTTPException) as error:
                    await app.state.core_settings.restart(app.state.store.instance_id, 'after-commit')
                assert 'Approved messages' in error.value.detail
                assert app.state.store.action(draft['id'])['state'] == 'ready'
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['publication', 'helper'])
def test_cancelled_restart_preparation_completes_owned_transaction(tmp_path, monkeypatch, phase):
    from pbdesk.services import core_settings
    async def run():
        settings = Settings(tmp_path)
        entered, release = threading.Event(), threading.Event()
        shutdowns = []
        async def launcher(*args):
            if phase == 'helper':
                entered.set()
                await asyncio.to_thread(release.wait)
        app = create_app(settings, demo=True, restart_launcher=launcher)
        async with app.router.lifespan_context(app):
            app.state.shutdown_callback = lambda: shutdowns.append(True)
            original = core_settings.publish_restart
            def publication(*args):
                if phase == 'publication':
                    entered.set()
                    release.wait(3)
                return original(*args)
            monkeypatch.setattr(core_settings, 'publish_restart', publication)
            restart = asyncio.create_task(app.state.core_settings.restart(app.state.store.instance_id, 'cancelled'))
            assert await asyncio.to_thread(entered.wait, 2)
            restart.cancel()
            await asyncio.sleep(.05)
            assert not restart.done() and app.state.dispatcher.paused
            release.set()
            with pytest.raises(asyncio.CancelledError): await restart
            assert app.state.restart_intent and app.state.core_settings.restarting
            assert (settings.state_dir / RESTART_RECORD).exists()
            await asyncio.sleep(.25)
            assert shutdowns == [True]
    asyncio.run(run())


def test_cancelled_settings_write_applies_committed_live_value(tmp_path, monkeypatch):
    async def run():
        settings = Settings(tmp_path)
        monkeypatch.setattr('pbdesk.services.notifications.shutil.which', lambda *a: '/fake/notify-send')
        app = create_app(settings, demo=True)
        async with app.router.lifespan_context(app):
            entered, release = threading.Event(), threading.Event()
            original = app.state.store.set_setting
            def blocked(key, value):
                if key == 'core_notifications_enabled':
                    entered.set()
                    release.wait(3)
                return original(key, value)
            monkeypatch.setattr(app.state.store, 'set_setting', blocked)
            task = asyncio.create_task(app.state.core_settings.update(app.state.store.instance_id, True))
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(.05)
            assert app.state.core_settings.gate.active == 1
            release.set()
            with pytest.raises(asyncio.CancelledError): await task
            assert app.state.store.get_setting('core_notifications_enabled') is True
            assert app.state.notifier.enabled and app.state.notifier.requested
            assert app.state.core_settings.gate.active == 0
    asyncio.run(run())


def test_dispatcher_pause_is_checked_inside_dispatch_lock(tmp_path):
    from pbdesk.services.outbox import Dispatcher
    from pbdesk.storage.database import Store
    async def run():
        store = Store(Settings(tmp_path))
        draft = prepare(store, approve=True)
        class Fake:
            send_ready = True
            calls = 0
            async def refresh_devices(self): pass
            async def send(self, action): self.calls += 1; return 'fake'
        provider = Fake()
        dispatcher = Dispatcher(store, lambda: provider)
        await dispatcher.lock.acquire()
        pending = asyncio.create_task(dispatcher.dispatch_once())
        await asyncio.sleep(0)
        dispatcher.paused = True
        dispatcher.lock.release()
        await pending
        assert provider.calls == 0 and store.action(draft['id'])['state'] == 'ready'
        store.close()
    asyncio.run(run())


def test_restart_record_expiry_shape_permissions_and_stop_all(tmp_path, monkeypatch):
    from pbdesk.services.lifecycle import ensure_core, CoreStartupError
    settings = Settings(tmp_path)
    instance, generation = str(uuid4()), stop_generation(settings)
    value = write_restart(settings, instance, generation, profile())
    assert inspect_restart(settings)[1] == value
    path = settings.state_dir / RESTART_RECORD
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match='private regular file'):
        inspect_restart(settings)
    path.chmod(0o600)
    now = time.time()
    expired = {**value, 'created_at': now - 61, 'expires_at': now - 1}
    path.write_text(json.dumps(expired))
    monkeypatch.setattr('pbdesk.services.lifecycle._probe', lambda *a: None)
    monkeypatch.setattr('pbdesk.services.lifecycle.subprocess.Popen', lambda *a, **k: pytest.fail('Expired restart must not fall back'))
    with pytest.raises(CoreStartupError, match='expired'):
        ensure_core(settings, generation=generation)
    write_restart(settings, instance, generation, profile())
    stop_generation(settings, advance=True)
    assert inspect_restart(settings)[1] is None and not path.exists()


def test_active_send_refuses_restart_without_cancelling_or_replaying(tmp_path):
    async def run():
        settings = Settings(tmp_path)
        app = create_app(settings, demo=True, restart_launcher=fake_launcher)
        async with app.router.lifespan_context(app):
            app.state.shutdown_callback = lambda: None
            draft = prepare(app.state.store, approve=True)
            entered, release = asyncio.Event(), asyncio.Event()
            class Fake:
                state = 'connected'
                send_ready = True
                calls = 0
                async def refresh_devices(self): pass
                async def send(self, action):
                    self.calls += 1
                    entered.set()
                    await release.wait()
                    return 'one-fake-submission'
                async def close(self): pass
            provider = Fake()
            app.state.provider = provider
            sending = asyncio.create_task(app.state.dispatcher.dispatch_once())
            await entered.wait()
            with pytest.raises(HTTPException) as error:
                await app.state.core_settings.restart(app.state.store.instance_id, 'active-send')
            assert error.value.status_code == 409
            assert not sending.done() and not app.state.core_settings.restarting
            assert app.state.store.action(draft['id'])['state'] == 'submitting'
            release.set()
            await sending
            await app.state.dispatcher.dispatch_once()
            assert provider.calls == 1
            assert app.state.store.action(draft['id'])['state'] == 'upstream_accepted'
    asyncio.run(run())


def test_concurrent_same_restart_coalesces_and_conflict_rejects(tmp_path):
    settings = Settings(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    async def launcher(*args):
        calls.append(args)
        entered.set()
        await asyncio.to_thread(release.wait)
    with TestClient(create_app(settings, demo=True, restart_launcher=launcher)) as client:
        client.app.state.shutdown_callback = lambda: None
        payload = {'expected_instance_id': current(client), 'request_id': 'same'}
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(client.post, '/api/v1/core/restart', headers=auth(settings), json=payload)
            assert entered.wait(2)
            second = pool.submit(client.post, '/api/v1/core/restart', headers=auth(settings), json=payload)
            third = pool.submit(client.post, '/api/v1/core/restart', headers=auth(settings), json={**payload, 'request_id': 'conflict'})
            release.set()
            assert first.result(timeout=3).status_code == second.result(timeout=3).status_code == 202
            assert third.result(timeout=3).status_code == 409
        assert len(calls) == 1


def test_stop_all_cancels_restart_publication_race(tmp_path, monkeypatch):
    from pbdesk.services import core_settings
    async def run():
        settings = Settings(tmp_path)
        entered, release = threading.Event(), threading.Event()
        app = create_app(settings, demo=True, restart_launcher=fake_launcher)
        async with app.router.lifespan_context(app):
            app.state.shutdown_callback = lambda: None
            generation = stop_generation(settings)
            original = core_settings.publish_restart
            def publication(*args):
                entered.set()
                release.wait(3)
                return original(*args)
            monkeypatch.setattr(core_settings, 'publish_restart', publication)
            task = asyncio.create_task(app.state.core_settings.restart(app.state.store.instance_id, 'racing-stop'))
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.to_thread(stop_generation, settings, advance=True)
            release.set()
            with pytest.raises(HTTPException) as error:
                await task
            assert error.value.status_code == 409
            assert not app.state.dispatcher.paused and not app.state.core_settings.restarting
            assert stop_generation(settings) != generation
            assert not (settings.state_dir / RESTART_RECORD).exists()
    asyncio.run(run())


def test_restart_record_rejects_symlink_oversize_and_unknown_profile(tmp_path):
    settings = Settings(tmp_path)
    instance, generation = str(uuid4()), stop_generation(settings)
    value = write_restart(settings, instance, generation, profile())
    path = settings.state_dir / RESTART_RECORD
    target = settings.state_dir / 'untrusted-record.json'
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(RuntimeError, match='inaccessible'):
        inspect_restart(settings)
    path.unlink()
    path.write_text('x' * 16385)
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match='private regular file'):
        inspect_restart(settings)
    invalid = {**value, 'profile': {**value['profile'], 'argv': ['untrusted']}}
    path.write_text(json.dumps(invalid))
    with pytest.raises(RuntimeError, match='invalid'):
        inspect_restart(settings)
