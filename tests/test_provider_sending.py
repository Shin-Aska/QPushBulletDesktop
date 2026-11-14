import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from pbdesk.config import Settings
from pbdesk.storage.database import Store
from pbdesk.pushbullet.provider import Provider,SubmissionError


def action(kind="note"):
    return dict(kind=kind,device_id="fake-phone",title="exact",body="body",url="https://example.com/" if kind=="link" else None,
        recipient="+12025550123" if kind=="sms" else None,client_request_id=str(uuid4()),transport="provider_plaintext")


@pytest.mark.parametrize("kind",["note","link","sms"])
def test_documented_explicit_device_payloads_only(tmp_path,kind):
    async def run():
        store=Store(Settings(tmp_path));requests=[]
        async def ingest(value): pass
        provider=Provider("fake-token",store,ingest);await provider.client.aclose()
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={"iden":"fake-accepted"})
        provider.client=httpx.AsyncClient(base_url="https://api.pushbullet.com/v2",transport=httpx.MockTransport(handler))
        message=action(kind)
        assert await provider.send(message)=="fake-accepted"
        request=requests[0];body=json.loads(request.content)
        assert request.method=="POST" and len(requests)==1
        if kind=="sms":
            assert request.url.path=="/v2/texts"
            assert body=={"data":{"target_device_iden":"fake-phone","addresses":["+12025550123"],"message":"body","guid":message["client_request_id"]}}
        else:
            assert request.url.path=="/v2/pushes"
            assert body["device_iden"]=="fake-phone" and body["type"]==kind
            assert "transport" not in body and "recipient" not in body
        await provider.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize("status,state",[(400,"failed"),(401,"failed"),(429,"failed"),(500,"uncertain"),(503,"uncertain")])
def test_outbound_errors_are_classified_without_retry(tmp_path,status,state):
    async def run():
        store=Store(Settings(tmp_path));calls=[]
        async def ingest(value): pass
        provider=Provider("fake-token",store,ingest);await provider.client.aclose()
        def handler(request):
            calls.append(request)
            return httpx.Response(status,json={"error":"secret message content must not propagate"})
        provider.client=httpx.AsyncClient(base_url="https://api.pushbullet.com/v2",transport=httpx.MockTransport(handler))
        with pytest.raises(SubmissionError) as error:await provider.send(action())
        assert error.value.state==state and len(calls)==1
        assert "secret message" not in str(error.value)
        await provider.close();store.close()
    asyncio.run(run())


def test_network_loss_after_request_is_uncertain_and_not_retried(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));calls=[]
        async def ingest(value): pass
        provider=Provider("fake-token",store,ingest);await provider.client.aclose()
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("fake secret token request body")
        provider.client=httpx.AsyncClient(base_url="https://api.pushbullet.com/v2",transport=httpx.MockTransport(handler))
        with pytest.raises(SubmissionError) as error:await provider.send(action("sms"))
        assert error.value.state=="uncertain" and len(calls)==1
        assert "secret" not in str(error.value)
        await provider.close();store.close()
    asyncio.run(run())
