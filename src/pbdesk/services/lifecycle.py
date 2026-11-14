"""Authenticated startup discovery with a bounded, GUI-thread-safe launcher."""
import os
import errno
import hashlib
from pathlib import Path
import subprocess
import sys
import time
import httpx
from pbdesk.credentials import private_dir, service_token, health_headers, verify_health

class CoreStartupError(RuntimeError):
    pass


def _probe(settings, token, client):
    headers = health_headers(settings, "desktop")
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
    if not verify_health(settings, "desktop", headers, body):
        raise CoreStartupError("Configured core port belongs to an incompatible service")
    return body if body.get("ready") is True else None


def ensure_core(settings, timeout=12.0, demo=False):
    """Call from a worker thread. Prefer a transient systemd user service."""
    import fcntl
    token = service_token(settings, "desktop")
    root = private_dir(settings)
    fd = os.open(root / "launcher.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    child = None
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CoreStartupError("Another launcher did not become ready before timeout")
                time.sleep(.05)
        with httpx.Client(timeout=.5, trust_env=False) as client:
            health = _probe(settings, token, client)
            if health:
                return health
            started = False
            command = [sys.executable, "-m", "pbdesk.cli", "--foreground", "--notifications"]
            if demo:
                command.append("--demo")
            unit_hash = hashlib.sha256((str(root.resolve()) + ":" + str(settings.core_port)).encode()).hexdigest()[:16]
            manager_available = False
            if os.environ.get("PBDESK_LIFECYCLE") != "foreground":
                try:
                    probe = subprocess.run(["systemctl", "--user", "show-environment"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
                    manager_available = probe.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if manager_available:
                transient = ["systemd-run", "--user", "--collect", "--service-type=exec",
                    "--unit=pbdesk-core-" + unit_hash,
                    "--property=UMask=0077",
                    "--setenv=PBDESK_STATE_DIR=" + str(settings.state_dir),
                    "--setenv=PBDESK_APPLICATION_DIR=" + str(settings.application_dir),
                    "--setenv=PBDESK_CORE_PORT=" + str(settings.core_port),
                    "--setenv=PBDESK_WEB_PORT=" + str(settings.web_port),
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
                env = dict(os.environ, PBDESK_APPLICATION_DIR=str(settings.application_dir), PBDESK_STATE_DIR=str(settings.state_dir),
                           PBDESK_CORE_PORT=str(settings.core_port), PBDESK_WEB_PORT=str(settings.web_port))
                # No credential in argv/env/log. Core logs are private and sanitized.
                log_fd = os.open(root / "core.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                try:
                    child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                        stdout=log_fd, stderr=log_fd, start_new_session=True, close_fds=True)
                finally:
                    os.close(log_fd)
            while time.monotonic() < deadline:
                health = _probe(settings, token, client)
                if health:
                    return health
                if child and child.poll() is not None:
                    raise CoreStartupError("Core exited during startup; inspect private core.log or run pbdesk-core --foreground")
                time.sleep(.1)
            raise CoreStartupError("Core readiness timed out; check port, service status and private core.log")
    finally:
        os.close(fd)
