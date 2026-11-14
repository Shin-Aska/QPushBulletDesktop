import asyncio
import httpx
import pytest
from pbdesk.pushbullet.provider import Provider, RateLimited, ProviderUnauthorized
from pbdesk.config import Settings
from pbdesk.storage.database import Store


def test_documented_sync_pagination_and_cursor(tmp_path):
    async def run():
        store=Store(Settings(tmp_path))
        seen=[]; requests=[]
        async def ingest(payload): seen.append(payload)
        provider=Provider("not-real",store,ingest)
        await provider.client.aclose()
        def handler(request):
            requests.append(request)
            assert request.method == "GET"
            assert request.headers["Access-Token"] == "not-real"
            if "cursor" not in request.url.params:
                return httpx.Response(200,json={"pushes":[{"iden":"one","modified":12}],"cursor":"page2"})
            return httpx.Response(200,json={"pushes":[{"iden":"two","modified":14}]})
        provider.client=httpx.AsyncClient(base_url="https://api.pushbullet.com/v2",headers={"Access-Token":"not-real"},transport=httpx.MockTransport(handler))
        await provider.sync()
        assert len(seen) == len(requests) == 2
        assert store.get_setting("pushbullet_modified") == 14
        assert requests[0].url.params["modified_after"] == "0"
        await provider.close();store.close()
    asyncio.run(run())


def test_rate_limit_does_not_advance_cursor(tmp_path):
    async def run():
        store=Store(Settings(tmp_path))
        async def ingest(payload): pass
        provider=Provider("not-real",store,ingest)
        await provider.client.aclose()
        provider.client=httpx.AsyncClient(base_url="https://api.pushbullet.com/v2",transport=httpx.MockTransport(lambda r:httpx.Response(429,headers={"Retry-After":"3"})))
        with pytest.raises(RateLimited) as exc: await provider.sync()
        assert exc.value.delay == 3
        assert store.get_setting("pushbullet_modified") is None
        await provider.close();store.close()
    asyncio.run(run())


def test_mixed_malformed_rows_and_tombstones_preserve_remote_cursor(tmp_path):
    from pbdesk.pushbullet.normalize import normalize
    async def run():
        store=Store(Settings(tmp_path))
        ingested=[]
        async def ingest(payload):
            item=normalize(payload)
            if item:
                ingested.append(item)
                store.ingest(item)
        provider=Provider("not-real",store,ingest)
        await provider.client.aclose()
        rows=[
            {"type":[],"iden":"malformed-type","created":0,"modified":2},
            {"type":{},"iden":"malformed-type2","created":0,"modified":3},
            {"type":"note","iden":"poison","created":"broken","modified":1e300},
            {"type":"note","iden":"infinite","created":0,"modified":float("inf")},
            {"type":"note","iden":"boolean","created":0,"modified":True},
            {"type":"note","iden":"future","created":0,"modified":253402300000},
            {"type":"note","iden":"good","created":4,"modified":10,"body":"valid"},
            {"iden":"deleted","active":False,"modified":12},
            {"type":"note","iden":"dismissed","dismissed":True,"created":0,"modified":13},
        ]
        # httpx's JSON encoder refuses Infinity (correctly), so supply provider
        # malformed JSON bytes to exercise permissive external JSON decoding too.
        import json
        def handler(request):
            return httpx.Response(200,content=json.dumps({"pushes":rows}),headers={"Content-Type":"application/json","Date":"Tue, 01 Jan 2030 00:00:00 GMT"})
        provider.client=httpx.AsyncClient(base_url="https://api.pushbullet.com/v2",transport=httpx.MockTransport(handler))
        await provider.sync()
        assert [item["source_id"] for item in ingested] == ["stored:good"]
        assert store.get_setting("pushbullet_modified") == 13
        assert len(store.list_items()) == 1
        await provider.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize("retry_after", ["nan","inf","-inf","not-a-number"])
def test_nonfinite_rate_limit_delay_is_bounded(tmp_path,retry_after):
    async def run():
        store=Store(Settings(tmp_path))
        async def ingest(payload): pass
        provider=Provider("not-real",store,ingest)
        await provider.client.aclose()
        provider.client=httpx.AsyncClient(base_url="https://api.pushbullet.com/v2",transport=httpx.MockTransport(lambda r:httpx.Response(429,headers={"Retry-After":retry_after})))
        with pytest.raises(RateLimited) as exc: await provider.sync()
        assert exc.value.delay == 60
        assert store.get_setting("pushbullet_modified") is None
        await provider.close();store.close()
    asyncio.run(run())


def test_poisoned_saved_cursor_is_recovered(tmp_path):
    async def run():
        store=Store(Settings(tmp_path))
        store.set_setting("pushbullet_modified",1e300)
        requests=[]
        async def ingest(payload): pass
        provider=Provider("not-real",store,ingest)
        await provider.client.aclose()
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={"pushes":[{"iden":"deleted","active":False,"modified":5}]})
        provider.client=httpx.AsyncClient(base_url="https://api.pushbullet.com/v2",transport=httpx.MockTransport(handler))
        await provider.sync()
        assert requests[0].url.params["modified_after"] == "0"
        assert store.get_setting("pushbullet_modified") == 5
        await provider.close();store.close()
    asyncio.run(run())


def test_push_sync_status_keeps_actual_last_success_on_read_error(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));fail=False
        async def ingest(value): pass
        provider=Provider('fake-token',store,ingest);await provider.client.aclose()
        def handler(request):
            return httpx.Response(503) if fail else httpx.Response(200,json={'pushes':[]})
        provider.client=httpx.AsyncClient(base_url='https://api.pushbullet.com/v2',transport=httpx.MockTransport(handler))
        await provider.sync()
        first=store.get_setting('push_sync')
        assert first['state']=='idle' and first['last_success_at']
        fail=True
        try:await provider.sync()
        except httpx.HTTPStatusError:pass
        else:raise AssertionError('Expected fake provider read failure')
        after=store.get_setting('push_sync')
        assert after['state']=='error' and after['last_success_at']==first['last_success_at']
        await provider.close();store.close()
    asyncio.run(run())
