"""Isolated process lifecycle acceptance: demo provider, private dirs, random ports."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from history_process_helpers import provision_process_history
from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import health_headers, health_response, service_token
from pbdesk.services.client_lifecycle import ClientLease, LeaseRegistry, LeaseTiming, stop_generation
from pbdesk.services.lifecycle import CoreStartupError, _probe, ensure_core
from pbdesk.services.web_identity import web_health_headers, verify_web_health

TIMING = {'renew': .15, 'expiry': 1.2, 'release_grace': .2, 'startup_grace': 1.5}


def eventually(operation, *, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = operation()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('Lifecycle condition timed out')


def free_ports():
    sockets = [socket.socket(), socket.socket()]
    try:
        for sock in sockets:
            sock.bind(('127.0.0.1', 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    core, web = free_ports()
    settings = Settings(tmp_path / 'state', core_port=core, web_port=web, application_dir=tmp_path / 'application')
    settings.application_dir.mkdir(exist_ok=True)
    provision_process_history(settings, tmp_path, monkeypatch)
    for key, value in {'PBDESK_STATE_DIR': settings.state_dir, 'PBDESK_APPLICATION_DIR': settings.application_dir,
        'PBDESK_CORE_PORT': core, 'PBDESK_WEB_PORT': web, 'PBDESK_LIFECYCLE': 'foreground',
        'PBDESK_LIFECYCLE_DEMO': '1', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/nonexistent-pbdesk-test-bus', 'PBDESK_LEASE_TIMING': json.dumps(TIMING)}.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv('PBDESK_STOP_GENERATION', raising=False)
    clients = []
    children = []
    from pbdesk.services import lifecycle
    original = subprocess.Popen
    def owned(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(lifecycle.subprocess, 'Popen', owned)
    def lease(role='desktop'):
        client = ClientLease(settings, role, demo=True).start()
        clients.append(client)
        # These ordinary lifecycle scenarios use provisioned history. A lease
        # attaches to bootstrap transport before its asynchronous unlock finishes.
        eventually(lambda: (value := health()) and value.get('history_encryption') == 1
            and value.get('history_ready') and value['instance_id'] == client.instance_id)
        return client
    def health():
        with httpx.Client(timeout=.3, trust_env=False) as client:
            return _probe(settings, None, client)
    def request(method, path, **kwargs):
        with httpx.Client(base_url=settings.core_url, timeout=10, trust_env=False,
            headers={'Authorization': 'Bearer ' + service_token(settings, 'desktop')}) as client:
            return client.request(method, path, **kwargs)
    yield settings, lease, health, request, children
    for client in clients:
        client.close()
    try:
        if health():
            request('POST', '/api/v1/shutdown')
    except (httpx.HTTPError, CoreStartupError):
        pass
    for child in children:
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=5)


def test_cold_start_two_desktops_and_last_exit(isolated):
    settings, lease, health, request, children = isolated
    first = lease()
    second = lease()
    assert len(children) == 1
    original = health()['instance_id']
    first.close()
    time.sleep(1.6)  # also proves independent renewal keeps second alive past expiry
    assert health()['instance_id'] == original
    second.close()
    eventually(lambda: health() is None)


def test_stop_all_cannot_revive_but_explicit_launch_can(isolated):
    settings, lease, health, request, children = isolated
    old = lease()
    generation = old.generation
    assert request('POST', '/api/v1/shutdown').json()['stopping']
    eventually(lambda: health() is None)
    time.sleep(.6)
    assert health() is None
    with pytest.raises(CoreStartupError, match='Stop All'):
        ensure_core(settings, demo=True, generation=generation)
    new = lease()
    assert new.generation != generation
    assert new.instance_id != old.instance_id


def test_racing_crash_recovery_launches_one_core(isolated):
    settings, lease, health, request, children = isolated
    first, second = lease(), lease()
    old_instance = first.instance_id
    children[0].kill()
    children[0].wait(timeout=5)
    eventually(lambda: (value := health()) and value['instance_id'] != old_instance)
    eventually(lambda: first.instance_id == second.instance_id != old_instance)
    assert len(children) == 2


def test_client_process_crash_expires(isolated):
    settings, lease, health, request, children = isolated
    script = "from pbdesk.config import Settings; from pbdesk.services.client_lifecycle import ClientLease; import time; c=ClientLease(Settings.from_env(),demo=True).start(); print('ready',flush=True); time.sleep(60)"
    client = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, text=True)
    try:
        assert client.stdout.readline().strip() == 'ready'
        assert health()
        client.kill()
        client.wait(timeout=5)
        eventually(lambda: health() is None)
    finally:
        if client.poll() is None:
            client.kill()
            client.wait(timeout=5)


def test_web_process_keeps_core_and_adopts_after_crash(isolated):
    settings, lease, health, request, children = isolated
    desktop = lease()
    result = request('POST', '/api/v1/web', json={'enabled': True})
    assert result.status_code == 200, result.text
    web = result.json()
    assert web['enabled'] and web['ownership'] == 'owned', web
    desktop.close()
    time.sleep(1.6)
    old_core = health()['instance_id']
    children[0].kill()
    children[0].wait(timeout=5)
    eventually(lambda: (value := health()) and value['instance_id'] != old_core
        and value.get('history_encryption') == 1 and value.get('history_ready'))
    adopted = eventually(lambda: (value := request('GET', '/api/v1/web').json())['enabled'] and value)
    assert adopted['instance_id'] == web['instance_id']
    assert adopted['ownership'] == 'adopted'
    assert request('POST', '/api/v1/web', json={'enabled': False}).json()['enabled'] is False
    eventually(lambda: health() is None)


def test_web_exit_does_not_stop_live_desktop(isolated):
    settings, lease, health, request, children = isolated
    desktop = lease()
    assert request('POST', '/api/v1/web', json={'enabled': True}).json()['enabled']
    original = health()['instance_id']
    request('POST', '/api/v1/web', json={'enabled': False})
    time.sleep(1.6)
    assert health()['instance_id'] == original


def test_core_lease_roles_and_instance_binding(tmp_path):
    settings = Settings(tmp_path)
    with TestClient(create_app(settings, demo=True)) as client:
        instance = client.app.state.store.instance_id
        generation = stop_generation(settings)
        def auth(role): return {'Authorization': 'Bearer ' + service_token(settings, role)}
        payload = {'instance_id': instance, 'generation': generation}
        assert client.post('/api/v1/clients', json=payload, headers=auth('mcp')).status_code == 403
        response = client.post('/api/v1/clients', json=payload, headers=auth('desktop'))
        lease_id = response.json()['lease_id']
        url = '/api/v1/clients/' + lease_id
        assert client.put(url, json={'instance_id': instance}, headers=auth('web')).status_code == 409
        assert client.put(url, json={'instance_id': 'replacement'}, headers=auth('desktop')).status_code == 409
        assert client.delete(url, params={'instance_id': instance}, headers=auth('desktop')).status_code == 200
        assert client.post('/api/v1/actions', json={}, headers=auth('web')).status_code == 403


def test_old_core_requires_actionable_restart(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    monkeypatch.setattr('pbdesk.services.lifecycle._probe', lambda *a: {'ready': True, 'instance_id': 'old'})
    with pytest.raises(CoreStartupError, match='restart the shared core'):
        ensure_core(settings)


def test_close_during_recovery_does_not_register(tmp_path, monkeypatch):
    lease = ClientLease(Settings(tmp_path), demo=True)
    def interrupted(*args, **kwargs):
        lease.stop_event.set()
        return {'instance_id': 'late'}
    monkeypatch.setattr('pbdesk.services.lifecycle.ensure_core', interrupted)
    monkeypatch.setattr(lease, '_client', lambda: pytest.fail('No bearer registration after close'))
    with pytest.raises(RuntimeError, match='Client closed'):
        lease.connect()


def test_timing_validation():
    with pytest.raises(ValueError):
        LeaseTiming(renew=1, expiry=1)


def test_long_request_does_not_interrupt_heartbeat(isolated):
    settings, lease, health, request, children = isolated
    # A test-only endpoint in an actual isolated core blocks the caller past expiry.
    script = '''
import asyncio
import uvicorn
from pbdesk.config import Settings
from pbdesk.api.app import create_app
settings=Settings.from_env()
app=create_app(settings,demo=True)
@app.get('/test/slow')
async def slow():
    await asyncio.sleep(2)
    return {'finished': True}
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=settings.core_port,access_log=False,log_level='warning'))
app.state.shutdown_callback=lambda: setattr(server,'should_exit',True)
server.run()
'''
    subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    eventually(health)
    desktop = lease()
    original = desktop.instance_id
    assert request('GET', '/test/slow').json()['finished']
    assert health()['instance_id'] == original


@pytest.mark.parametrize('forged', [False, True])
def test_foreign_or_replaced_web_never_receives_shutdown_credential(tmp_path, monkeypatch, forged):
    from pbdesk.services import webhost
    from pbdesk.services.web_identity import web_health_response
    settings = Settings(tmp_path)
    generation = stop_generation(settings)
    calls = []
    actual = httpx.AsyncClient
    def handler(request):
        calls.append(request)
        body = web_health_response(settings, request.headers, 'b' * 64, generation)
        if forged:
            body['proof'] = 'forged'
        return httpx.Response(200, json=body)
    monkeypatch.setattr(webhost.httpx, 'AsyncClient', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handler)))
    async def run():
        host = webhost.WebHost(settings)
        host.instance_id, host.adopted = 'a' * 64, True
        result = await host.set_enabled(False)
        assert result['error']
        assert all('authorization' not in request.headers for request in calls)
        assert all(request.url.path == '/health' for request in calls)
    asyncio.run(run())


def test_foreign_web_port_is_never_replaced(tmp_path, monkeypatch):
    from pbdesk.services import webhost
    settings = Settings(tmp_path)
    actual = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'application': 'someone-else'})
    monkeypatch.setattr(webhost.httpx, 'AsyncClient', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(webhost.asyncio, 'create_subprocess_exec', lambda *a, **k: pytest.fail('Foreign listener must never be replaced'))
    async def run():
        result = await webhost.WebHost(settings).set_enabled(True)
        assert not result['enabled']
        assert 'unverified' in result['error']
    asyncio.run(run())
    assert all('authorization' not in request.headers for request in calls)


def test_stop_all_generation_checked_after_launcher_wait(tmp_path, monkeypatch):
    import fcntl
    from pbdesk.credentials import private_dir
    from pbdesk.services.client_lifecycle import _generation_locked
    settings = Settings(tmp_path)
    generation = stop_generation(settings)
    root = private_dir(settings)
    fd = os.open(root / 'launcher.lock', os.O_RDWR)
    monkeypatch.setattr('pbdesk.services.lifecycle.subprocess.Popen', lambda *a, **k: pytest.fail('Stopped client must not launch'))
    with ThreadPoolExecutor(max_workers=1) as executor:
        fcntl.flock(fd, fcntl.LOCK_EX)
        future = executor.submit(ensure_core, settings, generation=generation, demo=True)
        time.sleep(.1)
        _generation_locked(root, advance=True)
        os.close(fd)
        with pytest.raises(CoreStartupError, match='Stop All'):
            future.result(timeout=3)


def test_web_crash_does_not_stop_live_desktop(isolated):
    settings, lease, health, request, children = isolated
    desktop = lease()
    env = dict(os.environ, PBDESK_WEB_INSTANCE_ID='c' * 64,
               PBDESK_STOP_GENERATION=stop_generation(settings))
    web = subprocess.Popen([sys.executable, '-m', 'pbdesk.web'], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def web_ready():
        with httpx.Client(timeout=.2, trust_env=False) as client:
            try:
                headers = web_health_headers(settings)
                body = client.get(f'http://127.0.0.1:{settings.web_port}/health', headers=headers).json()
                return verify_web_health(settings, headers, body)
            except (httpx.HTTPError, ValueError):
                return False
    eventually(web_ready)
    state = request('POST', '/api/v1/web', json={'enabled': True}).json()
    assert state['ownership'] == 'adopted'
    web.kill()
    web.wait(timeout=5)
    time.sleep(1.6)
    assert health()['instance_id'] == desktop.instance_id
    assert not request('GET', '/api/v1/web').json()['enabled']
