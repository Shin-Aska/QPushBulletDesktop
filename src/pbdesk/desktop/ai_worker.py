"""Cancellable AI-only local client; never launches or restarts core."""
import asyncio
import threading
import uuid
import httpx
from PyQt6.QtCore import QThread, pyqtSignal
from pbdesk.credentials import service_token, health_headers, verify_health


class AiWorker(QThread):
    result = pyqtSignal(dict)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._loop = None
        self._queue = None
        self._pending = []
        self._lock = threading.Lock()
        self._stopping = False
        self._tasks = {}

    def request(self, command, payload=None):
        payload = dict(payload or {})
        request_id = payload.pop('request_id', None) or str(uuid.uuid4())
        item = (command, payload, request_id)
        with self._lock:
            if self._stopping:
                return request_id
            if self._loop is None:
                self._pending.append(item)
            else:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
        return request_id

    def stop(self):
        with self._lock:
            self._stopping = True
            self._pending.clear()
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, ('stop', {}, ''))

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._queue = asyncio.Queue()
            for item in self._pending:
                self._queue.put_nowait(item)
            self._pending.clear()
            if self._stopping:
                self._queue.put_nowait(('stop', {}, ''))
        try:
            async with httpx.AsyncClient(base_url=self.settings.core_url, timeout=httpx.Timeout(55, connect=2), trust_env=False, follow_redirects=False) as client:
                while True:
                    command, payload, request_id = await self._queue.get()
                    if command in ('stop', 'ai_cancel'):
                        selected = payload.get('request_id') or payload.get('target_id')
                        for key, task in list(self._tasks.items()):
                            if selected is None or selected == key:
                                task.cancel()
                        if command == 'stop':
                            break
                        continue
                    task = asyncio.create_task(self._perform(client, command, payload, request_id))
                    self._tasks[request_id] = task
                await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        finally:
            with self._lock:
                self._loop = None
                self._queue = None

    async def _perform(self, client, command, payload, request_id):
        try:
            proof = health_headers(self.settings, 'desktop')
            health = await client.get('/api/v1/health', headers=proof)
            health.raise_for_status()
            body = health.json()
            if (not verify_health(self.settings, 'desktop', proof, body) or not body.get('ready')
                    or body.get('history_encryption') != 1 or not body.get('history_ready')):
                raise ValueError()
            routes = {'ai_settings': ('GET', '/settings'), 'ai_save_settings': ('POST', '/settings'),
                      'ai_save_key': ('POST', '/key'), 'ai_use_key': ('POST', '/key/use'),
                      'ai_models': ('GET', '/models'), 'ai_digest': ('POST', '/digest'),
                      'ai_prepare': ('POST', '/prepare'), 'ai_generate': ('POST', '/generate')}
            routes.update({'ai_preset_options': ('GET', '/preset-options'), 'ai_presets': ('GET', '/presets'), 'ai_create_preset': ('POST', '/presets'),
                'ai_update_preset': ('PATCH', '/presets/{preset_id}'), 'ai_archive_preset': ('DELETE', '/presets/{preset_id}'),
                'ai_active_preset': ('GET', '/active-preset'), 'ai_activate_preset': ('PUT', '/active-preset'),
                'ai_history': ('GET', '/history'), 'ai_history_detail': ('GET', '/history/{entry_id}'),
                'ai_pin_history': ('PATCH', '/history/{entry_id}'), 'ai_history_settings': ('GET', '/history-settings'),
                'ai_save_history_settings': ('PUT', '/history-settings')})
            method, suffix = routes[command]
            for name in ('preset_id', 'entry_id'):
                if '{' + name + '}' in suffix:
                    from urllib.parse import quote
                    suffix = suffix.replace('{' + name + '}', quote(str(payload.pop(name)), safe=''))
            values = {key: value for key, value in payload.items() if value is not None}
            response = await client.request(method, '/api/v1/ai' + suffix,
                headers={'Authorization': 'Bearer ' + service_token(self.settings, 'desktop')},
                **({'params': values} if method == 'GET' else {'json': payload}))
            if response.status_code >= 400:
                # Only the authenticated local AI router supplies these fixed messages.
                detail = response.json().get('detail')
                if response.status_code == 404 and command not in ('ai_history_detail', 'ai_pin_history', 'ai_update_preset', 'ai_archive_preset', 'ai_activate_preset'):
                    detail = 'AI is unavailable in this core version. Restart the updated core and desktop.'
                message = detail if isinstance(detail, str) and len(detail) <= 300 else 'The AI operation could not finish. Review Settings and prepare the text again.'
                self.result.emit({'command': command, 'request_id': request_id, 'ok': False, 'error': message})
            else:
                self.result.emit({'command': command, 'request_id': request_id, 'ok': True, 'data': response.json()})
        except asyncio.CancelledError:
            pass
        except Exception:
            self.result.emit({'command': command, 'request_id': request_id, 'ok': False, 'error': 'The private AI service is unavailable. Check that core is connected and try again.'})
        finally:
            payload.pop('key', None)
            self._tasks.pop(request_id, None)
