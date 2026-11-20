"""Desktop lifecycle and HTTP work. No network request runs on the GUI thread."""
import queue
import os
from pathlib import Path
import threading
import time

import httpx
from PyQt6.QtCore import QThread, pyqtSignal
from pbdesk.config import Settings
from pbdesk.credentials import service_token, health_headers, verify_health
from pbdesk.media_errors import IMAGE_ERRORS
from pbdesk.services.client_lifecycle import ClientLease
from pbdesk.storage.cipher import sync_directory

HISTORY_COMMANDS = {"history_protect", "history_unlock", "history_migrate", "history_recover", "history_save", "history_finish", "history_status"}

CORE_COMMANDS = {"core_settings", "core_settings_update", "core_restart", "configuration_get", "configure_provider", "preferences_update"}
MUTATIONS = {"core_settings_update", "core_restart", "web", "shutdown", "refresh_devices", "reload_provider", "create_action", "edit_action", "approve_action", "cancel_action", "refresh_sms", "add_device", "rename_device", "remove_device", "encryption", "test_encryption", "resolve_registration"}


class AccountChangedError(ValueError):
    """A queued action belongs to a different credential account."""


class PollStopped(Exception):
    """Stop a multi-device cached read between local requests."""


class CoreWorker(QThread):
    """Background Qt thread managing private core polling and HTTP commands."""
    history_security = pyqtSignal(dict)
    snapshot = pyqtSignal(dict)
    connection = pyqtSignal(str)
    failed = pyqtSignal(str)
    result = pyqtSignal(dict)
    stopped_all = pyqtSignal()

    @staticmethod
    def _media_error(exc):
        """Translate a media fetch exception into a structured error dictionary.

        :param exc: Exception raised while fetching or decoding a media image.
        :type exc: Exception
        :return: Dictionary containing `error_code`, user-facing `error`, and HTTP `status`.
        :rtype: dict[str, str | int | None]
        """
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        if isinstance(exc, AccountChangedError):
            code = 'account_changed'
        elif isinstance(exc, httpx.TimeoutException):
            code = 'timeout'
        elif isinstance(exc, httpx.HTTPStatusError):
            code = {401: 'access_denied', 403: 'access_denied', 404: 'not_found',
                409: 'account_changed', 410: 'unavailable', 413: 'too_large',
                415: 'format', 422: 'source_blocked', 502: 'download_failed',
                504: 'timeout'}.get(status, 'unknown')
        elif isinstance(exc, httpx.HTTPError):
            code = 'core_unavailable'
        else:
            code = 'decode_failed'
        return {'error_code': code, 'error': IMAGE_ERRORS[code], 'status': status}

    def __init__(self, settings: Settings, demo=False):
        """Initialize the core worker thread and synchronization state.

        :param settings: Desktop configuration settings.
        :type settings: Settings
        :param demo: Whether to seed a sanitized demo fixture in a newly started core.
        :type demo: bool
        """
        super().__init__()
        self.settings = settings
        self.demo = demo
        self.lease = None
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.query = ""
        self.device_id = None
        self.sms_cache = {}
        self.features = {}
        self.instance_id = None
        self.device_request_id = None
        self.account_id = None
        self.account_known = False
        self.pending_restart = None
        self._preference_lock = threading.Lock()
        self._pending_preferences = {}

    def request(self, action, value=None):
        """Enqueue a worker command and track pending preference updates.

        :param action: Worker command or mutation name to enqueue.
        :type action: str
        :param value: Optional command payload, search query, or toggle value.
        :type value: dict | str | bool | None
        """
        if action == 'preferences_update':
            with self._preference_lock:
                self._pending_preferences.update(value['changes'])
        self.commands.put((action, value))

    def stop(self):
        """Signal the worker thread and active core lease to stop."""
        self.stop_event.set()
        self.commands.put(("wake", None))
        if self.lease:
            self.lease.stop_event.set()

    def _account(self, data):
        """Update tracked account state and clear stale data when the account changes.

        :param data: Capabilities payload potentially containing an `account_id`.
        :type data: dict
        """
        if 'account_id' not in data:
            return
        account_id = data['account_id']
        changed = self.account_known and account_id != self.account_id
        if not self.account_known or account_id != self.account_id:
            self.sms_cache.clear()
            self.account_id = account_id
            self.account_known = True
        if changed:
            self.device_id = None
            self.device_request_id = None
            # Clear prior-account client memory even if the next data GET fails.
            self.snapshot.emit({'capabilities': data, 'devices': {'account_id': account_id, 'devices': []},
                'items': [], 'actions': [], 'sms_by_device': {}, 'sms': None,
                'sms_device_id': None, 'sms_request_id': None})

    @staticmethod
    def _consistent_account(capabilities, *parts):
        """Return whether all response sections belong to the capabilities account.

        :param capabilities: Core capabilities dictionary defining the expected account.
        :type capabilities: dict
        :param parts: Response section dictionaries to verify against the active account.
        :type parts: dict | None
        :return: `True` if every section matches the capabilities account, otherwise `False`.
        :rtype: bool
        """
        if 'account_id' not in capabilities:
            return True  # Older cores have no new private-history surfaces.
        account_id = capabilities['account_id']
        return all(part is None or ('account_id' in part and part['account_id'] == account_id
            and all('account_id' not in row or row['account_id'] == account_id
                for key in ('devices', 'items', 'actions', 'messages') for row in part.get(key, [])))
            for part in parts)

    def _sms_targets(self, devices, actions, selected_device, account_id):
        """Return sorted device IDs to poll for cached SMS history.

        :param devices: Devices response dictionary containing cached device records.
        :type devices: dict
        :param actions: Outbox actions response dictionary containing pending SMS items.
        :type actions: dict
        :param selected_device: Currently selected phone device ID, or `None` for all.
        :type selected_device: str | None
        :param account_id: Active account identifier used to filter cached SMS snapshots.
        :type account_id: str | int | None
        :return: Sorted list of target device IDs to query for SMS history.
        :rtype: list[str]
        """
        if selected_device:
            return [selected_device]
        targets = {row['id'] for row in devices['devices']
            if row.get('id') and (row.get('can_sms') or row.get('history', {}).get('count'))}
        targets.update(device for device, sms in self.sms_cache.items()
            if sms.get('account_id') == account_id)
        targets.update(row['device_id'] for row in actions['actions']
            if row.get('kind') == 'sms' and row.get('device_id'))
        return sorted(targets)

    def _read_sms(self, get, targets, account_id):
        """Read the existing per-phone cache API; failures preserve that phone only.

        :param get: Helper callable performing authenticated GET requests to core.
        :type get: collections.abc.Callable
        :param targets: Phone device IDs to query for cached SMS history.
        :type targets: list[str]
        :param account_id: Active account identifier for fallback cache validation.
        :type account_id: str | int | None
        :return: Mapping of device ID to SMS snapshot or fallback error dictionary.
        :rtype: dict[str, dict]
        :raises PollStopped: If the worker stop event is set during polling.
        """
        snapshots = {}
        for device in targets:
            if self.stop_event.is_set():
                raise PollStopped()
            try:
                snapshots[device] = get('/api/v1/sms', params={'device_id': device})
            except (httpx.HTTPError, ValueError, KeyError):
                previous = self.sms_cache.get(device, {})
                if previous.get('account_id') != account_id:
                    previous = {'account_id': account_id, 'messages': [],
                        'sync': {'state': 'idle', 'last_success_at': None, 'error': None, 'has_more': False}}
                snapshots[device] = {**previous,
                    'read_error': 'Cached SMS could not be read. Retained messages are shown; retry after reconnection.'}
        return snapshots

    @classmethod
    def _consistent_sms(cls, capabilities, snapshots):
        """Return whether per-device SMS snapshots match the account and device IDs.

        :param capabilities: Core capabilities dictionary defining the expected account.
        :type capabilities: dict
        :param snapshots: Per-device SMS snapshot dictionaries keyed by device ID.
        :type snapshots: dict[str, dict]
        :return: `True` if all snapshots and messages match the account and device IDs.
        :rtype: bool
        """
        if not cls._consistent_account(capabilities, *snapshots.values()):
            return False
        account_id = capabilities.get('account_id')
        return all(('account_id' not in row or row['account_id'] == account_id)
            and ('device_id' not in row or row['device_id'] == device)
            for device, sms in snapshots.items() for row in sms.get('messages', []))

    def _identity(self, client):
        """Verify the core health endpoint, history encryption, and readiness.

        :param client: Synchronous HTTP client connected to the private core.
        :type client: httpx.Client
        :return: Verified core health identity dictionary.
        :rtype: dict
        :raises ValueError: If core identity verification fails, history encryption is disabled, or core is not ready.
        """
        proof = health_headers(self.settings, "desktop")
        response = client.get("/api/v1/health", headers=proof)
        response.raise_for_status()
        identity = response.json()
        if not verify_health(self.settings, "desktop", proof, identity):
            raise ValueError("Port conflict: unverified core identity. Check PBDESK_CORE_PORT.")
        if identity.get('history_encryption') != 1:
            raise ValueError('Restart the existing core to enable mandatory encrypted history. The running service has been left unchanged.')
        if not identity.get("ready"):
            raise ValueError("The private core is not ready. Check pbdesk-diagnostics.")
        return identity

    def _history_request(self, client, headers, command, value):
        """Execute a history security request or recovery key file export.

        :param client: Synchronous HTTP client connected to the private core.
        :type client: httpx.Client
        :param headers: Authorization headers for core requests.
        :type headers: dict[str, str]
        :param command: History security command name to perform.
        :type command: str
        :param value: Optional arguments such as recovery key or export file path.
        :type value: dict | None
        :return: Response JSON from core, or `{'saved': True}` after exporting a key.
        :rtype: dict
        """
        value = value or {}
        if command == 'history_status':
            response = client.get('/api/v1/history/security', headers=headers)
        elif command == 'history_save':
            response = client.post('/api/v1/history/recovery-key', headers=headers, timeout=120)
            response.raise_for_status()
            key = response.json()['recovery_key']
            # An explicit user-chosen file is the only export destination. Never
            # overwrite or follow links, and never send the key to GUI signals.
            fd = os.open(value['path'], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(key + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                sync_directory(Path(value['path']).parent)
            finally:
                key = None
            return {'saved': True}
        else:
            suffix = {'history_protect': 'protect', 'history_unlock': 'unlock', 'history_migrate': 'migrate',
                      'history_recover': 'recover', 'history_finish': 'recovery-offer'}[command]
            payload = None
            if command == 'history_recover':
                key = value.get('recovery_key')
                if value.get('path'):
                    with Path(value['path']).open() as stream:
                        key = stream.read(16385).strip()
                payload = {'recovery_key': key}
            elif command == 'history_finish':
                payload = {'saved': value['saved']}
            response = client.post('/api/v1/history/' + suffix, json=payload, headers=headers, timeout=180)
        response.raise_for_status()
        return response.json()

    def _mutate(self, client, headers, command, value):
        """Send a configuration, device, action, or lifecycle mutation to core.

        :param client: Synchronous HTTP client connected to the private core.
        :type client: httpx.Client
        :param headers: Authorization headers for core requests.
        :type headers: dict[str, str]
        :param command: Mutation or configuration command name.
        :type command: str
        :param value: Command payload dictionary, toggle flag, or `None`.
        :type value: dict | bool | None
        :return: Decoded JSON response from the core endpoint.
        :rtype: dict
        :raises ValueError: If the preference update response payload is invalid.
        :raises AccountChangedError: If the active account changed before SMS refresh or action mutation.
        """
        if command == 'configuration_get':
            response = client.get('/api/v1/configuration', headers=headers)
            response.raise_for_status()
            return response.json()
        if command in {'configure_provider', 'preferences_update'}:
            path = '/api/v1/configuration/' + ('provider' if command == 'configure_provider' else 'preferences')
            payload = {key: field for key, field in value.items() if key != 'request_id'} if command == 'configure_provider' else value['changes']
            method = 'POST' if command == 'configure_provider' else 'PATCH'
            response = client.request(method, path, json=payload, headers=headers,
                timeout=httpx.Timeout(85 if command == 'configure_provider' else 4, connect=2))
            response.raise_for_status()
            data = response.json()
            if command == 'preferences_update':
                if not isinstance(data, dict) or not isinstance(data.get('preferences'), dict):
                    raise ValueError('Invalid preference response')
                with self._preference_lock:
                    for name, field in payload.items():
                        if self._pending_preferences.get(name) == field:
                            self._pending_preferences.pop(name, None)
            return data
        if command in {"create_action", "edit_action", "approve_action", "cancel_action", "refresh_sms"} and "account_id" in value:
            # Credentials may reload without changing the core instance identity.
            response = client.get("/api/v1/capabilities", headers=headers)
            response.raise_for_status()
            capabilities = response.json()
            self._account(capabilities)
            self.features = capabilities.get("features", {})
            if capabilities.get("account_id") != value["account_id"]:
                if command == 'refresh_sms':
                    raise AccountChangedError('The account changed before SMS refresh. Review the current devices before refreshing.')
                raise AccountChangedError("The account changed before this action could be saved. Review message activity in the current account; the original conversation draft is kept.")
        method, payload = "POST", None
        if command == "core_settings_update":
            method, path = "PATCH", "/api/v1/core/settings"
            payload = {key: value[key] for key in ("expected_instance_id", "notifications_enabled")}
        elif command == "core_restart":
            path = "/api/v1/core/restart"
            payload = {key: value[key] for key in ("expected_instance_id", "request_id")}
        elif command == "web":
            path, payload = "/api/v1/web", {"enabled": bool(value)}
        elif command == "shutdown":
            path = "/api/v1/shutdown"
        elif command == "refresh_devices":
            path = "/api/v1/devices/refresh"
        elif command == "reload_provider":
            path = "/api/v1/provider/reload"
        elif command == "refresh_sms":
            path, payload = "/api/v1/sms/refresh", {"device_id": value["device_id"]}
        elif command == "resolve_registration":
            path, payload = "/api/v1/devices/registration/resolve", {"device_id": value.get("device_id"), "acknowledge_unknown_outcome": True}
        elif command == "add_device":
            path, payload = "/api/v1/devices", {"nickname": value["nickname"]}
        elif command in {"rename_device", "remove_device", "encryption", "test_encryption"}:
            from urllib.parse import quote
            path = "/api/v1/devices/" + quote(value["device_id"], safe="")
            if command == "rename_device":
                method, payload = "PATCH", {"nickname": value["nickname"]}
            elif command == "remove_device":
                method = "DELETE"
            elif command == "encryption":
                path += "/encryption"
                payload = {key: value[key] for key in ("mode", "entry", "password") if key in value}
            else:
                path += "/encryption/test"
        elif command == "create_action":
            path, payload = "/api/v1/actions", value["payload"]
        else:
            action_id = int(value["action_id"])
            if command == "edit_action":
                method, path = "PATCH", f"/api/v1/actions/{action_id}"
            else:
                suffix = "approve" if command == "approve_action" else "cancel"
                path = f"/api/v1/actions/{action_id}/{suffix}"
            payload = value["payload"]
        response = client.request(method, path, json=payload, headers=headers, timeout=httpx.Timeout(20, connect=2))
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _mutation_error(exc, command="create_action"):
        """Return a user-safe message, ambiguity flag, and HTTP status for a mutation error.

        :param exc: Exception raised during the mutation request.
        :type exc: Exception
        :param command: Command name associated with the failed request.
        :type command: str
        :return: Tuple of `(message, ambiguous, status_code)`.
        :rtype: tuple[str, bool, int | None]
        """
        if isinstance(exc, AccountChangedError):
            return str(exc), False, 409
        if command in CORE_COMMANDS:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if status == 404:
                return "Core settings require an updated core. Restart the existing core process once, then refresh settings.", False, status
            if isinstance(exc, httpx.HTTPStatusError) and status < 500 and status != 408:
                try:
                    detail = exc.response.json().get("detail")
                except ValueError:
                    detail = None
                return detail if isinstance(detail, str) else "The core rejected this settings change. Refresh settings before trying again.", False, status
            return "The core could not confirm this operation. Wait for reconnection and refresh settings; the request will not be repeated automatically.", True, status
        if command not in {"create_action", "edit_action", "approve_action", "cancel_action"}:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            detail = None
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    detail = exc.response.json().get('detail')
                except ValueError:
                    pass
            ambiguous = status is None or status >= 500 or status == 408 or (isinstance(detail, dict) and detail.get('state') == 'uncertain')
            if command == 'add_device' and ambiguous:
                return 'Registration outcome is uncertain. Refresh Devices and use Review uncertain registration before attempting to add this computer again.', True, status
            # Never reflect request-validation input or credential/provider exception text.
            return "The device or service operation could not be confirmed. Refresh its current state before trying again; cached history is kept.", ambiguous, status
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status >= 500 or status == 408:
                return "The core could not confirm the result. Review message activity; reconcile creation only with the exact original request ID and payload.", True, status
            try:
                detail = exc.response.json().get("detail")
            except ValueError:
                detail = None
            # Validation arrays can contain the user's text; keep the error concise.
            message = detail if isinstance(detail, str) else "The core rejected the request. Check the selected device and fields."
            if status == 409:
                message = "The action changed or is no longer editable. Review its refreshed state before continuing."
            return str(message)[:1000], False, status
        return "The result could not be confirmed. Refresh message activity before taking another action. A create request can be reconciled using the same request ID; sending is never automatically retried.", True, None

    def run(self):
        """Execute the worker client loop and clean up preferences and lease on exit."""
        try:
            self._run_client()
        finally:
            try:
                self._flush_preferences()
            finally:
                if self.lease:
                    self.lease.close()

    def _flush_preferences(self):
        """Save queued UI choices before releasing the last desktop lease."""
        with self._preference_lock:
            changes = dict(self._pending_preferences)
        if not changes or self.instance_id is None or not self.features.get('configuration'):
            return
        try:
            with httpx.Client(base_url=self.settings.core_url, timeout=1,
                    trust_env=False, follow_redirects=False) as client:
                self._identity(client)
                response = client.patch('/api/v1/configuration/preferences', json=changes,
                    headers={'Authorization': 'Bearer ' + service_token(self.settings, 'desktop')})
                response.raise_for_status()
        except Exception:
            pass

    def _run_client(self):
        """Run the core lease lifecycle, command dispatch, and snapshot polling loop.

        :raises AccountChangedError: If the active account changes mid-request during media fetch (handled internally).
        :raises ValueError: If a fetched media payload fails size or raster decoding checks (handled internally).
        :raises PollStopped: If the worker stop event is set during snapshot polling (handled internally).
        """
        if self.stop_event.is_set():
            return
        self.connection.emit("Starting private core…")
        try:
            self.lease = ClientLease(self.settings, demo=self.demo)
            if self.stop_event.is_set():
                self.lease.stop_event.set()
            self.lease.start()
            token = service_token(self.settings, "desktop")
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        with httpx.Client(base_url=f"http://{self.settings.core_host}:{self.settings.core_port}", timeout=2, trust_env=False, follow_redirects=False) as client:
            authorization = {"Authorization": f"Bearer {token}"}
            next_poll = 0.0
            while not self.stop_event.is_set():
                try:
                    command, value = self.commands.get(timeout=max(0.01, min(0.2, next_poll - time.monotonic())))
                except queue.Empty:
                    command, value = "poll", None
                if self.stop_event.is_set():
                    break
                if command == "select_device":
                    value = value if isinstance(value, dict) else {'device_id': value}
                    if 'account_id' in value and value['account_id'] != self.account_id:
                        continue
                    self.device_id = value.get("device_id")
                    self.device_request_id = value.get("request_id")
                    if self.device_id in self.sms_cache:
                        cached = self.sms_cache[self.device_id]
                        # Untagged legacy caches are usable only before an
                        # account-aware core has established the current scope.
                        if ((not self.account_known and 'account_id' not in cached)
                                or self._consistent_sms({'account_id': self.account_id}, {self.device_id: cached})):
                            self.snapshot.emit({"sms": cached, "sms_device_id": self.device_id, "sms_request_id": self.device_request_id})
                    next_poll = 0
                if command == "search":
                    self.query = value
                    next_poll = 0
                if command not in MUTATIONS and command not in CORE_COMMANDS and command not in HISTORY_COMMANDS and command != "media_image" and time.monotonic() < next_poll:
                    continue
                request_id = value.get("request_id") if isinstance(value, dict) else None
                origin = {"account_id": value["account_id"]} if isinstance(value, dict) and "account_id" in value else {}
                try:
                    identity = self._identity(client)
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    self.connection.emit("Disconnected · waiting for core")
                    detail = self.lease.error if self.lease and self.lease.error else str(exc)
                    message = f"Core connection failed: {detail}. Recovery continues while this client remains open; relaunch after Stop All."
                    if not self.pending_restart:
                        self.failed.emit(message)
                    if command in MUTATIONS or command in CORE_COMMANDS or command == "media_image":
                        self.result.emit({**origin, "command": command, "request_id": request_id, "error": message, "ambiguous": False, "status": None})
                    next_poll = time.monotonic() + (.2 if self.pending_restart else 2)
                    continue
                if command in HISTORY_COMMANDS:
                    try:
                        data = self._history_request(client, authorization, command, value)
                        self.result.emit({'command': command, 'request_id': request_id, 'data': data})
                    except (httpx.HTTPError, OSError, ValueError, KeyError):
                        self.result.emit({'command': command, 'request_id': request_id,
                            'error': 'The operation could not be confirmed. Check KWallet authorization or the selected recovery file, then retry. Refreshing reconciles an interrupted request.'})
                    next_poll = 0
                    continue
                try:
                    if identity.get('history_ready'):
                        security = {'history_ready': True}
                    else:
                        response = client.get('/api/v1/history/security', headers=authorization)
                        response.raise_for_status()
                        security = response.json()
                    self.history_security.emit(security)
                except (httpx.HTTPError, ValueError):
                    self.failed.emit('Encrypted history status is unavailable. Retry after checking the private core.')
                    next_poll = time.monotonic() + 2
                    continue
                if not security.get('history_ready'):
                    self.instance_id = identity['instance_id']
                    if command in MUTATIONS or command in CORE_COMMANDS or command == 'media_image':
                        self.result.emit({'command': command, 'request_id': request_id,
                            'error': 'Unlock and protect local history before continuing.'})
                    next_poll = time.monotonic() + 2
                    continue
                if identity['instance_id'] != self.instance_id or not self.features:
                    self.instance_id = identity['instance_id']
                    if self.lease:
                        self.lease.wake()
                    try:
                        response = client.get('/api/v1/capabilities', headers=authorization)
                        response.raise_for_status()
                        capability_data = response.json()
                        self._account(capability_data)
                        self.features = capability_data.get('features', {})
                    except (httpx.HTTPError, ValueError):
                        self.features = {}
                if command in CORE_COMMANDS:
                    required = ('configuration' if command in {'configuration_get', 'configure_provider', 'preferences_update'}
                        else 'core_restart' if command == 'core_restart' else 'core_settings')
                    if not self.features.get(required):
                        self.result.emit({'command': command, 'request_id': request_id, 'status': None, 'ambiguous': False,
                            'error': 'Core settings require an updated core. Restart the existing core process once, then refresh settings.'})
                        continue
                    if command == 'core_restart' and self.pending_restart:
                        pending = self.pending_restart
                        if value.get('expected_instance_id') == pending['instance_id'] and request_id == pending['request_id'] and pending.get('confirmed'):
                            self.result.emit({'command': command, 'request_id': request_id, 'data': {'accepted': True, 'instance_id': pending['instance_id'], 'request_id': pending['request_id']}})
                        else:
                            self.result.emit({'command': command, 'request_id': request_id, 'status': 409, 'ambiguous': False,
                                'error': 'A core restart is already in progress; wait for reconnection.'})
                        continue
                    try:
                        if command == 'core_settings':
                            response = client.get('/api/v1/core/settings', headers=authorization)
                            response.raise_for_status()
                            data = response.json()
                            pending = self.pending_restart
                            if (pending and not pending.get('confirmed') and
                                    time.monotonic() - pending.get('started', time.monotonic()) >= 20 and
                                    identity['instance_id'] == pending['instance_id'] == data.get('instance_id') and
                                    data.get('restarting') is False):
                                # A later requested read reconciles a lost POST response.
                                # Only a fresh explicit action may send another restart.
                                self.pending_restart = None
                        else:
                            data = self._mutate(client, authorization, command, value)
                        if command == 'core_restart':
                            self.pending_restart = {'instance_id': identity['instance_id'], 'request_id': request_id, 'confirmed': True, 'started': time.monotonic()}
                            self.connection.emit('Restarting core · reconnecting…')
                            if self.lease:
                                self.lease.wake()
                        self.result.emit({'command': command, 'request_id': request_id, 'data': data})
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        message, ambiguous, status = self._mutation_error(exc, command)
                        if command == 'core_restart' and ambiguous:
                            self.pending_restart = {'instance_id': identity['instance_id'], 'request_id': request_id, 'confirmed': False, 'started': time.monotonic()}
                        self.result.emit({'command': command, 'request_id': request_id, 'error': message,
                            'ambiguous': ambiguous, 'status': status})
                    next_poll = 0
                    continue
                if command == 'media_image':
                    try:
                        response = client.get('/api/v1/capabilities', headers=authorization)
                        response.raise_for_status()
                        capabilities = response.json()
                        self._account(capabilities)
                        if capabilities.get('account_id') != value.get('account_id'):
                            raise AccountChangedError('Account changed')
                        if not capabilities.get('features', {}).get('media_images'):
                            self.result.emit({**origin, 'command': command, 'request_id': request_id,
                                'error_code': 'core_update', 'error': IMAGE_ERRORS['core_update']})
                            continue
                        params = {key: value[key] for key in ('account_id', 'kind', 'item_id', 'message_id', 'index') if key in value}
                        response = client.get('/api/v1/media/image', params=params, headers=authorization, timeout=httpx.Timeout(14, connect=2))
                        response.raise_for_status()
                        # Decode the bounded raster on this worker thread. QImage
                        # crosses Qt signals safely; only QPixmap creation is GUI work.
                        import base64
                        from .media import decode_image, MAX_IMAGE_BYTES
                        data = response.json()
                        encoded = data.pop('data', None)
                        if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                            raise ValueError('Image preview limit')
                        image = decode_image(base64.b64decode(encoded, validate=True))
                        if image is None:
                            raise ValueError('Image raster unavailable')
                        self.result.emit({**origin, 'command': command, 'request_id': request_id,
                            'data': {'account_id': data.get('account_id'), 'image': image}})
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        self.result.emit({**origin, 'command': command, 'request_id': request_id,
                            **self._media_error(exc)})
                    continue
                required = ('sms_history' if command == 'refresh_sms' else 'device_management' if command in {'add_device', 'rename_device', 'remove_device', 'encryption', 'test_encryption', 'resolve_registration'} else None)
                if required and not self.features.get(required):
                    self.result.emit({**origin, 'command': command, 'request_id': request_id, 'error': 'Core update ready · restart the shared core to use SMS history and device management. Stored history is kept.', 'ambiguous': False, 'status': None})
                    next_poll = 0
                    continue
                if command in MUTATIONS:
                    try:
                        data = self._mutate(client, authorization, command, value)
                        self.result.emit({**origin, "command": command, "request_id": request_id, "device_id": value.get("device_id") if isinstance(value, dict) else None, "data": data})
                        if command == "shutdown":
                            self.stopped_all.emit()
                            return
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        message, ambiguous, status = self._mutation_error(exc, command)
                        self.result.emit({**origin, "command": command, "request_id": request_id, "error": message, "ambiguous": ambiguous, "status": status})
                    next_poll = 0
                try:
                    def get(path, **kwargs):
                        """Fetch and decode JSON from a core endpoint unless stopping.

                        :param path: Relative core API endpoint path.
                        :type path: str
                        :param kwargs: Additional keyword arguments forwarded to `client.get`.
                        :type kwargs: Any
                        :return: Decoded JSON response from the core endpoint.
                        :rtype: dict
                        :raises PollStopped: If the worker stop event is set before performing the request.
                        """
                        if self.stop_event.is_set():
                            raise PollStopped()
                        response = client.get(path, headers=authorization, **kwargs)
                        response.raise_for_status()
                        return response.json()
                    capabilities = get("/api/v1/capabilities")
                    self._account(capabilities)
                    selected_device = self.device_id
                    sms_request_id = self.device_request_id
                    items = get("/api/v1/items", params={"q": self.query})
                    web = get("/api/v1/web")
                    features = capabilities.get("features", {})
                    configuration = get('/api/v1/configuration') if features.get('configuration') else None
                    self.features = features
                    devices = get("/api/v1/devices") if features.get("devices") else {"devices": [], "synced_at": None, "account_id": capabilities.get("account_id")}
                    actions = get("/api/v1/actions") if features.get("send") else {"actions": [], "account_id": capabilities.get("account_id")}
                    if not self._consistent_account(capabilities, items, devices, actions):
                        self.connection.emit('Account changed · refreshing local view…')
                        self.sms_cache.clear()
                        next_poll = time.monotonic() + 0.1
                        continue
                    sms_by_device = {}
                    if features.get("sms_history"):
                        targets = self._sms_targets(devices, actions, selected_device, capabilities.get('account_id'))
                        sms_by_device = self._read_sms(get, targets, capabilities.get('account_id'))
                    # A failed phone GET has no account proof. Recheck even when
                    # every successful earlier read belonged to the old account.
                    final_capabilities = get('/api/v1/capabilities')
                    if self.stop_event.is_set():
                        raise PollStopped()
                    if (not self._consistent_account(capabilities, items, devices, actions, final_capabilities)
                            or not self._consistent_sms(capabilities, sms_by_device)):
                        # A credential reload may happen between independent local GETs.
                        # Never publish rows assembled across two provider accounts.
                        self.connection.emit('Account changed · refreshing local view…')
                        self.sms_cache.clear()
                        self._account(final_capabilities)
                        next_poll = time.monotonic() + 0.1
                        continue
                    self.sms_cache.update(sms_by_device)
                    sms = sms_by_device.get(selected_device)
                    self.snapshot.emit({"configuration": configuration, "sms": sms, "sms_by_device": sms_by_device, "sms_device_id": selected_device, "sms_request_id": sms_request_id, "items": items["items"], "web": web, "capabilities": capabilities, "devices": devices, "actions": actions["actions"], "instance_id": identity["instance_id"]})
                    if self.pending_restart and identity['instance_id'] != self.pending_restart['instance_id']:
                        self.pending_restart = None
                    self.connection.emit("Restarting core · reconnecting…" if self.pending_restart else "Connected to private core")
                except PollStopped:
                    break
                except (httpx.HTTPError, ValueError, KeyError):
                    self.connection.emit("Disconnected · waiting for core")
                    self.failed.emit("Core refresh failed. Retained content remains visible; use Refresh to try again.")
                next_poll = time.monotonic() + (.2 if self.pending_restart else 2)
