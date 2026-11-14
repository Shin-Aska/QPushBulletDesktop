"""Core-owned optional web process; persisted preference, disabled by default."""
import asyncio
import os
import sys
import secrets
import httpx

class WebHost:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.error = None
        self.lock = asyncio.Lock()
        self.store = None

    def status(self):
        alive = self.process is not None and self.process.returncode is None
        return {"enabled": alive, "url": f"http://{self.settings.web_host}:{self.settings.web_port}",
                "host": self.settings.web_host, "port": self.settings.web_port, "error": self.error}

    async def set_enabled(self, enabled, *, persist=False):
        async with self.lock:
            if persist:
                if self.store is None:
                    raise RuntimeError("Web host persistence is not initialized")
                await asyncio.to_thread(self.store.set_setting, "web_enabled", enabled)
            return await self._set_enabled(enabled)

    async def _set_enabled(self, enabled):
        if not enabled:
            if self.process and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process = None
            self.error = None
            return self.status()
        if self.status()["enabled"]:
            return self.status()
        instance_id = secrets.token_hex(32)
        env = dict(os.environ, PBDESK_APPLICATION_DIR=str(self.settings.application_dir), PBDESK_WEB_INSTANCE_ID=instance_id, PBDESK_STATE_DIR=str(self.settings.state_dir),
                   PBDESK_CORE_PORT=str(self.settings.core_port), PBDESK_WEB_PORT=str(self.settings.web_port))
        self.process = await asyncio.create_subprocess_exec(sys.executable, "-m", "pbdesk.web", env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        # Startup is bounded and observes child failure, including missing optional Jinja dependency.
        async with httpx.AsyncClient(trust_env=False, timeout=.3) as client:
            for _ in range(40):
                await asyncio.sleep(.1)
                if self.process.returncode is not None:
                    self.error = "Web host failed to start; check web extra and port availability"
                    return self.status()
                try:
                    response = await client.get(self.status()["url"] + "/health")
                    if response.status_code == 200 and response.json() == {"application": "pbdesk-web", "api_version": 1, "instance_id": instance_id}:
                        self.error = None
                        return self.status()
                except (httpx.HTTPError, ValueError):
                    pass
        await self._set_enabled(False)
        self.error = "Web host readiness timed out"
        return self.status()
