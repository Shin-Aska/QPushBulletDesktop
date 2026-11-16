"""CLI readiness uses signed fixture health; no live wallet or provider calls."""
import json
import sys

import httpx
import pytest

from pbdesk import cli
from pbdesk.config import Settings
from pbdesk.credentials import health_response


@pytest.fixture
def clock(monkeypatch):
    now, sleeps = [0.0], []
    monkeypatch.setattr(cli.time, 'monotonic', lambda: now[0])
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(cli.time, 'sleep', sleep)
    return now, sleeps


def test_signed_waiter_waits_for_history_not_transport(tmp_path, monkeypatch, clock):
    settings = Settings(tmp_path)
    calls = []
    actual = httpx.Client
    def handle(request):
        calls.append(request)
        assert request.url.path == '/api/v1/health'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json=health_response(settings, request.headers,
            '11111111-1111-1111-1111-111111111111', True, history_ready=len(calls) >= 3))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    body = cli.wait_for_history_ready(settings, timeout=1)
    assert body['history_ready']
    assert len(calls) == 3 and clock[1] == [.1, .1]


@pytest.mark.parametrize('mode', ['old', 'unverified', 'locked', 'refused'])
def test_signed_waiter_fails_actionably_without_bearer_or_wallet(tmp_path, monkeypatch, clock, mode):
    settings = Settings(tmp_path)
    actual = httpx.Client
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.path == '/api/v1/health'
        assert 'authorization' not in request.headers
        if mode == 'refused':
            raise httpx.ConnectError('fictional transport failure', request=request)
        kwargs = {} if mode == 'old' else {'history_ready': False}
        body = health_response(settings, request.headers,
            '11111111-1111-1111-1111-111111111111', True, **kwargs)
        if mode == 'unverified':
            body['proof'] = '0' * 64
        return httpx.Response(200, json=body)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    monkeypatch.setattr('pbdesk.services.kwallet.authorize_database_wallet', lambda *a, **k: pytest.fail('No wallet authorization in CLI readiness'))
    with pytest.raises(cli.HistoryNotReady, match='Open pbdesk to protect or unlock local history'):
        cli.wait_for_history_ready(settings, timeout=.2)
    assert calls
    assert clock[0][0] <= .2


@pytest.mark.parametrize('command', ['web', 'configure', 'reload'])
@pytest.mark.parametrize('mode', ['transition', 'old', 'locked'])
def test_cli_commands_gate_reads_and_mutate_once_after_signed_history_ready(tmp_path, monkeypatch, clock, command, mode):
    settings = Settings(tmp_path)
    monkeypatch.setattr(Settings, 'from_env', staticmethod(lambda: settings))
    monkeypatch.setattr('pbdesk.services.lifecycle.ensure_core', lambda *a, **k: {'ready': True})
    lease_closed = []
    class Lease:
        def __init__(self, *args, **kwargs): pass
        def start(self): return self
        def close(self): lease_closed.append(True)
    monkeypatch.setattr('pbdesk.services.client_lifecycle.ClientLease', Lease)
    actual = httpx.Client
    calls, health_count, mutations = [], [0], []
    def handle(request):
        calls.append(request.url.path)
        if request.url.path == '/api/v1/health':
            assert 'authorization' not in request.headers
            health_count[0] += 1
            kwargs = {} if mode == 'old' else {'history_ready': mode == 'transition' and health_count[0] >= 2}
            return httpx.Response(200, json=health_response(settings, request.headers,
                '11111111-1111-1111-1111-111111111111', True, **kwargs))
        assert mode == 'transition' and health_count[0] >= 2
        assert request.headers['authorization'].startswith('Bearer ')
        if request.method == 'POST':
            mutations.append(request)
            return httpx.Response(200, json={'configured': True})
        assert request.url.path == '/api/v1/configuration'
        return httpx.Response(200, json={'setup': {'revision': 7}})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    if command == 'web':
        monkeypatch.setattr(sys, 'argv', ['pbdesk-web', 'enable'])
        invoke = cli.web_main
        expected = '/api/v1/web'
    elif command == 'configure':
        invoke = lambda: cli.configure_connection(settings, {'backend': 'kwallet', 'wallet': 'fictional'})
        expected = '/api/v1/configuration/provider'
    else:
        invoke = lambda: cli.admin_main(['reload-provider'])
        expected = '/api/v1/provider/reload'
    if mode == 'transition':
        invoke()
        assert len(mutations) == 1 and mutations[0].url.path == expected
        assert calls[:2] == ['/api/v1/health', '/api/v1/health']
        if command == 'configure':
            assert json.loads(mutations[0].content)['expected_revision'] == 7
    else:
        error = cli.HistoryNotReady if command == 'configure' else SystemExit
        with pytest.raises(error):
            invoke()
        assert not mutations and all(path == '/api/v1/health' for path in calls)
    if command == 'configure':
        assert lease_closed == [True]
