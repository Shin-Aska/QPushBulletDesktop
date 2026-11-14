"""Status-only web/MCP boundaries and desktop CRUD use in-process fake providers."""
import asyncio
import pytest
from fastapi.testclient import TestClient
from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import service_token
from test_sms_history import PHONE,ROWS,THREAD
from pbdesk.pushbullet.sms import SmsHistory


class FakeProvider:
    state='connected';send_ready=False
    def __init__(self,settings,store,ingest,encryption):
        self.store=store;self.calls=[];self.key_states={'phone':'key_ready'}
        store.save_devices([PHONE]);store.set_setting('pushbullet_account_id','fake-account')
        store.merge_sms_thread('fake-account','phone','conversation',SmsHistory.normalize_thread(THREAD,ROWS,False),'fixture')
    async def run(self): await asyncio.Event().wait()
    async def close(self): pass
    async def refresh_devices(self): self.calls.append('refresh')
    async def request_sms_refresh(self,device): self.calls.append(('history',device))
    async def register_device(self,nickname): self.calls.append(('add',nickname))
    async def rename_device(self,device,nickname): self.calls.append(('rename',device,nickname))
    async def remove_device(self,device): self.calls.append(('delete',device));self.store.deactivate_device(device)
    async def reload_encryption(self): pass
    async def test_encryption(self,device): return False


def test_cache_only_reads_queue_refresh_and_desktop_mutation_scope(tmp_path):
    settings=Settings(tmp_path);app=create_app(settings,demo=True,provider_factory=FakeProvider)
    headers=lambda role:{'Authorization':'Bearer '+service_token(settings,role)}
    with TestClient(app) as client:
        desktop=headers('desktop')
        result=client.get('/api/v1/sms?device_id=phone&direction=outgoing',headers=desktop).json()
        assert len(result['messages'])==1 and result['messages'][0]['direction']=='outgoing'
        assert not app.state.provider.calls
        assert client.get('/api/v1/sms?device_id=phone&direction=invalid',headers=desktop).status_code==422
        assert client.post('/api/v1/sms/refresh',headers=desktop,json={'device_id':'phone'}).status_code==200
        assert app.state.provider.calls==[('history','phone')]
        for role in ('mcp','web'):
            assert client.get('/api/v1/sms?device_id=phone',headers=headers(role)).status_code==403
            for method,path,data in [('post','/api/v1/devices',{'nickname':'new'}),('patch','/api/v1/devices/phone',{'nickname':'renamed'}),('delete','/api/v1/devices/phone',None),('post','/api/v1/devices/phone/encryption/test',{})]:
                assert client.request(method,path,headers=headers(role),json=data).status_code==403
        assert client.post('/api/v1/devices',headers=desktop,json={'nickname':'new'}).status_code==200
        assert client.patch('/api/v1/devices/phone',headers=desktop,json={'nickname':'renamed'}).status_code==200
        assert client.delete('/api/v1/devices/phone',headers=desktop).status_code==200
        assert len(client.get('/api/v1/sms?device_id=phone',headers=desktop).json()['messages'])==2
        assert not app.state.store.action_list()
        assert client.post('/api/v1/devices/phone/encryption/test',headers=desktop).json()['verified'] is False
        devices=client.get('/api/v1/devices',headers=desktop).json()['devices']
        assert devices[0]['encryption']['state']=='key_ready' and devices[0]['reachable'] is None


def test_local_encryption_selection_no_secret_reflection_and_protected_defaults(tmp_path,monkeypatch):
    from pbdesk import credentials
    settings=Settings(tmp_path);app=create_app(settings,demo=True,provider_factory=FakeProvider);created=[]
    monkeypatch.setattr(credentials,'device_e2e_password',lambda *args:'fake-password')
    monkeypatch.setattr(credentials,'create_device_e2e_password',lambda *args:created.append(args))
    with TestClient(app) as client:
        headers={'Authorization':'Bearer '+service_token(settings,'desktop')}
        secret='fixture-secret'
        response=client.post('/api/v1/devices/phone/encryption',headers=headers,json={'mode':'device','entry':'phone-private','password':secret})
        assert response.status_code==200 and secret not in response.text
        assert response.json()['encryption']['entry']=='phone-private'
        assert len(created)==1
        assert client.post('/api/v1/devices/phone/encryption',headers=headers,json={'mode':'shared','password':secret}).status_code==422
        assert client.post('/api/v1/devices/phone/encryption',headers=headers,json={'mode':'device','entry':'api-key','password':secret}).status_code==422
        invalid=client.post('/api/v1/devices/phone/encryption',headers=headers,json={'mode':'device','entry':'phone-private','password':{'secret':secret}})
        assert invalid.status_code==422 and secret not in invalid.text
        assert secret.encode() not in app.state.store.path.read_bytes()
