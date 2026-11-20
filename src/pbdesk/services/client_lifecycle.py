"""Instance-bound client leases and a persistent Stop All recovery barrier."""
import asyncio
import fcntl
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass

import httpx
from pbdesk.credentials import private_dir, service_token


@dataclass(frozen=True)
class LeaseTiming:
    """Timing bounds and intervals controlling client lease renewal and expiration.

    :ivar renew: Lease renewal interval in seconds.
    :type renew: float
    :ivar expiry: Lease expiration timeout in seconds.
    :type expiry: float
    :ivar release_grace: Grace period in seconds after the last lease is released before shutdown.
    :type release_grace: float
    :ivar startup_grace: Grace period in seconds before requiring an initial client lease registration.
    :type startup_grace: float
    """

    renew: float = 5
    expiry: float = 45
    release_grace: float = 3
    startup_grace: float = 20

    def __post_init__(self):
        """Validate that all lease timing parameters are within allowed bounds.

        :raises ValueError: If any timing bound falls outside permitted limits.
        """
        if not (.05 <= self.renew <= 60 and self.renew * 3 <= self.expiry <= 600
                and .05 <= self.release_grace <= 60 and .1 <= self.startup_grace <= 120):
            raise ValueError("Invalid client lifecycle timing")

    @classmethod
    def from_env(cls):
        """Load LeaseTiming overrides from PBDESK_LEASE_TIMING or return defaults.

        :return: Constructed ``LeaseTiming`` configuration instance.
        :rtype: LeaseTiming
        """
        raw = os.environ.get('PBDESK_LEASE_TIMING')
        return cls(**json.loads(raw)) if raw else cls()


def stop_generation(settings, *, advance=False):
    """Read/create under the same lock used to serialize launch and Stop All.

    :param settings: Application settings pointing to the private state directory.
    :type settings: pbdesk.config.Settings
    :param advance: Whether to rotate and persist a fresh Stop All generation token.
    :type advance: bool
    :return: Current or newly generated 64-hex-character Stop All generation token.
    :rtype: str
    """
    root = private_dir(settings)
    fd = os.open(root / 'launcher.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return _generation_locked(root, advance=advance)
    finally:
        os.close(fd)


def _generation_locked(root, *, advance=False):
    """Read or atomically advance the persistent Stop All generation token under lock.

    :param root: Private state directory path containing ``stop-generation``.
    :type root: pathlib.Path
    :param advance: Whether to rotate and atomically replace the generation token.
    :type advance: bool
    :return: Validated 64-hex-character Stop All generation token.
    :rtype: str
    :raises RuntimeError: If the existing ``stop-generation`` file is malformed.
    """
    path = root / 'stop-generation'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            generation = stream.read(129).strip()
        if len(generation) != 64 or any(c not in '0123456789abcdef' for c in generation):
            raise RuntimeError('Invalid Stop All generation file')
    except FileNotFoundError:
        generation = None
    if advance or generation is None:
        generation = secrets.token_hex(32)
        fd, temporary = tempfile.mkstemp(dir=root, prefix='.stop-generation-')
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(generation + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return generation


class LeaseRegistry:
    """Core-side registry tracking active desktop and web client leases."""

    def __init__(self, instance_id, generation, shutdown, *, standalone=False, timing=None):
        """Initialize the lease registry for a core instance and Stop All generation.

        :param instance_id: Unique identifier of the owning core instance.
        :type instance_id: str
        :param generation: Active Stop All generation token for this core launch.
        :type generation: str
        :param shutdown: Callback invoked when coupled client leases expire.
        :type shutdown: collections.abc.Callable[[], Any]
        :param standalone: Whether the core stays alive without coupled client leases.
        :type standalone: bool
        :param timing: Optional lease timing overrides; defaults to environment settings.
        :type timing: LeaseTiming | None
        """
        self.instance_id, self.generation, self.shutdown = instance_id, generation, shutdown
        self.standalone = standalone
        self.timing = timing or LeaseTiming.from_env()
        self.leases = {}
        self.empty_since = None
        self.started = time.monotonic()
        self.ever_registered = False
        self.stopping = False

    def register(self, role, instance_id, generation):
        """Register a new client lease for the active instance and generation.

        :param role: Client role registering the lease (``desktop`` or ``web``).
        :type role: str
        :param instance_id: Core instance identifier expected by the client.
        :type instance_id: str
        :param generation: Stop All generation token held by the client.
        :type generation: str
        :return: Dictionary containing ``lease_id``, ``instance_id``, and ``renew_after``.
        :rtype: dict[str, Any]
        :raises ValueError: If the core is stopping or the instance/generation does not match.
        """
        if self.stopping or instance_id != self.instance_id or generation != self.generation:
            raise ValueError('Core instance or Stop All generation changed; relaunch the client')
        lease_id = secrets.token_urlsafe(32)
        self.leases[lease_id] = (role, time.monotonic() + self.timing.expiry)
        self.ever_registered = True
        self.empty_since = None
        return {'lease_id': lease_id, 'instance_id': self.instance_id, 'renew_after': self.timing.renew}

    def update(self, role, instance_id, lease_id, release=False):
        """Renew or release an existing client lease for the matching role and instance.

        :param role: Client role updating the lease.
        :type role: str
        :param instance_id: Core instance identifier expected by the client.
        :type instance_id: str
        :param lease_id: Lease identifier previously issued by :meth:`register`.
        :type lease_id: str
        :param release: Whether to delete the lease immediately instead of renewing it.
        :type release: bool
        :return: Dictionary containing ``released`` status and ``instance_id``.
        :rtype: dict[str, Any]
        :raises ValueError: If the instance, lease ID, or role does not match an active lease.
        """
        if instance_id != self.instance_id or lease_id not in self.leases or self.leases[lease_id][0] != role:
            raise ValueError('Client lease is unknown or belongs to another core instance')
        if release:
            del self.leases[lease_id]
        else:
            self.leases[lease_id] = (role, time.monotonic() + self.timing.expiry)
        return {'released': release, 'instance_id': self.instance_id}

    async def run(self):
        """Monitor active leases and trigger shutdown when coupled clients disappear."""
        while not self.stopping:
            await asyncio.sleep(min(.5, self.timing.renew))
            now = time.monotonic()
            self.leases = {key: value for key, value in self.leases.items() if value[1] > now}
            if self.standalone or self.leases:
                self.empty_since = None
                continue
            if not self.ever_registered and now - self.started < self.timing.startup_grace:
                continue
            if self.empty_since is None:
                self.empty_since = now
            if now - self.empty_since >= self.timing.release_grace:
                self.stopping = True
                self.shutdown()


class ClientLease:
    """Dedicated heartbeat thread, independent of client media/mutation workers.

    Only connection refusal permits recovery. Failed commands are never replayed.
    """
    def __init__(self, settings, role='desktop', *, demo=False, generation=None):
        """Initialize a client lease holder for the desktop or web role.

        :param settings: Application settings specifying core URL and state directory.
        :type settings: pbdesk.config.Settings
        :param role: Client role holding the lease (``desktop`` or ``web``).
        :type role: str
        :param demo: Whether core auto-launch should enable demo mode.
        :type demo: bool
        :param generation: Explicit Stop All generation token, or ``None`` to load/create it.
        :type generation: str | None
        :raises ValueError: If ``role`` is not ``desktop`` or ``web``.
        """
        if role not in {'desktop', 'web'}:
            raise ValueError('Only desktop and web processes hold client leases')
        self.settings, self.role, self.demo = settings, role, demo
        self.generation = generation if generation is not None else stop_generation(settings)
        self.stop_event = threading.Event()
        self.thread = None
        self.wake_event = threading.Event()
        self.lock = threading.RLock()
        self.instance_id = self.lease_id = None
        self.error = None
        self.timing = LeaseTiming.from_env()

    def connect(self):
        """Ensure the core service is running and register or reuse a client lease.

        :return: Verified core health payload dictionary returned by ``ensure_core``.
        :rtype: dict[str, Any]
        :raises RuntimeError: If the client lease closes during core startup.
        """
        from pbdesk.services.lifecycle import ensure_core
        identity = ensure_core(self.settings, demo=self.demo, role=self.role, generation=self.generation, cancel=self.stop_event)
        with self.lock:
            if self.stop_event.is_set():
                raise RuntimeError('Client closed during core startup')
            if identity['instance_id'] != self.instance_id or self.lease_id is None:
                with self._client() as client:
                    response = client.post('/api/v1/clients', json={'instance_id': identity['instance_id'], 'generation': self.generation})
                    response.raise_for_status()
                    data = response.json()
                self.instance_id, self.lease_id = identity['instance_id'], data['lease_id']
        return identity

    def _client(self):
        """Create an authenticated synchronous HTTP client targeting the core API.

        :return: Configured synchronous ``httpx.Client`` with bearer token authentication.
        :rtype: httpx.Client
        """
        return httpx.Client(base_url=self.settings.core_url, timeout=2, trust_env=False, follow_redirects=False,
                            headers={'Authorization': 'Bearer ' + service_token(self.settings, self.role)})

    def start(self):
        """Connect to the core and launch the background lease heartbeat thread.

        :return: This ``ClientLease`` instance for call chaining.
        :rtype: ClientLease
        """
        self.connect()
        self.thread = threading.Thread(target=self._run, name='pbdesk-' + self.role + '-lease', daemon=True)
        self.thread.start()
        return self

    def _run(self):
        """Run the lease heartbeat loop and release the lease on exit."""
        try:
            self._heartbeat()
        finally:
            with self.lock:
                self._release()

    def wake(self):
        """Prompt discovery without replaying a command or blocking the caller."""
        self.wake_event.set()

    def _heartbeat(self):
        """Periodically renew the client lease and reconnect if the core restarts."""
        delay = self.timing.renew
        while True:
            deadline = time.monotonic() + delay
            while not self.stop_event.is_set() and not self.wake_event.is_set() and time.monotonic() < deadline:
                self.stop_event.wait(min(.1, max(0, deadline - time.monotonic())))
            if self.stop_event.is_set():
                return
            self.wake_event.clear()
            try:
                self.connect()  # authenticated discovery before any bearer credential
                if self.stop_event.is_set():
                    return
                with self._client() as client:
                    response = client.put('/api/v1/clients/' + self.lease_id, json={'instance_id': self.instance_id})
                    if response.status_code == 409:
                        self.lease_id = None
                    response.raise_for_status()
                self.error = None
                delay = self.timing.renew
            except Exception as exc:
                self.error = str(exc)
                delay = min(10, max(self.timing.renew, delay * 2))
                if stop_generation(self.settings) != self.generation:
                    self.stop_event.set()

    def close(self):
        """Stop the heartbeat thread and release the active client lease."""
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
        with self.lock:
            self._release()

    def _release(self):
        """Verify the core instance identity and delete the active client lease."""
        if not self.lease_id:
            return
        # Never release against a replacement or an unverified listener.
        from pbdesk.services.lifecycle import _probe
        try:
            with httpx.Client(timeout=.5, trust_env=False) as probe:
                identity = _probe(self.settings, None, probe, self.role)
            if identity and identity['instance_id'] == self.instance_id:
                with self._client() as client:
                    client.delete('/api/v1/clients/' + self.lease_id, params={'instance_id': self.instance_id})
        except Exception:
            pass
        self.lease_id = None
