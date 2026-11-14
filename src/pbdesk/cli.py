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
from pbdesk.config import Settings


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


def web_main():
    """Control web through core; the gateway is never another data owner."""
    parser = argparse.ArgumentParser(description="Optional local web host control")
    parser.add_argument("action", choices=["status", "enable", "disable"], nargs="?", default="status")
    args = parser.parse_args()
    from pbdesk.credentials import service_token
    from pbdesk.services.lifecycle import ensure_core
    import httpx
    settings = Settings.from_env()
    ensure_core(settings)
    with httpx.Client(base_url=settings.core_url, trust_env=False, timeout=8,
                      headers={"Authorization": "Bearer " + service_token(settings, "desktop")}) as client:
        response = client.get("/api/v1/web") if args.action == "status" else client.post("/api/v1/web", json={"enabled": args.action == "enable"})
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))


def mcp_main():
    from pbdesk.mcp_server.server import main
    return main()


def admin_main(argv=None):
    parser = argparse.ArgumentParser(description="Local secure setup (web accounts deferred)")
    parser.add_argument("action", choices=["provider-token", "use-kwallet", "reload-provider", "revoke-service", "install-service"])
    parser.add_argument("--role", choices=["desktop", "web", "mcp"])
    parser.add_argument("--wallet", help="Existing KWallet name; defaults to the network wallet")
    parser.add_argument("--folder", default="Pushbullet", help="KWallet folder (default: Pushbullet)")
    parser.add_argument("--entry", default="api-key", help="KWallet password entry (default: api-key)")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    from pbdesk.credentials import CredentialLocked, store_provider_token, revoke_service_token, use_kwallet
    if args.action == "reload-provider":
        from pbdesk.services.lifecycle import ensure_core
        from pbdesk.credentials import service_token
        import httpx
        ensure_core(settings)
        with httpx.Client(base_url=settings.core_url,trust_env=False,follow_redirects=False,timeout=30,
                headers={"Authorization":"Bearer "+service_token(settings,"desktop")}) as client:
            response=client.post("/api/v1/provider/reload")
            response.raise_for_status()
            print(json.dumps(response.json(),indent=2))
    elif args.action == "use-kwallet":
        try:
            use_kwallet(settings, folder=args.folder, entry=args.entry, wallet=args.wallet)
        except CredentialLocked as exc:
            parser.exit(1, str(exc) + ". No credential source was changed.\n")
        print("Selected existing KWallet password entry. Only its location was saved. Restart the core to connect.")
    elif args.action == "provider-token":
        token = getpass.getpass("Pushbullet access token (secure keyring only): ")
        if not token.strip():
            parser.error("Access token cannot be empty")
        try:
            store_provider_token(token, settings)
        except CredentialLocked as exc:
            parser.exit(1, str(exc) + "; unlock Secret Service/KWallet and retry. No token was saved to a file.\n")
        print("Provider token securely saved and read-back verified. Restart core to connect.")
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
        unit = "[Unit]\nDescription=pbdesk private inbox core\n\n[Service]\nType=simple\nExecStart=" + quote(sys.executable) + " -m pbdesk.cli --foreground --standalone\n"
        for key, value in {"PBDESK_APPLICATION_DIR": str(settings.application_dir), "PBDESK_STATE_DIR": str(settings.state_dir), "PBDESK_CORE_PORT": str(settings.core_port), "PBDESK_WEB_PORT": str(settings.web_port)}.items():
            unit += "Environment=" + quote(key + "=" + value) + "\n"
        unit += "Restart=on-failure\nRestartSec=3\nKillMode=process\nUMask=0077\n\n[Install]\nWantedBy=default.target\n"
        path = root / "pbdesk-core.service"
        path.write_text(unit)
        result = subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        if result.returncode:
            parser.exit(1, "Unit written, but user systemd is unavailable. Run daemon-reload in your desktop session.\n")
        print(f"Installed {path}. Login autostart remains off; use systemctl --user enable explicitly if wanted.")


def diagnostics_main():
    from pbdesk.credentials import CredentialLocked, secure_backend, health_headers, verify_health, provider_credential_source
    import sqlite3
    import httpx
    settings = Settings.from_env()
    versions = {}
    for name in ("pbdesk", "fastapi", "uvicorn", "pydantic", "SQLAlchemy", "alembic", "httpx", "websockets", "keyring", "cryptography", "PyQt6", "Jinja2", "mcp"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not installed"
    report = {"python": sys.version.split()[0], "dependencies": versions, "sqlite": sqlite3.sqlite_version}
    with sqlite3.connect(":memory:") as conn:
        try:
            conn.execute("CREATE VIRTUAL TABLE probe USING fts5(value)")
            report["fts5"] = True
        except sqlite3.OperationalError:
            report["fts5"] = False
    try:
        source = provider_credential_source(settings)
        report["provider_credential_source"] = source
        report["credential_backend"] = "org.kde.KWallet (configured)" if source["backend"] == "kwallet" else type(secure_backend()).__module__
    except CredentialLocked:
        report["credential_backend"] = "locked/unavailable (no plaintext fallback)"
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            headers = health_headers(settings, "desktop")
            response = client.get(settings.core_url + "/api/v1/health", headers=headers)
            body = response.json()
            report["core"] = "ready" if response.status_code == 200 and verify_health(settings, "desktop", headers, body) and body["ready"] else "incompatible/unready"
            if report["core"] == "ready":
                from pbdesk.credentials import service_token
                caps = client.get(settings.core_url + "/api/v1/capabilities", headers={"Authorization": "Bearer " + service_token(settings, "desktop")}).json()
                report["provider_state"] = caps.get("provider_state", "unknown")
                for key in ("encryption_state","encrypted_pending","send_ready","send_unavailable_reason"):
                    report[key]=caps.get(key)
    except (httpx.HTTPError, ValueError):
        report["core"] = "unavailable"
    try:
        result = subprocess.run(["systemctl", "--user", "is-active", "pbdesk-core.service"], capture_output=True, text=True, timeout=2)
        report["service"] = result.stdout.strip() or "user service manager unavailable"
    except (OSError, subprocess.TimeoutExpired):
        report["service"] = "unavailable"
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    core_main()
