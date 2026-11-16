from dataclasses import replace
from pathlib import Path
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import (health_headers, verify_health, service_token,
                               revoke_service_token, health_response, CredentialLocked)

@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path)

def auth(settings, role="desktop"):
    return {"Authorization": "Bearer " + service_token(settings, role)}

def test_bootstrap_and_roles(settings):
    with TestClient(create_app(settings, demo=True)) as client:
        assert client.get("/api/v1/health").status_code == 401
        assert client.get("/api/v1/health", headers=auth(settings)).status_code == 401
        headers = health_headers(settings, "desktop")
        response = client.get("/api/v1/health", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert verify_health(settings, "desktop", headers, body)
        assert response.headers["cache-control"] == "no-store"
        assert not verify_health(settings, "desktop", health_headers(settings, "desktop"), body)
        assert not verify_health(settings, "web", headers, body)
        assert not verify_health(settings, "desktop", headers, dict(body, ready=False))
        assert not verify_health(settings, "desktop", headers, dict(body, api_version=True))
        assert not verify_health(settings, "desktop", headers, dict(body, proof=headers["X-PBDesk-Proof"]))
        assert client.get("/api/v1/items").status_code == 401
        assert client.get("/api/v1/items", headers=auth(settings,"web")).status_code == 403
        assert client.get("/api/v1/capabilities", headers=auth(settings,"web")).status_code == 200
        assert client.get("/api/v1/items", headers=auth(settings,"mcp")).json()["items"]
        assert client.post("/api/v1/shutdown", headers=auth(settings,"mcp")).status_code == 403
        assert client.get("/api/v1/items", headers={**auth(settings),"Origin":"http://evil.example"}).status_code == 403
        old = auth(settings)
        revoke_service_token(settings,"desktop")
        assert client.get("/api/v1/items",headers=old).status_code == 401
        assert client.get("/api/v1/items",headers=auth(settings)).status_code == 200
        assert client.post("/api/v1/send",headers=auth(settings)).status_code == 404
        assert client.get("/api/v1/capabilities",headers=auth(settings)).json()["features"] == {"inbox":True,"search":True,"send":True,"send_note":True,"send_link":True,"send_sms":False,"sms_history":True,"device_management":True,"devices":True,"e2e_decrypt":True,"ai":True,"ai_summary":True,"ai_draft_reply":True,"ai_saved_summaries":True,"media_images":True,"web_inbox":False,"client_lifecycle":True,"core_settings":True,"core_restart":True,"configuration":True}

def test_credentials_permissions(settings):
    assert len({service_token(settings, role) for role in ("desktop","web","mcp")}) == 3
    assert settings.state_dir.stat().st_mode & 0o777 == 0o700
    assert (settings.state_dir / "service-credentials.json").stat().st_mode & 0o777 == 0o600

def test_locked_provider_has_no_plaintext_fallback(settings, monkeypatch):
    from pbdesk.configuration import save_provider
    save_provider(settings, {'backend': 'keyring'})
    def locked(*args):
        raise CredentialLocked("unavailable")
    monkeypatch.setattr("pbdesk.api.app.provider_token",locked)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/capabilities", headers=auth(settings)).json()["provider_state"] == "locked"
    assert not (settings.state_dir / "provider-token").exists()

def test_single_instance(settings):
    from pbdesk.services.instance import InstanceLock, AlreadyRunning
    lock = InstanceLock(settings)
    try:
        with pytest.raises(AlreadyRunning):
            InstanceLock(settings)
    finally:
        lock.close()
    InstanceLock(settings).close()

def test_demo_restart_claimed_once(settings):
    class FakeNotifier:
        enabled = True
        def __init__(self): self.items=[]
        async def notify(self,item): self.items.append(item)
    notifier=FakeNotifier()
    with TestClient(create_app(settings,demo=True,notifier=notifier)) as client:
        first=client.get("/api/v1/items",headers=auth(settings)).json()
    with TestClient(create_app(settings,demo=True,notifier=notifier)) as client:
        second=client.get("/api/v1/items",headers=auth(settings)).json()
    assert len(first["items"]) == len(second["items"]) == len(notifier.items) == 1
    assert first["latest_sequence"] == second["latest_sequence"] == 1
    assert first["instance_id"] != second["instance_id"]
