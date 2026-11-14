"""Optional web process ownership, authenticated adoption, and bounded shutdown."""
import asyncio
import errno
import os
import sys
import secrets
import subprocess
import httpx
from pbdesk.credentials import service_token
from pbdesk.services.client_lifecycle import stop_generation
from pbdesk.services.web_identity import web_health_headers, verify_web_health


class OwnedWebProcess:
    """Popen ownership survives an intentional core event-loop shutdown.

    asyncio subprocess transports kill live children on finalization; a web host
    must remain available for authenticated adoption by the replacement core.
    """
    def __init__(self, process):
        self.process = process

    @property
    def returncode(self):
        return self.process.poll()

    async def wait(self):
        return await asyncio.to_thread(self.process.wait)

    def terminate(self):
        self.process.terminate()

    def kill(self):
        self.process.kill()


class WebHost:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.instance_id = None
        self.adopted = False
        self.error = None
        self.lock = asyncio.Lock()
        self.store = None

    def status(self):
        alive = bool(self.instance_id and (self.adopted or (self.process and self.process.returncode is None)))
        return {'enabled': alive, 'url': f'http://{self.settings.web_host}:{self.settings.web_port}',
                'host': self.settings.web_host, 'port': self.settings.web_port, 'error': self.error,
                'instance_id': self.instance_id if alive else None,
                'ownership': ('adopted' if self.adopted else 'owned') if alive else None}

    async def _probe(self, client):
        headers = web_health_headers(self.settings)
        try:
            response = await client.get(self.status()['url'] + '/health', headers=headers)
        except httpx.ConnectError as exc:
            cause = exc
            while cause is not None:
                if isinstance(cause, OSError) and cause.errno == errno.ECONNREFUSED:
                    return None
                cause = cause.__cause__ or cause.__context__
            raise RuntimeError('Cannot access web port; check loopback permissions') from None
        except httpx.HTTPError:
            raise RuntimeError('Web port is occupied by an unresponsive service') from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code != 200 or not verify_web_health(self.settings, headers, body):
            raise RuntimeError('Web port is occupied by an unverified service; it was left untouched')
        return body

    async def watch(self):
        while True:
            await asyncio.sleep(1)
            async with self.lock:
                if not self.adopted:
                    continue
                try:
                    async with httpx.AsyncClient(trust_env=False, timeout=.5) as client:
                        body = await self._probe(client)
                    if body is None or body['instance_id'] != self.instance_id:
                        self.instance_id = None
                        self.adopted = False
                except RuntimeError as exc:
                    self.instance_id = None
                    self.adopted = False
                    self.error = str(exc)

    async def set_enabled(self, enabled, *, persist=False):
        async with self.lock:
            if persist:
                if self.store is None:
                    raise RuntimeError('Web host persistence is not initialized')
                await asyncio.to_thread(self.store.set_setting, 'web_enabled', enabled)
            return await self._set_enabled(enabled)

    async def _set_enabled(self, enabled):
        async with httpx.AsyncClient(trust_env=False, timeout=.5, follow_redirects=False) as client:
            if not enabled:
                try:
                    if self.instance_id:
                        body = await self._probe(client)
                        if body and body['instance_id'] == self.instance_id:
                            response = await client.post(self.status()['url'] + '/internal/shutdown',
                                headers={'Authorization': 'Bearer ' + service_token(self.settings, 'desktop')},
                                json={'instance_id': self.instance_id})
                            response.raise_for_status()
                        elif body:
                            raise RuntimeError('Web instance changed; replacement was left untouched')
                except (RuntimeError, httpx.HTTPError) as exc:
                    self.error = str(exc) if isinstance(exc, RuntimeError) else 'Web shutdown could not be confirmed'
                if self.process and self.process.returncode is None:
                    try:
                        await asyncio.wait_for(self.process.wait(), 3)
                    except TimeoutError:
                        self.process.terminate()  # Our exact owned child, never a guessed PID.
                        try:
                            await asyncio.wait_for(self.process.wait(), 2)
                        except TimeoutError:
                            self.process.kill()
                            await self.process.wait()
                self.process = None
                self.instance_id = None
                self.adopted = False
                return self.status()
            if self.status()['enabled']:
                return self.status()
            try:
                existing = await self._probe(client)
                if existing:
                    if existing['generation'] != await asyncio.to_thread(stop_generation, self.settings):
                        raise RuntimeError('Web process belongs to a stopped launch; let it exit before enabling again')
                    self.instance_id = existing['instance_id']
                    self.adopted = True
                    self.error = None
                    return self.status()
            except RuntimeError as exc:
                self.error = str(exc)
                return self.status()
            instance_id = secrets.token_hex(32)
            generation = await asyncio.to_thread(stop_generation, self.settings)
            env = dict(os.environ, PBDESK_APPLICATION_DIR=str(self.settings.application_dir),
                PBDESK_WEB_INSTANCE_ID=instance_id, PBDESK_STOP_GENERATION=generation,
                PBDESK_STATE_DIR=str(self.settings.state_dir), PBDESK_CORE_PORT=str(self.settings.core_port),
                PBDESK_WEB_PORT=str(self.settings.web_port))
            process = await asyncio.to_thread(subprocess.Popen, [sys.executable, '-m', 'pbdesk.web'], env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.process = OwnedWebProcess(process)
            for _ in range(60):
                await asyncio.sleep(.1)
                if self.process.returncode is not None:
                    self.error = 'Web host failed to start; check web extra and port availability'
                    return self.status()
                try:
                    body = await self._probe(client)
                    if body and body['instance_id'] == instance_id and body['generation'] == generation:
                        self.instance_id = instance_id
                        self.error = None
                        return self.status()
                    if body:
                        break
                except RuntimeError:
                    break
            # The owned child may be stopped; never address the unverified listener.
            self.process.terminate()
            await self.process.wait()
            self.process = None
            self.error = 'Web host readiness failed; check configured port and private service identity'
            return self.status()
