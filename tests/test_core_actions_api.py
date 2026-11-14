"""All sending tests use an injected in-process fake provider, never a real account."""
import asyncio
import time
from uuid import uuid4
from fastapi.testclient import TestClient
from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import service_token

DEVICE={"id":"fake-phone","nickname":"fake","type":"android","manufacturer":None,"model":None,"active":True,"can_push":True,"can_sms":True}


class FakeProvider:
    state="connected"
    send_ready=True
    def __init__(self,settings,store,ingest,encryption):
        self.store,self.ingest,self.encryption=store,ingest,encryption
        self.calls=[]
        self.store.save_devices([DEVICE])
    async def run(self): await asyncio.Event().wait()
    async def close(self): pass
    async def refresh_devices(self): self.store.save_devices([DEVICE])
    async def reload_encryption(self): pass
    async def send(self,action): self.calls.append(action);return "fake-upstream"


def headers(settings,role="desktop"):
    return {"Authorization":"Bearer "+service_token(settings,role)}


def payload(**updates):
    value={"client_request_id":str(uuid4()),"kind":"note","device_id":"fake-phone","title":"reviewed","body":"exact fake content","transport":"provider_plaintext"}
    value.update(updates);return value


def test_mcp_can_request_never_self_approve_and_exact_desktop_approval(tmp_path,monkeypatch):
    settings=Settings(tmp_path);created=[]
    def factory(*args):
        selected=FakeProvider(*args);created.append(selected);return selected
    monkeypatch.setattr("pbdesk.api.app.provider_token",lambda *args:(_ for _ in ()).throw(AssertionError("Real credentials must not be read")))
    with TestClient(create_app(settings,demo=True,provider_factory=factory)) as client:
        mcp=headers(settings,"mcp");desktop=headers(settings)
        caps=client.get("/api/v1/capabilities",headers=mcp).json()
        assert caps["features"]["send_sms"] and caps["send_ready"]
        listed=client.get("/api/v1/devices",headers=mcp).json()["devices"]
        assert [{key:row[key] for key in DEVICE} for row in listed]==[DEVICE]
        assert listed[0]["encryption"]["entry"]=="e2e-password"
        assert listed[0]["reachable"] is None
        assert client.post("/api/v1/devices/refresh",headers=mcp).status_code==403
        request=payload()
        response=client.post("/api/v1/actions",headers=mcp,json=request)
        assert response.status_code==200,response.text
        action=response.json();action_id=action["id"]
        assert action["requested_by"]=="mcp" and action["state"]=="pending_approval"
        approval={"revision":action["revision"],"fingerprint":action["fingerprint"],"allow_provider_plaintext":True}
        assert client.post(f"/api/v1/actions/{action_id}/approve",headers=mcp,json=approval).status_code==403
        assert client.patch(f"/api/v1/actions/{action_id}",headers=mcp,json={}).status_code==403
        assert client.post(f"/api/v1/actions/{action_id}/cancel",headers=mcp,json={"revision":1}).status_code==403
        assert client.post("/api/v1/actions",headers=mcp,json=payload(confirmed=True)).status_code==422
        assert client.post("/api/v1/actions",headers=mcp,json={key:value for key,value in payload().items() if key!="transport"}).status_code==422
        assert client.post(f"/api/v1/actions/{action_id}/approve",headers=desktop,json={**approval,"allow_provider_plaintext":"true"}).status_code==422
        assert client.post(f"/api/v1/actions/{action_id}/approve",headers=desktop,json={**approval,"allow_provider_plaintext":False}).status_code==409
        assert not created[0].calls
        assert client.post(f"/api/v1/actions/{action_id}/approve",headers=desktop,json=approval).json()["state"]=="ready"
        for _ in range(80):
            current=client.get(f"/api/v1/actions/{action_id}",headers=mcp).json()
            if current["state"]=="upstream_accepted":break
            time.sleep(.025)
        assert current["state"]=="upstream_accepted"
        assert current["upstream_id"]=="fake-upstream" and len(created[0].calls)==1
        assert client.post("/api/v1/actions",headers=mcp,json=request).json()["id"]==action_id
        assert client.post(f"/api/v1/actions/{action_id}/approve",headers=desktop,json=approval).status_code==409
        assert len(created[0].calls)==1


def test_edit_cas_cancel_and_web_denial(tmp_path):
    settings=Settings(tmp_path)
    with TestClient(create_app(settings,demo=True,provider_factory=FakeProvider)) as client:
        desktop=headers(settings)
        action=client.post("/api/v1/actions",headers=desktop,json=payload()).json()
        action_id=action["id"]
        edited=client.patch(f"/api/v1/actions/{action_id}",headers=desktop,json={"expected_revision":1,"kind":"link","device_id":"fake-phone","recipient":None,"title":"edited","body":"new exact","url":"https://example.com/","transport":"provider_plaintext"}).json()
        assert edited["revision"]==2 and edited["expires_at"]==action["expires_at"]
        assert client.post(f"/api/v1/actions/{action_id}/approve",headers=desktop,json={"revision":1,"fingerprint":action["fingerprint"],"allow_provider_plaintext":True}).status_code==409
        assert client.post(f"/api/v1/actions/{action_id}/cancel",headers=desktop,json={"revision":1}).status_code==409
        assert client.post(f"/api/v1/actions/{action_id}/cancel",headers=desktop,json={"revision":2}).json()["state"]=="cancelled"
        web=headers(settings,"web")
        assert client.get("/api/v1/actions",headers=web).status_code==403
        assert client.post("/api/v1/actions",headers=web,json=payload()).status_code==403
        assert client.post("/api/v1/provider/reload",headers=web).status_code==403


def test_provider_reload_retries_locked_ciphertext_without_secret_request_body(tmp_path):
    from test_crypto_pushbullet import KEY,encrypt_object
    class CryptoFake(FakeProvider):
        async def reload_encryption(self):
            async def loader():return KEY
            await self.encryption.reload(loader)
    settings=Settings(tmp_path)
    app=create_app(settings,demo=True,provider_factory=CryptoFake)
    with TestClient(app) as client:
        encrypted={"type":"push","push":{"encrypted":True,"ciphertext":encrypt_object({"type":"mirror","source_device_iden":"fake-phone","package_name":"fake","notification_id":1,"title":"decrypted","body":"fake"})}}
        client.portal.call(app.state.ingest,encrypted)
        before=client.get('/api/v1/capabilities',headers=headers(settings)).json()
        assert before['encrypted_pending']==1
        after=client.post('/api/v1/provider/reload',headers=headers(settings)).json()
        assert after['encryption_state']=='ready' and after['encrypted_pending']==0
        values=client.get('/api/v1/items',headers=headers(settings)).json()['items']
        assert any(value['title']=='decrypted' for value in values)
