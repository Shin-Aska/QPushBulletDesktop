"""Session mirror of core-owned SQLite preferences; no desktop database access."""
from copy import deepcopy
import threading
import uuid
import weakref
from pbdesk.config import Settings

_lock = threading.RLock()
_states = {}


def preference_path():
    """Legacy import location, used only by migration tools and fixtures.

    :return: Legacy UI preferences JSON path inside the configured state directory.
    :rtype: pathlib.Path
    """
    return Settings.from_env().state_dir / 'ui-preferences.json'


def _key():
    """Return the active application and resolved state directory key tuple.

    :return: Pair of ``(application_dir, resolved_state_dir)`` path strings.
    :rtype: tuple[str, str]
    """
    settings = Settings.from_env()
    return (str(settings.application_dir), str(settings.state_dir.resolve()))


def _state(key=None):
    """Return or initialize the cached preference state dictionary for a key.

    :param key: Optional state key tuple; defaults to the active environment key.
    :type key: tuple[str, str] | None
    :return: Mutable state dictionary holding values, pending edits, and worker state.
    :rtype: dict
    """
    return _states.setdefault(key or _key(), {'values': {}, 'pending': {}, 'requests': {},
        'worker': None, 'ready': False, 'failed': False})


def read_preferences():
    """Return a deep copy of the currently mirrored preference values.

    :return: Copy of local UI preference values.
    :rtype: dict
    """
    with _lock:
        return deepcopy(_state()['values'])


def _send(state):
    """Send pending preference changes to the bound worker when ready.

    :param state: Mutable preference mirror state dictionary for an environment key.
    :type state: dict
    """
    worker = state['worker']() if state['worker'] else None
    pending = dict(state['pending'])
    if (worker is None or not state['ready'] or state['failed'] or not pending
            or pending in state['requests'].values()):
        return
    request_id = str(uuid.uuid4())
    state['requests'][request_id] = pending
    worker.request('preferences_update', {'request_id': request_id, 'changes': pending})


def update_preferences(changes):
    """Merge preference changes locally, queue core persistence, and return updated values.

    :param changes: Mapping of preference names to updated values.
    :type changes: collections.abc.Mapping[str, object]
    :return: Deep copy of the merged local preference values.
    :rtype: dict
    """
    with _lock:
        state = _state()
        state['values'].update(deepcopy(changes))
        state['pending'].update(deepcopy(changes))
        state['failed'] = False
        _send(state)
        return deepcopy(state['values'])


def bind_preferences(worker):
    """Associate a worker with the active state key and listen for update results.

    :param worker: Core worker or compatible object exposing ``request`` and ``result``.
    :type worker: pbdesk.desktop.worker.CoreWorker
    """
    key = _key()
    with _lock:
        _state(key)['worker'] = weakref.ref(worker)
    if hasattr(worker, 'result'):
        worker.result.connect(lambda result: _result(key, result))


def receive_preferences(preferences):
    """Update the local mirror from core preferences while keeping unsent changes.

    :param preferences: Authoritative preference dictionary received from core.
    :type preferences: collections.abc.Mapping[str, object]
    :return: Deep copy of the effective local preference values.
    :rtype: dict
    """
    with _lock:
        state = _state()
        state['values'] = deepcopy(preferences)
        state['values'].update(state['pending'])
        state['ready'] = True
        _send(state)
        return deepcopy(state['values'])


def _result(key, result):
    """Reconcile a core preferences_update response and flush remaining pending edits.

    :param key: Environment state key identifying the target preference mirror.
    :type key: tuple[str, str]
    :param result: Worker result dictionary emitted after a core command completes.
    :type result: dict
    """
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
