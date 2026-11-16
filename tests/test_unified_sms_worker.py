"""All-device desktop polling against a fake existing per-phone core API."""
import threading
from copy import deepcopy

import httpx
import pytest

from pbdesk.config import Settings
from pbdesk.credentials import health_response
from pbdesk.desktop.worker import CoreWorker

INSTANCE = '11111111-1111-1111-1111-111111111111'


class FakeLease:
    def __init__(self, *args, **kwargs):
        self.stop_event = threading.Event()
        self.error = None
    def start(self): pass
    def wake(self): pass
    def close(self): pass


def phone(device, account='a', body='cached'):
    return {'account_id': account, 'messages': [{'id': 1, 'account_id': account,
        'device_id': device, 'conversation_id': 'same-thread', 'body': body}],
        'sync': {'state': 'ready', 'last_success_at': 10, 'error': None}}


class Core:
    def __init__(self, tmp_path, monkeypatch):
        self.settings = Settings(tmp_path)
        self.worker = CoreWorker(self.settings)
        self.account = 'a'
        self.devices = [{'id': 'one', 'can_sms': True}, {'id': 'two', 'can_sms': True}]
        self.actions = []
        self.requests, self.frames = [], []
        self.request_limit = 100
        self.sms_hook = None
        self.data_hook = None
        self.features = {'devices': True, 'send': True, 'sms_history': True}
        actual = httpx.Client
        monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', FakeLease)
        monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client',
            lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(self.handle)))
        self.worker.snapshot.connect(self.frames.append)

    def handle(self, request):
        self.requests.append(request)
        assert len(self.requests) < self.request_limit, 'poll did not finish'
        assert request.method == 'GET', 'browsing must never mutate or refresh provider'
        path = request.url.path
        if path.endswith('/health'):
            return httpx.Response(200, json=health_response(self.settings, request.headers,
                INSTANCE, True, history_ready=True))
        assert request.headers['authorization'].startswith('Bearer ')
        if path.endswith('/capabilities'):
            data = {'account_id': self.account, 'features': self.features}
        elif path.endswith('/items'):
            data = {'account_id': self.account, 'items': []}
        elif path.endswith('/web'):
            data = {'enabled': False}
        elif path.endswith('/devices'):
            data = {'account_id': self.account, 'devices': self.devices}
        elif path.endswith('/actions'):
            data = {'account_id': self.account, 'actions': self.actions}
        elif path.endswith('/sms'):
            assert set(request.url.params) == {'device_id'}
            device = request.url.params['device_id']
            if self.sms_hook:
                result = self.sms_hook(device, request)
                if result is not None:
                    return result
            data = phone(device, self.account)
        else:
            raise AssertionError(path)
        if self.data_hook:
            data = deepcopy(data)
            self.data_hook(path, data)
        return httpx.Response(200, json=data)

    def run_frame(self):
        self.worker.snapshot.connect(lambda frame: self.worker.stop() if 'instance_id' in frame else None)
        self.worker.run()
        return [frame for frame in self.frames if 'instance_id' in frame][-1]

    @property
    def reads(self):
        return [request.url.params['device_id'] for request in self.requests
            if request.url.path.endswith('/sms')]


def test_default_all_uses_existing_per_phone_gets_and_tags_duplicate_threads(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    frame = core.run_frame()
    assert core.reads == ['one', 'two']
    assert frame['sms_device_id'] is None and frame['sms'] is None
    assert set(frame['sms_by_device']) == {'one', 'two'}
    for device, sms in frame['sms_by_device'].items():
        assert sms['account_id'] == 'a'
        assert sms['messages'][0]['device_id'] == device
        assert sms['messages'][0]['conversation_id'] == 'same-thread'


def test_all_includes_inactive_history_cache_and_sms_actions_without_truncation(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    core.devices = [{'id': f'phone-{i:03}', 'can_sms': True, 'active': False} for i in range(120)]
    core.devices += [{'id': 'history-only', 'history': {'count': 4}}, {'id': 'no-sms'}]
    core.worker.sms_cache = {'removed': phone('removed'), 'foreign': phone('foreign', 'b')}
    core.actions = [{'kind': 'sms', 'device_id': 'action-only'}, {'kind': 'note', 'device_id': 'note-only'}]
    core.request_limit = 200
    frame = core.run_frame()
    assert len(frame['sms_by_device']) == 123
    assert {'removed', 'history-only', 'action-only'} <= set(core.reads)
    assert not {'foreign', 'no-sms', 'note-only'} & set(core.reads)


def test_filtered_read_retains_legacy_shape_and_opaque_generation(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    core.worker.request('select_device', {'device_id': 'two', 'account_id': 'a', 'request_id': 'opaque-generation'})
    frame = core.run_frame()
    assert core.reads == ['two']
    assert frame['sms_device_id'] == 'two' and frame['sms_request_id'] == 'opaque-generation'
    assert frame['sms'] == frame['sms_by_device']['two']


@pytest.mark.parametrize('failure', ['timeout', 'status', 'invalid_json'])
def test_partial_read_failure_retains_phone_while_other_updates(tmp_path, monkeypatch, failure):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    core.worker.sms_cache['one'] = phone('one', body='last good')
    def hook(device, request):
        if device == 'one':
            if failure == 'timeout':
                raise httpx.ReadTimeout('private-token', request=request)
            if failure == 'invalid_json':
                return httpx.Response(200, text='private-token')
            return httpx.Response(500, json={'detail': 'private-token'})
        return httpx.Response(200, json=phone(device, body='new text'))
    core.sms_hook = hook
    frame = core.run_frame()
    old, new = frame['sms_by_device']['one'], frame['sms_by_device']['two']
    assert old['messages'][0]['body'] == 'last good'
    assert old['sync']['last_success_at'] == 10 and old['read_error']
    assert new['messages'][0]['body'] == 'new text' and 'read_error' not in new
    assert 'private-token' not in repr(frame)


@pytest.mark.parametrize('failure', [False, True])
def test_switch_during_get_discards_frame_clears_cache_and_resets_all(tmp_path, monkeypatch, failure):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    core.worker.sms_cache['one'] = phone('one', body='private prior account')
    core.worker.device_id, core.worker.device_request_id = 'one', 'old-filter'
    switched = False
    def hook(device, request):
        nonlocal switched
        if not switched:
            switched = True
            core.account = 'b'
            if failure:
                raise httpx.ReadTimeout('private-token', request=request)
        return None
    core.sms_hook = hook
    frame = core.run_frame()
    assert frame['capabilities']['account_id'] == 'b'
    assert frame['sms_device_id'] is None and frame['sms_request_id'] is None
    assert set(frame['sms_by_device']) == {'one', 'two'}
    assert core.worker.account_id == 'b'
    assert all(sms['account_id'] == 'b' for sms in core.worker.sms_cache.values())
    assert 'private prior account' not in repr(core.frames)
    assert len([frame for frame in core.frames if 'instance_id' in frame]) == 1


def test_old_account_filter_queued_after_switch_is_ignored(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'b'})
    core.account = 'b'
    core.worker.request('select_device', {'device_id': 'one', 'account_id': 'a', 'request_id': 'stale'})
    frame = core.run_frame()
    assert core.reads == ['one', 'two']
    assert frame['sms_device_id'] is None and frame['sms_request_id'] is None


def test_filter_generation_is_captured_before_a_selection_race(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    core.worker.device_id, core.worker.device_request_id = 'one', 'first'
    def hook(device, request):
        core.worker.device_id, core.worker.device_request_id = 'two', 'later'
    core.sms_hook = hook
    frame = core.run_frame()
    assert core.reads == ['one']
    assert frame['sms_device_id'] == 'one' and frame['sms_request_id'] == 'first'


def test_stop_during_first_phone_get_prevents_remaining_reads_and_frame(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.sms_hook = lambda device, request: core.worker.stop()
    core.worker.run()
    assert core.reads == ['one']
    assert not core.frames


def test_older_protected_core_needs_no_aggregate_endpoint(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.features.pop('sms_history')
    frame = core.run_frame()
    assert not core.reads and frame['sms_by_device'] == {}


def test_known_account_never_emits_unscoped_or_foreign_cached_legacy_sms(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    core.worker.sms_cache = {'one': {'messages': [{'body': 'unscoped private'}]}, 'two': phone('two', 'b')}
    core.worker.request('select_device', {'device_id': 'one', 'request_id': 'legacy'})
    frame = core.run_frame()
    assert 'unscoped private' not in repr(core.frames)
    assert frame['sms']['account_id'] == 'a'


@pytest.mark.parametrize('source', ['items', 'devices', 'actions', 'sms'])
@pytest.mark.parametrize('nested', [False, True])
def test_inconsistent_source_account_discards_entire_frame(tmp_path, monkeypatch, source, nested):
    core = Core(tmp_path, monkeypatch)
    changed = False
    def data_hook(path, data):
        nonlocal changed
        if path.endswith('/' + source) and not changed:
            changed = True
            if nested:
                key = 'messages' if source == 'sms' else source
                if not data[key]:
                    data[key].append({'account_id': 'b'})
                else:
                    data[key][0]['account_id'] = 'b'
            else:
                data['account_id'] = 'b'
    core.data_hook = data_hook
    frame = core.run_frame()
    assert changed
    assert all(sms['account_id'] == 'a' for sms in frame['sms_by_device'].values())
    assert len([value for value in core.frames if 'instance_id' in value]) == 1
    assert not any(row.get('account_id') == 'b' for row in frame['items'])


def test_mismatched_message_device_discards_frame_and_retries(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    changed = False
    def data_hook(path, data):
        nonlocal changed
        if path.endswith('/sms') and not changed:
            changed = True
            data['messages'][0]['device_id'] = 'wrong-phone'
    core.data_hook = data_hook
    frame = core.run_frame()
    assert changed and core.reads == ['one', 'two', 'one', 'two']
    assert frame['sms_by_device']['one']['messages'][0]['device_id'] == 'one'


def test_queued_refresh_with_captured_old_account_never_posts(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    results = []
    core.worker.result.connect(lambda result: (results.append(result), core.worker.stop()))
    core.worker.request('refresh_sms', {'device_id': 'one', 'account_id': 'b', 'request_id': 'old'})
    core.worker.run()
    assert len(results) == 1 and results[0]['status'] == 409
    assert results[0]['account_id'] == 'b' and 'account changed' in results[0]['error']
    assert not core.reads


def test_scoped_cached_filter_emits_legacy_immediately_with_generation(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    cached = phone('one', body='loaded before')
    core.worker.sms_cache['one'] = cached
    core.worker.request('select_device', {'device_id': 'one', 'account_id': 'a', 'request_id': 'switch'})
    core.run_frame()
    immediate = core.frames[0]
    assert immediate['sms'] == cached
    assert immediate['sms_device_id'] == 'one' and immediate['sms_request_id'] == 'switch'


def test_failed_read_does_not_publish_mismatched_cached_message_accounts(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    core.worker._account({'account_id': 'a'})
    cached = phone('one', body='foreign cached text')
    cached['messages'][0]['account_id'] = 'b'
    core.worker.sms_cache['one'] = cached
    failed = False
    def hook(device, request):
        nonlocal failed
        if device == 'one' and not failed:
            failed = True
            raise httpx.ReadTimeout('private-token', request=request)
    core.sms_hook = hook
    core.run_frame()
    assert failed and core.reads == ['one', 'two', 'one', 'two']
    assert 'foreign cached text' not in repr(core.frames)


def test_stop_during_final_account_check_suppresses_frame(tmp_path, monkeypatch):
    core = Core(tmp_path, monkeypatch)
    def hook(path, data):
        if path.endswith('/capabilities') and len(core.reads) == 2:
            core.worker.stop()
    core.data_hook = hook
    core.worker.run()
    assert core.reads == ['one', 'two'] and not core.frames
