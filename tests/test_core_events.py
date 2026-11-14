import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from pbdesk.api.app import create_app
from pbdesk.api.streaming import BoundedStreamingResponse
from pbdesk.config import Settings
from pbdesk.storage.database import Store
from pbdesk.pushbullet.normalize import DEMO,normalize


def test_slow_asgi_send_disconnects():
    async def run():
        async def content(): yield b"event: change\ndata: {}\n\n"
        async def receive():
            await asyncio.sleep(100)
        async def blocked_send(message):
            await asyncio.sleep(100)
        response=BoundedStreamingResponse(content())
        response.send_timeout=.01
        await asyncio.wait_for(response({"type":"http","asgi":{"spec_version":"2.4"}},receive,blocked_send),.2)
    asyncio.run(run())


def test_event_replay_and_resync(tmp_path):
    async def run():
        app=create_app(Settings(tmp_path),demo=True)
        async with app.router.lifespan_context(app):
            # Test stream iterator without HTTP client buffering an infinite response.
            route=next(route for route in app.routes if route.path=="/api/v1/events")
            class Request:
                headers = {}
                async def is_disconnected(self): return False
            response=await route.endpoint(Request(),after=0,instance_id=app.state.store.instance_id,last_event_id=None)
            iterator=response.body_iterator
            event=await anext(iterator)
            assert "event: change" in event and '"sequence": 1' in event
            await iterator.aclose()
            response=await route.endpoint(Request(),after=999,instance_id="old",last_event_id=None)
            iterator=response.body_iterator
            assert "event: resync" in await anext(iterator)
            await iterator.aclose()
    asyncio.run(run())
