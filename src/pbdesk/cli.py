"""Local entry points: secrets are prompted, never command-line arguments."""
import argparse
import asyncio
import getpass
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from pbdesk.config import Settings
from pbdesk.runtime import command, appimage_path, system_environment


def core_main(argv=None):
    parser = argparse.ArgumentParser(description="Run the private pbdesk core (coupled to desktop/web clients)")
    parser.add_argument("--foreground", action="store_true", help="Run in foreground (default)")
    parser.add_argument("--standalone", action="store_true", help="Keep core running without desktop/web clients (debug/service use)")
    parser.add_argument("--demo", action="store_true", help="Replay one fixed sanitized note; no provider connection")
    parser.add_argument("--fixture", type=Path, help="Replay a sanitized JSON event array without provider connection")
    parser.add_argument("--notifications", action="store_true", help="Enable core-owned KDE notification submission")
    args = parser.parse_args(argv)
    from pbdesk.api.app import create_app
    import uvicorn
    settings = Settings.from_env()
    app = create_app(settings, demo=args.demo, fixture_path=args.fixture, notifications=args.notifications, standalone=args.standalone, launch_generation=os.environ.get("PBDESK_STOP_GENERATION"))
    config = uvicorn.Config(app, host=settings.core_host, port=settings.core_port, workers=1,
                            access_log=False, log_level="warning", timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    try:
        server.run()
    except KeyboardInterrupt:
        pass


def desktop_main():
    from pbdesk.desktop.app import main
    return main()



class HistoryNotReady(RuntimeError):
    """A CLI command must not cross the encrypted-history bootstrap gate."""


HISTORY_SETUP_HINT = ('Open pbdesk to protect or unlock local history, then retry. '
                      'Restart an older core to enable mandatory encrypted history.')


def wait_for_history_ready(settings, *, timeout=12.0):
    """Wait only on signed health reads; never authorize a wallet or replay work."""
    import httpx
    from pbdesk.services.lifecycle import _probe, CoreStartupError
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
        while True:
            try:
                identity = _probe(settings, None, client)
            except CoreStartupError:
                raise HistoryNotReady(HISTORY_SETUP_HINT) from None
            if identity is not None:
                if identity.get('history_encryption') != 1:
                    raise HistoryNotReady(HISTORY_SETUP_HINT)
                if identity.get('ready') and identity.get('history_ready'):
                    return identity
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HistoryNotReady(HISTORY_SETUP_HINT)
            time.sleep(min(.1, remaining))

def web_main():
    """Control web through core; the gateway is never another data owner."""
    parser = argparse.ArgumentParser(description="Optional local web host control")
    parser.add_argument("action", choices=["status", "enable", "disable"], nargs="?", default="status")
    args = parser.parse_args()
    from pbdesk.credentials import service_token
    from pbdesk.services.lifecycle import ensure_core, CoreStartupError
    import httpx
    settings = Settings.from_env()
    try:
        ensure_core(settings)
        wait_for_history_ready(settings)
    except (CoreStartupError, HistoryNotReady):
        parser.exit(1, HISTORY_SETUP_HINT + '\n')
    with httpx.Client(base_url=settings.core_url, trust_env=False, timeout=8,
                      headers={"Authorization": "Bearer " + service_token(settings, "desktop")}) as client:
        response = client.get("/api/v1/web") if args.action == "status" else client.post("/api/v1/web", json={"enabled": args.action == "enable"})
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))


def mcp_main():
    from pbdesk.mcp_server.server import main
    return main()


def configure_connection(settings, reference, token=None):
    """Local admin uses the same signed core API as native setup."""
    import httpx
    from pbdesk.services.client_lifecycle import ClientLease
    from pbdesk.credentials import service_token
    lease = ClientLease(settings)
    try:
        lease.start()
        wait_for_history_ready(settings)
        with httpx.Client(base_url=settings.core_url, trust_env=False, follow_redirects=False,
                timeout=85, headers={'Authorization': 'Bearer ' + service_token(settings, 'desktop')}) as client:
            response = client.get('/api/v1/configuration')
            response.raise_for_status()
            value = {**reference, 'expected_revision': response.json()['setup']['revision']}
            if token is not None:
                value['token'] = token
            response = client.post('/api/v1/configuration/provider', json=value)
            response.raise_for_status()
    except HistoryNotReady:
        raise
    except Exception:
        raise RuntimeError('Could not save connection settings. ' + HISTORY_SETUP_HINT + ' Use Settings → Account after unlocking the credential store.') from None
    finally:
        lease.close()


def admin_main(argv=None):
    parser = argparse.ArgumentParser(description="Local secure setup (web accounts deferred)")
    parser.add_argument("action", choices=["provider-token", "use-kwallet", "reload-provider", "revoke-service", "install-service"])
    parser.add_argument("--role", choices=["desktop", "web", "mcp"])
    parser.add_argument("--wallet", help="Existing KWallet name; defaults to the network wallet")
    parser.add_argument("--folder", default="Pushbullet", help="KWallet folder (default: Pushbullet)")
    parser.add_argument("--entry", default="api-key", help="KWallet password entry (default: api-key)")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    from pbdesk.credentials import CredentialLocked, revoke_service_token
    if args.action == "reload-provider":
        from pbdesk.services.lifecycle import ensure_core, CoreStartupError
        from pbdesk.credentials import service_token
        import httpx
        try:
            ensure_core(settings)
            wait_for_history_ready(settings)
        except (CoreStartupError, HistoryNotReady):
            parser.exit(1, HISTORY_SETUP_HINT + '\n')
        with httpx.Client(base_url=settings.core_url,trust_env=False,follow_redirects=False,timeout=30,
                headers={"Authorization":"Bearer "+service_token(settings,"desktop")}) as client:
            response=client.post("/api/v1/provider/reload")
            response.raise_for_status()
            print(json.dumps(response.json(),indent=2))
    elif args.action == "use-kwallet":
        try:
            configure_connection(settings, {'backend': 'kwallet', 'folder': args.folder, 'entry': args.entry, 'wallet': args.wallet or 'kdewallet'})
        except (CredentialLocked, RuntimeError) as exc:
            parser.exit(1, str(exc) + ". No credential source was changed.\n")
        print("Selected existing KWallet password entry. Its location was saved in encrypted history and synchronization is starting.")
    elif args.action == "provider-token":
        token = getpass.getpass("Pushbullet access token (secure keyring only): ")
        if not token.strip():
            parser.error("Access token cannot be empty")
        try:
            configure_connection(settings, {'backend': 'keyring'}, token)
        except (CredentialLocked, RuntimeError) as exc:
            parser.exit(1, str(exc) + "; unlock Secret Service/KWallet and retry. No token was saved to a file.\n")
        print("Provider token securely saved and verified. Encrypted connection settings were updated.")
    elif args.action == "revoke-service":
        if not args.role:
            parser.error("--role is required")
        revoke_service_token(settings, args.role)
        print("Service token rotated; clients must reconnect.")
    else:
        # Explicit local operation, never runs on application launch.
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
        root.mkdir(parents=True, exist_ok=True)
        def quote(value):
            return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        image = appimage_path() if getattr(sys, "frozen", False) else None
        launch = [str(image), "--core", "--foreground", "--standalone"] if image else command("pbdesk.cli", "--foreground", "--standalone")
        unit = "[Unit]\nDescription=pbdesk private inbox core\n\n[Service]\nType=simple\nExecStart=" + " ".join(quote(part) for part in launch) + "\n"
        for key, value in {"PBDESK_APPLICATION_DIR": str(settings.application_dir), "PBDESK_STATE_DIR": str(settings.state_dir), "PBDESK_CORE_PORT": str(settings.core_port), "PBDESK_WEB_PORT": str(settings.web_port)}.items():
            unit += "Environment=" + quote(key + "=" + value) + "\n"
        unit += "Restart=on-failure\nRestartSec=3\nKillMode=process\nUMask=0077\n\n[Install]\nWantedBy=default.target\n"
        path = root / "pbdesk-core.service"
        path.write_text(unit)
        result = subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, env=system_environment())
        if result.returncode:
            parser.exit(1, "Unit written, but user systemd is unavailable. Run daemon-reload in your desktop session.\n")
        print(f"Installed {path}. Login autostart remains off; use systemctl --user enable explicitly if wanted.")


def diagnostics_main():
    from pbdesk.credentials import health_headers, verify_health
    from pbdesk.storage.cipher import driver
    import httpx
    settings = Settings.from_env()
    versions = {}
    for name in ("pbdesk", "fastapi", "uvicorn", "pydantic", "SQLAlchemy", "alembic", "httpx", "websockets", "keyring", "cryptography", "PyQt6", "Jinja2", "mcp", "sqlcipher3"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not installed"
    report = {"python": sys.version.split()[0], "dependencies": versions}
    try:
        cipher = driver()
        with cipher.connect(':memory:') as conn:
            report['sqlcipher'] = conn.execute('PRAGMA cipher_version').fetchone()[0]
            conn.execute('CREATE VIRTUAL TABLE probe USING fts5(value)')
            report['fts5'] = True
    except Exception:
        report['sqlcipher'] = 'unavailable; encrypted history cannot open'
        report['fts5'] = False
    from pbdesk.services.history_security import HistorySecurity
    report['history_security'] = HistorySecurity(settings).inspect()['state']
    report['credential_backend'] = 'Existing KWallet; authorization happens in desktop setup'
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            headers = health_headers(settings, "desktop")
            response = client.get(settings.core_url + "/api/v1/health", headers=headers)
            body = response.json()
            report["core"] = "ready" if response.status_code == 200 and verify_health(settings, "desktop", headers, body) and body["ready"] else "incompatible/unready"
            if report["core"] == "ready" and body.get("history_encryption") != 1:
                report["core"] = "restart required for mandatory encrypted history"
            if report["core"] == "ready" and body.get("history_encryption") == 1:
                report["history_ready"] = body.get("history_ready", False)
                from pbdesk.credentials import service_token
                caps = client.get(settings.core_url + "/api/v1/capabilities", headers={"Authorization": "Bearer " + service_token(settings, "desktop")}).json()
                report["provider_state"] = caps.get("provider_state", "unknown")
                for key in ("encryption_state","encrypted_pending","send_ready","send_unavailable_reason"):
                    report[key]=caps.get(key)
    except (httpx.HTTPError, ValueError):
        report["core"] = "unavailable"
    try:
        result = subprocess.run(["systemctl", "--user", "is-active", "pbdesk-core.service"], capture_output=True, text=True, timeout=2, env=system_environment())
        report["service"] = result.stdout.strip() or "user service manager unavailable"
    except (OSError, subprocess.TimeoutExpired):
        report["service"] = "unavailable"
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    core_main()
