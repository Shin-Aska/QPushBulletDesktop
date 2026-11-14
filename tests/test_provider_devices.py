import asyncio
import httpx
from pbdesk.config import Settings
from pbdesk.storage.database import Store
from pbdesk.pushbullet.provider import Provider


def test_device_cache_safelists_fields_and_retains_inactive(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));requests=[]
        async def ingest(value):pass
        provider=Provider('fake-token',store,ingest);await provider.client.aclose()
        rows=[
            dict(iden='one',nickname='phone',icon='phone',type='legacy',active=True,pushable=True,has_sms=True,push_token='secret-do-not-expose'),
            dict(iden='two',nickname='optional',active=True),
            dict(iden='three',active=True,pushable=False,has_sms='true'),
            dict(iden='inactive',active=False,pushable=True,has_sms=True),
        ]
        def handler(request):
            requests.append(request);return httpx.Response(200,json={'devices':rows})
        provider.client=httpx.AsyncClient(base_url='https://api.pushbullet.com/v2',transport=httpx.MockTransport(handler))
        await provider.refresh_devices()
        values={row['id']:row for row in store.device_list()}
        assert values['one']['type']=='phone' and values['one']['can_sms']
        assert values['two']['can_push'] and not values['two']['can_sms']
        assert not values['three']['can_push'] and not values['three']['can_sms']
        assert not values['inactive']['can_push'] and not values['inactive']['can_sms']
        assert all('push_token' not in row for row in values.values())
        assert store.get_setting('devices_synced_at')
        assert len(requests)==1 and requests[0].method=='GET'
        await provider.close();store.close()
    asyncio.run(run())
