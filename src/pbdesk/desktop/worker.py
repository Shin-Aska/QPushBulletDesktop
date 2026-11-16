"""Desktop lifecycle and HTTP work. No network request runs on the GUI thread."""
import queue
import threading
import time

import httpx
from PyQt6.QtCore import QThread, pyqtSignal
from pbdesk.config import Settings
from pbdesk.credentials import service_token, health_headers, verify_health
from pbdesk.media_errors import IMAGE_ERRORS
from pbdesk.services.client_lifecycle import ClientLease

CORE_COMMANDS = {"core_settings", "core_settings_update", "core_restart", "configuration_get", "configure_provider", "preferences_update"}
MUTATIONS = {"core_settings_update", "core_restart", "web", "shutdown", "refresh_devices", "reload_provider", "create_action", "edit_action", "approve_action", "cancel_action", "refresh_sms", "add_device", "rename_device", "remove_device", "encryption", "test_encryption", "resolve_registration"}


class AccountChangedError(ValueError):
    """A queued action belongs to a different credential account."""


class CoreWorker(QThread):
    snapshot = pyqtSignal(dict)
    connection = pyqtSignal(str)
    failed = pyqtSignal(str)
    result = pyqtSignal(dict)
    stopped_all = pyqtSignal()

    @staticmethod
    def _media_error(exc):
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
        if action == 'preferences_update':
            with self._preference_lock:
                self._pending_preferences.update(value['changes'])
        self.commands.put((action, value))

    def stop(self):
        self.stop_event.set()
        self.commands.put(("wake", None))
        if self.lease and self.instance_id is None:
            self.lease.stop_event.set()

    def _account(self, data):
        if 'account_id' not in data:
            return
        account_id = data['account_id']
        changed = self.account_known and account_id != self.account_id
        if not self.account_known or account_id != self.account_id:
            self.sms_cache.clear()
            self.account_id = account_id
            self.account_known = True
        if changed:
            # Clear prior-account client memory even if the next data GET fails.
            self.snapshot.emit({'capabilities': data, 'devices': {'account_id': account_id, 'devices': []}, 'items': [], 'actions': []})

    @staticmethod
    def _consistent_account(capabilities, *parts):
        if 'account_id' not in capabilities:
            return True  # Older cores have no new private-history surfaces.
        account_id = capabilities['account_id']
        return all(part is None or ('account_id' in part and part['account_id'] == account_id) for part in parts)

    def _identity(self, client):
        proof = health_headers(self.settings, "desktop")
        response = client.get("/api/v1/health", headers=proof)
        response.raise_for_status()
        identity = response.json()
        if not verify_health(self.settings, "desktop", proof, identity):
            raise ValueError("Port conflict: unverified core identity. Check PBDESK_CORE_PORT.")
        if not identity.get("ready"):
            raise ValueError("The private core is not ready. Check pbdesk-diagnostics.")
        return identity

    def _mutate(self, client, headers, command, value):
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
        if command in {"create_action", "edit_action", "approve_action", "cancel_action"} and "account_id" in value:
            # Credentials may reload without changing the core instance identity.
            response = client.get("/api/v1/capabilities", headers=headers)
            response.raise_for_status()
            capabilities = response.json()
            self._account(capabilities)
            self.features = capabilities.get("features", {})
            if capabilities.get("account_id") != value["account_id"]:
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
                    self.device_id = value.get("device_id")
                    self.device_request_id = value.get("request_id")
                    if self.device_id in self.sms_cache:
                        self.snapshot.emit({"sms": self.sms_cache[self.device_id], "sms_device_id": self.device_id, "sms_request_id": self.device_request_id})
                    next_poll = 0
                if command == "search":
                    self.query = value
                    next_poll = 0
                if command not in MUTATIONS and command not in CORE_COMMANDS and command != "media_image" and time.monotonic() < next_poll:
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
                if identity['instance_id'] != self.instance_id:
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
                        response = client.get(path, headers=authorization, **kwargs)
                        response.raise_for_status()
                        return response.json()
                    capabilities = get("/api/v1/capabilities")
                    self._account(capabilities)
                    items = get("/api/v1/items", params={"q": self.query})
                    web = get("/api/v1/web")
                    features = capabilities.get("features", {})
                    configuration = get('/api/v1/configuration') if features.get('configuration') else None
                    self.features = features
                    devices = get("/api/v1/devices") if features.get("devices") else {"devices": [], "synced_at": None, "account_id": capabilities.get("account_id")}
                    actions = get("/api/v1/actions") if features.get("send") else {"actions": [], "account_id": capabilities.get("account_id")}
                    sms = None
                    selected_device = self.device_id
                    if selected_device and features.get("sms_history"):
                        sms = get("/api/v1/sms", params={"device_id": selected_device})
                    if not self._consistent_account(capabilities, items, devices, actions, sms):
                        # A credential reload may happen between independent local GETs.
                        # Never publish rows assembled across two provider accounts.
                        self.connection.emit('Account changed · refreshing local view…')
                        next_poll = time.monotonic() + 0.1
                        continue
                    if sms is not None:
                        self.sms_cache[selected_device] = sms
                    self.snapshot.emit({"configuration": configuration, "sms": sms, "sms_device_id": selected_device, "sms_request_id": self.device_request_id, "items": items["items"], "web": web, "capabilities": capabilities, "devices": devices, "actions": actions["actions"], "instance_id": identity["instance_id"]})
                    if self.pending_restart and identity['instance_id'] != self.pending_restart['instance_id']:
                        self.pending_restart = None
                    self.connection.emit("Restarting core · reconnecting…" if self.pending_restart else "Connected to private core")
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    self.connection.emit("Disconnected · waiting for core")
                    self.failed.emit(f"Core refresh failed: {exc}. Retained content remains visible; use Refresh to try again.")
                next_poll = time.monotonic() + (.2 if self.pending_restart else 2)
