import asyncio
from pbdesk.config import Settings
from pbdesk.services.webhost import WebHost


def test_persisted_preference_and_listener_transition_share_lock(tmp_path,monkeypatch):
    async def run():
        host=WebHost(Settings(tmp_path))
        class Store:
            value=False
            def set_setting(self,key,value):
                assert key == "web_enabled"
                self.value=value
        host.store=Store()
        entered=asyncio.Event()
        release=asyncio.Event()
        transitions=[]
        async def fake_transition(enabled):
            if enabled:
                entered.set()
                await release.wait()
            transitions.append(enabled)
            return {"enabled":enabled}
        monkeypatch.setattr(host,"_set_enabled",fake_transition)
        enable=asyncio.create_task(host.set_enabled(True,persist=True))
        await entered.wait()
        disable=asyncio.create_task(host.set_enabled(False,persist=True))
        await asyncio.sleep(0)
        # The later request cannot persist False while enabling is still in flight.
        assert host.store.value is True
        release.set()
        results=await asyncio.gather(enable,disable)
        assert transitions == [True,False]
        assert host.store.value is results[-1]["enabled"] is False
    asyncio.run(run())
