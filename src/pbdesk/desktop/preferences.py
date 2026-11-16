"""Session mirror of core-owned SQLite preferences; no desktop database access."""
from copy import deepcopy
import threading
import uuid
import weakref
from pbdesk.config import Settings

_lock = threading.RLock()
_states = {}


def preference_path():
    """Legacy import location, used only by migration tools and fixtures."""
    return Settings.from_env().state_dir / 'ui-preferences.json'


def _key():
    settings = Settings.from_env()
    return (str(settings.application_dir), str(settings.state_dir.resolve()))


def _state(key=None):
    return _states.setdefault(key or _key(), {'values': {}, 'pending': {}, 'requests': {},
        'worker': None, 'ready': False, 'failed': False})


def read_preferences():
    with _lock:
        return deepcopy(_state()['values'])


def _send(state):
    worker = state['worker']() if state['worker'] else None
    pending = dict(state['pending'])
    if (worker is None or not state['ready'] or state['failed'] or not pending
            or pending in state['requests'].values()):
        return
    request_id = str(uuid.uuid4())
    state['requests'][request_id] = pending
    worker.request('preferences_update', {'request_id': request_id, 'changes': pending})


def update_preferences(changes):
    with _lock:
        state = _state()
        state['values'].update(deepcopy(changes))
        state['pending'].update(deepcopy(changes))
        state['failed'] = False
        _send(state)
        return deepcopy(state['values'])


def bind_preferences(worker):
    key = _key()
    with _lock:
        _state(key)['worker'] = weakref.ref(worker)
    if hasattr(worker, 'result'):
        worker.result.connect(lambda result: _result(key, result))


def receive_preferences(preferences):
    with _lock:
        state = _state()
        state['values'] = deepcopy(preferences)
        state['values'].update(state['pending'])
        state['ready'] = True
        _send(state)
        return deepcopy(state['values'])


def _result(key, result):
    if result.get('command') != 'preferences_update':
        return
    with _lock:
        state = _state(key)
        sent = state['requests'].pop(result.get('request_id'), None)
        if sent is None:
            return
        if result.get('error'):
            state['failed'] = True
            return
        for name, value in sent.items():
            if state['pending'].get(name) == value:
                state['pending'].pop(name, None)
        preferences = result.get('data', {}).get('preferences')
        if isinstance(preferences, dict):
            state['values'] = deepcopy(preferences)
            state['values'].update(state['pending'])
        _send(state)
