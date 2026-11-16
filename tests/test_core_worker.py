"""CoreWorker transport-only tests: no Qt widgets/display or live network."""
import threading
from uuid import uuid4
import httpx
import pytest
from pbdesk.config import Settings
from pbdesk.credentials import health_response
from pbdesk.desktop.worker import CoreWorker

OLD = '11111111-1111-1111-1111-111111111111'
NEW = '22222222-2222-2222-2222-222222222222'


class FakeLease:
    def __init__(self, *args, **kwargs):
        self.stop_event = threading.Event()
        self.error = None
        self.wakes = 0
    def start(self): return self
    def wake(self): self.wakes += 1
    def close(self): pass


@pytest.mark.parametrize('queued_at_exit', [False, True])
def test_preferences_use_authenticated_api_and_queued_changes_flush_on_exit(tmp_path, monkeypatch, queued_at_exit):
    import json
    settings = Settings(tmp_path)
    actual = httpx.Client
    patches = []
    def handle(request):
        path = request.url.path
        if path.endswith('/health'):
            assert 'authorization' not in request.headers
            return httpx.Response(200, json=health_response(settings, request.headers, OLD, True))
        assert request.headers.get('authorization', '').startswith('Bearer ')
        if path.endswith('/capabilities'):
            return httpx.Response(200, json={'features': {'configuration': True}})
        if path == '/api/v1/configuration':
            return httpx.Response(200, json={'setup': {'required': False}, 'preferences': {}})
        assert path == '/api/v1/configuration/preferences' and request.method == 'PATCH'
        value = json.loads(request.content)
        patches.append(value)
        return httpx.Response(200, json={'preferences': value})
    monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', FakeLease)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    worker.result.connect(lambda value: worker.stop())
    if queued_at_exit:
        worker.request('configuration_get', {'request_id': 'read'})
    worker.request('preferences_update', {'request_id': 'preference', 'changes': {'main_page': 'sms', 'color_scheme': 'dark'}})
    worker.run()
    assert patches == [{'main_page': 'sms', 'color_scheme': 'dark'}]


@pytest.mark.parametrize('outcome, expected', [
    (422, 'source_blocked'), (410, 'unavailable'), (413, 'too_large'),
    (415, 'format'), (502, 'download_failed'), (504, 'timeout'),
    ('timeout', 'timeout'), ('network', 'core_unavailable'),
    ('invalid_image', 'decode_failed'), ('old_core', 'core_update'),
])
def test_image_failure_reports_reason_without_private_details_or_retry(tmp_path, monkeypatch, outcome, expected):
    from pbdesk.media_errors import IMAGE_ERRORS
    settings = Settings(tmp_path)
    actual = httpx.Client
    calls, results = [], []
    def handle(request):
        path = request.url.path
        if path.endswith('/health'):
            return httpx.Response(200, json=health_response(settings, request.headers, OLD, True))
        assert request.headers.get('authorization', '').startswith('Bearer ')
        if path.endswith('/capabilities'):
            return httpx.Response(200, json={'account_id': 'acct',
                'features': {'media_images': outcome != 'old_core'}})
        assert path == '/api/v1/media/image'
        calls.append(request)
        assert dict(request.url.params) == {'account_id': 'acct', 'kind': 'contact', 'message_id': '7', 'index': '0'}
        private = 'https://dl3.pushbulletusercontent.com/private-token/photo'
        if outcome == 'timeout':
            raise httpx.ReadTimeout(private, request=request)
        if outcome == 'network':
            raise httpx.ConnectError(private, request=request)
        if outcome == 'invalid_image':
            return httpx.Response(200, json={'account_id': 'acct', 'data': 'not-base64'})
        return httpx.Response(outcome, json={'detail': private})
    monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', FakeLease)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    worker.result.connect(lambda value: (results.append(value), worker.stop()))
    worker.request('media_image', {'account_id': 'acct', 'kind': 'contact', 'message_id': 7,
        'index': 0, 'revision': 'retained-url-hash', 'request_id': 'image-attempt'})
    worker.run()
    assert len(results) == 1 and len(calls) == (0 if outcome == 'old_core' else 1)
    result = results[0]
    assert result['command'] == 'media_image' and result['request_id'] == 'image-attempt'
    assert result['account_id'] == 'acct' and result['error_code'] == expected
    assert result['error'] == IMAGE_ERRORS[expected]
    assert 'private-token' not in repr(result)
    if isinstance(outcome, int):
        assert result['status'] == outcome


def test_core_commands_verify_identity_strip_patch_id_and_coalesce_restart(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual = httpx.Client
    calls, results, snapshots, stopped = [], [], [], []
    health_count = 0
    restarts = 0
    def handle(request):
        nonlocal health_count, restarts
        calls.append(request)
        path = request.url.path
        if path.endswith('/health'):
            health_count += 1
            instance = NEW if health_count >= 6 else OLD
            assert 'authorization' not in request.headers
            return httpx.Response(200, json=health_response(settings, request.headers, instance, True))
        assert request.headers.get('authorization', '').startswith('Bearer ')
        if path.endswith('/capabilities'):
            return httpx.Response(200, json={'account_id': 'retained-account', 'features': {'core_settings': True, 'core_restart': True}})
        if path.endswith('/core/settings'):
            if request.method == 'PATCH':
                import json
                assert json.loads(request.content) == {'expected_instance_id': OLD, 'notifications_enabled': True}
            return httpx.Response(200, json={'instance_id': OLD, 'notifications': {'requested': True}})
        if path.endswith('/core/restart'):
            restarts += 1
            return httpx.Response(202, json={'accepted': True, 'instance_id': OLD, 'request_id': 'restart-one'})
        if path.endswith('/items'):
            return httpx.Response(200, json={'account_id': 'retained-account', 'items': []})
        if path.endswith('/web'):
            return httpx.Response(200, json={'enabled': True})
        raise AssertionError(path)
    monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', FakeLease)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    worker.result.connect(results.append)
    worker.snapshot.connect(lambda value: (snapshots.append(value), worker.stop()))
    worker.stopped_all.connect(lambda: stopped.append(True))
    worker.request('core_settings', {'request_id': 'get-one'})
    worker.request('core_settings_update', {'expected_instance_id': OLD, 'notifications_enabled': True, 'request_id': 'patch-one'})
    payload = {'expected_instance_id': OLD, 'request_id': 'restart-one'}
    worker.request('core_restart', payload)
    worker.request('core_restart', payload)
    worker.request('core_restart', {**payload, 'request_id': 'conflict'})
    worker.run()
    assert restarts == 1 and len(results) == 5
    assert results[0]['command'] == 'core_settings' and results[0]['request_id'] == 'get-one'
    assert results[1]['command'] == 'core_settings_update' and results[1]['request_id'] == 'patch-one'
    assert results[2]['data']['accepted'] and results[3]['data']['accepted']
    assert results[4]['status'] == 409
    assert snapshots[-1]['instance_id'] == NEW and snapshots[-1]['capabilities']['account_id'] == 'retained-account'
    assert not stopped and worker.pending_restart is None
    assert worker.lease.wakes >= 2


def test_foreign_core_gets_no_bearer_for_settings_command(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual = httpx.Client
    calls, results = [], []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'application': 'pbdesk-core', 'api_version': 1, 'ready': True,
            'instance_id': OLD, 'proof': 'foreign'})
    monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', FakeLease)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    worker.result.connect(lambda value: (results.append(value), worker.stop()))
    worker.request('core_settings', {'request_id': 'foreign'})
    worker.run()
    assert results[0]['error'] and all('authorization' not in call.headers for call in calls)
    assert all(call.url.path.endswith('/health') for call in calls)


def test_old_core_settings_capability_is_actionable_and_sends_no_settings_call(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual = httpx.Client
    calls, results = [], []
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('/health'):
            return httpx.Response(200, json=health_response(settings, request.headers, OLD, True))
        assert request.url.path.endswith('/capabilities')
        return httpx.Response(200, json={'features': {}})
    monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', FakeLease)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    worker.result.connect(lambda value: (results.append(value), worker.stop()))
    worker.request('core_settings', {'request_id': 'old'})
    worker.run()
    assert 'updated core' in results[0]['error']
    assert all('/core/settings' not in str(call.url) for call in calls)


def test_ambiguous_restart_is_not_reposted_or_falsely_confirmed(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual = httpx.Client
    results, posts = [], []
    def handle(request):
        path = request.url.path
        if path.endswith('/health'):
            return httpx.Response(200, json=health_response(settings, request.headers, OLD, True))
        if path.endswith('/capabilities'):
            return httpx.Response(200, json={'features': {'core_settings': True, 'core_restart': True}})
        assert path.endswith('/core/restart')
        posts.append(request)
        raise httpx.ReadError('lost response', request=request)
    monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', FakeLease)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    def result(value):
        results.append(value)
        if len(results) == 2:
            worker.stop()
    worker.result.connect(result)
    payload = {'expected_instance_id': OLD, 'request_id': 'uncertain'}
    worker.request('core_restart', payload)
    worker.request('core_restart', payload)
    worker.run()
    assert len(posts) == 1 and results[0]['ambiguous']
    assert all('data' not in value for value in results)


def test_lost_restart_reconciles_idle_same_instance_then_allows_explicit_new_request(tmp_path, monkeypatch):
    import time
    settings = Settings(tmp_path)
    actual = httpx.Client
    results, posts = [], []
    worker = CoreWorker(settings)
    def handle(request):
        path = request.url.path
        if path.endswith('/health'):
            return httpx.Response(200, json=health_response(settings, request.headers, OLD, True))
        if path.endswith('/capabilities'):
            return httpx.Response(200, json={'features': {'core_settings': True, 'core_restart': True}})
        if path.endswith('/core/settings'):
            worker.pending_restart['started'] = time.monotonic() - 25
            return httpx.Response(200, json={'instance_id': OLD, 'restarting': False})
        assert path.endswith('/core/restart')
        posts.append(request)
        if len(posts) == 1:
            raise httpx.ReadError('request outcome not confirmed', request=request)
        return httpx.Response(202, json={'accepted': True, 'instance_id': OLD, 'request_id': 'explicit-new'})
    monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', FakeLease)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    def result(value):
        results.append(value)
        if len(results) == 3:
            worker.stop()
    worker.result.connect(result)
    worker.request('core_restart', {'expected_instance_id': OLD, 'request_id': 'lost'})
    worker.request('core_settings', {'request_id': 'manual-refresh-after-timeout'})
    worker.request('core_restart', {'expected_instance_id': OLD, 'request_id': 'explicit-new'})
    worker.run()
    assert len(posts) == 2 and results[0]['ambiguous']
    assert results[1]['data']['restarting'] is False
    assert results[2]['data']['accepted']
