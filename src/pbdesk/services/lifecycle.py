"""Authenticated startup discovery with a bounded, GUI-thread-safe launcher."""
import os
import errno
import hashlib
import json
import secrets
from pathlib import Path
import subprocess
import sys
import time
import httpx
from pbdesk.credentials import private_dir, service_token, health_headers, verify_health

class CoreStartupError(RuntimeError):
    pass


def _probe(settings, token, client, role="desktop"):
    headers = health_headers(settings, role)
    try:
        response = client.get(settings.core_url + "/api/v1/health", headers=headers)
    except httpx.ConnectError as exc:
        cause = exc
        refused = False
        while cause is not None:
            if isinstance(cause, OSError) and cause.errno == errno.ECONNREFUSED:
                refused = True
            cause = cause.__cause__ or cause.__context__
        if refused:
            return None
        raise CoreStartupError("Cannot connect to core port; check loopback access and permissions") from None
    except httpx.HTTPError:
        raise CoreStartupError("Configured core port does not provide a responsive pbdesk service") from None
    if response.status_code != 200:
        raise CoreStartupError("Core port is occupied or credentials are incompatible; check state directory and configured port")
    try:
        body = response.json()
    except ValueError:
        raise CoreStartupError("Configured core port belongs to an incompatible service") from None
    if not verify_health(settings, role, headers, body):
        raise CoreStartupError("Configured core port belongs to an incompatible service")
    return body


def ensure_core(settings, timeout=12.0, demo=False, *, role="desktop", generation=None, cancel=None):
    """Call from a worker thread. Prefer a transient systemd user service."""
    import fcntl
    token = service_token(settings, role)
    root = private_dir(settings)
    fd = os.open(root / "launcher.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    child = None
    try:
        deadline = time.monotonic() + timeout
        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise CoreStartupError("Client closed during core startup")
        while True:
            check_cancel()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CoreStartupError("Another launcher did not become ready before timeout")
                time.sleep(.05)
        from pbdesk.services.client_lifecycle import _generation_locked
        current = _generation_locked(root)
        if generation is not None and generation != current:
            raise CoreStartupError("Stop All was requested; relaunch this client to start a new core")
        from pbdesk.services.core_settings import pending_restart, clear_restart
        try:
            restart = pending_restart(settings, current, allow_expired=True)
        except RuntimeError as exc:
            raise CoreStartupError(str(exc)) from None
        def probe(client):
            health = _probe(settings, token, client) if role == "desktop" else _probe(settings, token, client, role)
            if health and health.get("client_lifecycle") != 1:
                raise CoreStartupError("Core update ready: restart the shared core to enable coupled client lifecycle")
            return health
        with httpx.Client(timeout=.5, trust_env=False) as client:
            health = probe(client)
            if health:
                while not health.get("ready") and time.monotonic() < deadline:
                    check_cancel()
                    time.sleep(.1)
                    health = probe(client)
                    if health is None:
                        raise CoreStartupError("Core stopped while becoming ready; retry startup")
                if not health.get("ready"):
                    raise CoreStartupError("Core readiness timed out; restart the shared core")
                if restart and health["instance_id"] != restart["instance_id"]:
                    clear_restart(settings, restart["instance_id"])
                return health
            check_cancel()
            if restart and restart["expires_at"] <= time.time():
                raise CoreStartupError("Restart handoff expired; start the core explicitly with its original launch options")
            started = False
            command = [sys.executable, "-m", "pbdesk.cli", "--foreground"]
            if restart:
                profile = restart['profile']
                if profile['notifications']:
                    command.append('--notifications')
                if profile['demo']:
                    command.append('--demo')
                if profile['fixture']:
                    command.extend(['--fixture', profile['fixture']])
                if profile['standalone']:
                    command.append('--standalone')
            else:
                command.append('--notifications')
                if demo or os.environ.get("PBDESK_LIFECYCLE_DEMO") == "1":
                    command.append("--demo")
            unit_hash = hashlib.sha256((str(root.resolve()) + ":" + str(settings.core_port)).encode()).hexdigest()[:16]
            manager_available = False
            if os.environ.get("PBDESK_LIFECYCLE") != "foreground":
                try:
                    manager_probe = subprocess.run(["systemctl", "--user", "show-environment"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
                    manager_available = manager_probe.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if manager_available:
                transient = ["systemd-run", "--user", "--collect", "--service-type=exec",
                    "--unit=pbdesk-core-" + unit_hash + "-" + secrets.token_hex(4),
                    "--property=UMask=0077",
                    "--property=KillMode=process",
                    "--setenv=PBDESK_STATE_DIR=" + str(settings.state_dir.resolve()),
                    "--setenv=PBDESK_APPLICATION_DIR=" + str(settings.application_dir),
                    "--setenv=PBDESK_CORE_PORT=" + str(settings.core_port),
                    "--setenv=PBDESK_WEB_PORT=" + str(settings.web_port),
                    "--setenv=PBDESK_STOP_GENERATION=" + current,
                    *["--setenv=" + key + "=" + os.environ[key] for key in ("PBDESK_LEASE_TIMING", "PBDESK_LIFECYCLE_DEMO") if key in os.environ and not (restart and key == "PBDESK_LEASE_TIMING")],
                    *(["--setenv=PBDESK_LEASE_TIMING=" + json.dumps(restart["profile"]["timing"])] if restart else []),
                    *command]
                try:
                    result = subprocess.run(transient, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=min(4, max(.1, deadline-time.monotonic())))
                    if result.returncode:
                        raise CoreStartupError("User service manager rejected core startup; inspect systemctl --user status and configured ports")
                    started = True
                except (OSError, subprocess.TimeoutExpired):
                    raise CoreStartupError("User service startup failed or timed out; inspect systemctl --user status") from None
            if not started:
                env = dict(os.environ, PBDESK_APPLICATION_DIR=str(settings.application_dir), PBDESK_STATE_DIR=str(settings.state_dir.resolve()),
                           PBDESK_CORE_PORT=str(settings.core_port), PBDESK_WEB_PORT=str(settings.web_port), PBDESK_STOP_GENERATION=current)
                if restart:
                    env["PBDESK_LEASE_TIMING"] = json.dumps(restart["profile"]["timing"])
                # No credential in argv/env/log. Core logs are private and sanitized.
                log_fd = os.open(root / "core.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                try:
                    child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                        stdout=log_fd, stderr=log_fd, start_new_session=True, close_fds=True)
                finally:
                    os.close(log_fd)
            while time.monotonic() < deadline:
                check_cancel()
                health = probe(client)
                if health and health.get("ready"):
                    if restart and health["instance_id"] != restart["instance_id"]:
                        clear_restart(settings, restart["instance_id"])
                    return health
                if child and child.poll() is not None:
                    raise CoreStartupError("Core exited during startup; inspect private core.log or run pbdesk-core --foreground")
                time.sleep(.1)
            raise CoreStartupError("Core readiness timed out; check port, service status and private core.log")
    except BaseException:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        raise
    finally:
        os.close(fd)
