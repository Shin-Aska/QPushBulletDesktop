"""Core-owned settings, quiescent restart coordination, and bounded relaunch handoff."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from pbdesk.credentials import private_dir
from pbdesk.services.client_lifecycle import LeaseTiming, stop_generation
from pbdesk.storage.workflows import actions

RESTART_RECORD = 'pending-core-restart.json'
RESTART_TTL = 60


def _canonical_uuid(value):
    return isinstance(value, str) and str(uuid.UUID(value)) == value


def _validate_record(value):
    try:
        if not isinstance(value, dict) or set(value) != {'version', 'instance_id', 'generation', 'created_at', 'expires_at', 'profile'}:
            raise ValueError()
        if type(value['version']) is not int or value['version'] != 1 or not _canonical_uuid(value['instance_id']):
            raise ValueError()
        generation = value['generation']
        if not isinstance(generation, str) or len(generation) != 64 or any(c not in '0123456789abcdef' for c in generation):
            raise ValueError()
        created, expires = value['created_at'], value['expires_at']
        if type(created) not in (float, int) or type(expires) not in (float, int) or not 0 < expires - created <= RESTART_TTL or created > time.time() + 5:
            raise ValueError()
        profile = value['profile']
        if not isinstance(profile, dict) or set(profile) != {'demo', 'fixture', 'standalone', 'notifications', 'timing'}:
            raise ValueError()
        if any(type(profile[key]) is not bool for key in ('demo', 'standalone', 'notifications')):
            raise ValueError()
        if profile['fixture'] is not None and (not isinstance(profile['fixture'], str) or not profile['fixture'] or len(profile['fixture']) > 4096 or not Path(profile['fixture']).is_absolute()):
            raise ValueError()
        if not isinstance(profile['timing'], dict):
            raise ValueError()
        LeaseTiming(**profile['timing'])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RuntimeError('Private restart handoff is invalid; start the core explicitly with its original launch options') from None
    return value


def pending_restart(settings, generation, *, allow_expired=False):
    """Caller holds launcher.lock. No fallback to a different launch after failure."""
    root = private_dir(settings)
    try:
        fd = os.open(root / RESTART_RECORD, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeError('Private restart handoff is inaccessible') from None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
            raise RuntimeError('Private restart handoff must be a private regular file (0600)')
        try:
            value = _validate_record(json.loads(stream.read(16385)))
        except (ValueError, UnicodeError):
            raise RuntimeError('Private restart handoff is invalid') from None
    if value['generation'] != generation:
        clear_restart(settings, value['instance_id'])
        return None
    if not allow_expired and value['expires_at'] <= time.time():
        raise RuntimeError('Restart handoff expired; start the core explicitly with its original launch options')
    return value


def write_restart(settings, instance_id, generation, profile):
    """Write only the launch profile observed by this running core."""
    now = time.time()
    value = _validate_record({'version': 1, 'instance_id': instance_id, 'generation': generation,
        'created_at': now, 'expires_at': now + RESTART_TTL, 'profile': profile})
    root = private_dir(settings)
    fd, temporary = tempfile.mkstemp(dir=root, prefix='.core-restart-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / RESTART_RECORD)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def clear_restart(settings, expected_instance):
    """Remove only the handoff owned by the expected old core instance."""
    root = private_dir(settings)
    path = root / RESTART_RECORD
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            value = json.loads(stream.read(16385))
        if isinstance(value, dict) and value.get('instance_id') == expected_instance:
            path.unlink()
    except (FileNotFoundError, ValueError, OSError):
        pass


async def finish_mutation(awaitable):
    """Cancellation cannot release mutation ownership while a worker can commit.

    The request still observes cancellation after the in-flight operation settles;
    no provider command is replayed, and restart sees it active until then.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Retrieve a late exception without changing the original cancellation.
        if task.done() and not task.cancelled():
            task.exception()
        raise


class RestartGate:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.active = 0
        self.restarting = False

    @asynccontextmanager
    async def operation(self):
        async with self.lock:
            if self.restarting:
                raise HTTPException(409, 'Core is restarting; wait for reconnection before making changes')
            self.active += 1
        try:
            yield
        finally:
            async with self.lock:
                self.active -= 1


class CoreSettingsService:
    def __init__(self, app, settings, profile, *, launcher=None):
        self.app, self.settings, self.profile = app, settings, profile
        self.gate = RestartGate()
        self.request_id = None
        self.accepted_instance = None
        self.started = time.monotonic()
        self.launcher = launcher or launch_restart_helper

    @property
    def restarting(self):
        return self.gate.restarting

    def _instance(self, expected):
        if expected != self.app.state.store.instance_id:
            raise HTTPException(409, 'Core instance changed; refresh core settings before continuing')

    def snapshot(self):
        registry, notifier = self.app.state.clients, self.app.state.notifier
        now = time.monotonic()
        roles = [role for role, deadline in registry.leases.values() if deadline > now]
        # Availability and effective enabled state are separate from the saved request.
        available = bool(getattr(notifier, 'available', getattr(notifier, 'command', None)))
        return {'instance_id': self.app.state.store.instance_id,
            'lifecycle': {'mode': 'standalone' if registry.standalone else 'coupled',
                'desktop_clients': roles.count('desktop'), 'web_clients': roles.count('web'),
                'renew_seconds': registry.timing.renew, 'expiry_seconds': registry.timing.expiry,
                'release_grace_seconds': registry.timing.release_grace},
            'addresses': {'core': self.settings.core_url, 'web': self.app.state.host.status()['url']},
            'paths': {'application': str(self.settings.application_dir), 'state': str(self.settings.state_dir.resolve()),
                      'database': str(self.app.state.store.path.resolve())},
            'notifications': {'requested': bool(getattr(notifier, 'requested', notifier.enabled)),
                              'enabled': bool(notifier.enabled and available), 'available': available,
                              'outcome': str(getattr(notifier, 'outcome', 'unavailable'))},
            'restarting': self.restarting, 'uptime_seconds': max(0, int(now - self.started))}

    async def update(self, expected, enabled):
        return await finish_mutation(self._update(expected, enabled))

    async def _update(self, expected, enabled):
        async with self.gate.operation():
            self._instance(expected)
            adapter = self.app.state.notifier
            if not hasattr(adapter, 'configure'):
                raise HTTPException(409, 'This embedded notifier does not support live core settings')
            await finish_mutation(asyncio.to_thread(self.app.state.store.set_setting, 'core_notifications_enabled', enabled))
            adapter.configure(enabled)
            return self.snapshot()

    def _approved_actions(self):
        store = self.app.state.store
        store.expire_actions()
        with store.engine.connect() as connection:
            return connection.execute(select(actions.c.id).where(actions.c.state.in_(['ready', 'submitting']),
                actions.c.account_id == store.account_key()).limit(1)).first() is not None

    async def restart(self, expected, request_id):
        return await finish_mutation(self._restart(expected, request_id))

    async def _restart(self, expected, request_id):
        self._instance(expected)
        async with self.gate.lock:
            if self.gate.restarting:
                if self.request_id == request_id and self.accepted_instance == expected:
                    return {'accepted': True, 'instance_id': expected, 'request_id': request_id}
                raise HTTPException(409, 'A core restart is already in progress; wait for reconnection')
            if not self.app.state.shutdown_callback:
                raise HTTPException(409, 'This core cannot restart itself; restart its foreground process explicitly')
            if self.profile is None:
                raise HTTPException(409, 'This embedded core has custom launch settings; restart its process explicitly')
            dispatcher = self.app.state.dispatcher
            if self.gate.active or dispatcher.lock.locked() or self.app.state.provider_reload_lock.locked():
                raise HTTPException(409, 'A provider operation or message submission is active; wait for it to finish before restarting')
            # Check and pause while holding the exact dispatch/approval interlock.
            async with dispatcher.lock:
                if await asyncio.to_thread(self._approved_actions):
                    raise HTTPException(409, 'Approved messages are ready or submitting; wait or cancel them before restarting')
                self.gate.restarting = True
                dispatcher.paused = True
                self.request_id, self.accepted_instance = request_id, expected
                generation = self.app.state.clients.generation
                try:
                    # Serialize record publication with Stop All and every launcher.
                    await asyncio.to_thread(publish_restart, self.settings, expected, generation, self.profile)
                    await self.launcher(self.settings, expected)
                except Exception:
                    await asyncio.to_thread(cancel_restart, self.settings, expected)
                    self.gate.restarting = False
                    dispatcher.paused = False
                    self.request_id = self.accepted_instance = None
                    raise HTTPException(409, 'Core restart could not be prepared; the current core is still running') from None
                # Neither Stop All nor generation advancement is involved in restart.
                self.app.state.restart_intent = True
                self.app.state.clients.stopping = True
                asyncio.get_running_loop().call_later(.2, self.app.state.shutdown_callback)
                return {'accepted': True, 'instance_id': expected, 'request_id': request_id}


def publish_restart(settings, instance_id, generation, profile):
    import fcntl
    from pbdesk.services.client_lifecycle import _generation_locked
    root = private_dir(settings)
    fd = os.open(root / 'launcher.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if _generation_locked(root) != generation:
            raise RuntimeError('Stop All cancelled the restart')
        write_restart(settings, instance_id, generation, profile)
    finally:
        os.close(fd)


def cancel_restart(settings, instance_id):
    import fcntl
    root = private_dir(settings)
    fd = os.open(root / 'launcher.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        clear_restart(settings, instance_id)
    finally:
        os.close(fd)


async def launch_restart_helper(settings, instance_id):
    env = dict(os.environ, PBDESK_APPLICATION_DIR=str(settings.application_dir), PBDESK_STATE_DIR=str(settings.state_dir.resolve()),
        PBDESK_CORE_PORT=str(settings.core_port), PBDESK_WEB_PORT=str(settings.web_port))
    # Popen has no asyncio transport finalizer that could kill this helper when
    # the old core closes its event loop. The helper holds no client lease.
    process = await asyncio.to_thread(subprocess.Popen,
        [sys.executable, '-m', 'pbdesk.services.core_settings', '--restart-helper', instance_id],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ready = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 3)
        if ready != b'ready\n' or process.poll() is not None:
            raise RuntimeError('Restart helper failed its startup handshake')
    except BaseException:
        if process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.wait)
        raise
    finally:
        process.stdout.close()


def inspect_restart(settings):
    import fcntl
    from pbdesk.services.client_lifecycle import _generation_locked
    root = private_dir(settings)
    fd = os.open(root / 'launcher.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        generation = _generation_locked(root)
        return generation, pending_restart(settings, generation)
    finally:
        os.close(fd)


def restart_helper(instance_id):
    """No lease or PID markers: wait for signed old core exit, then serialized launch."""
    import httpx
    from pbdesk.config import Settings
    from pbdesk.services.lifecycle import _probe, ensure_core, CoreStartupError
    settings = Settings.from_env()
    generation, record = inspect_restart(settings)
    if record is None or record['instance_id'] != instance_id:
        return
    print('ready', flush=True)
    while time.time() < record['expires_at']:
        if stop_generation(settings) != generation:
            cancel_restart(settings, instance_id)
            return
        try:
            with httpx.Client(timeout=.5, trust_env=False) as client:
                identity = _probe(settings, None, client)
            if identity:
                if identity['instance_id'] != instance_id:
                    if identity.get('ready'):
                        cancel_restart(settings, instance_id)
                    return
            else:
                identity = ensure_core(settings, generation=generation)
                if identity['instance_id'] != instance_id:
                    return
        except CoreStartupError:
            # Failed exact-profile launch never falls back to ordinary desktop defaults.
            pass
        time.sleep(.2)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--restart-helper', required=True)
    restart_helper(parser.parse_args().restart_helper)
