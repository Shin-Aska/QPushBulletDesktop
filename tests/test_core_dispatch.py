import asyncio
from uuid import uuid4
import pytest
from pbdesk.config import Settings
from pbdesk.storage.database import Store
from pbdesk.domain.actions import ActionRequest,Approval
from pbdesk.services.outbox import Dispatcher
from pbdesk.pushbullet.provider import SubmissionError

DEVICE=dict(id="fake",nickname="fake",type=None,manufacturer=None,model=None,active=True,can_push=True,can_sms=True)


def prepared(store):
    store.save_devices([DEVICE])
    action=store.create_action(ActionRequest(client_request_id=uuid4(),kind="note",device_id="fake",body="exact",transport="provider_plaintext"),"desktop")
    store.approve_action(action['id'],Approval(revision=1,fingerprint=action['fingerprint'],allow_provider_plaintext=True),"desktop")
    return action['id']


@pytest.mark.parametrize("state",["failed","uncertain"])
def test_submission_terminal_result_never_automatically_retries(tmp_path,state):
    async def run():
        store=Store(Settings(tmp_path));action_id=prepared(store)
        class Fake:
            send_ready=True
            calls=0
            async def refresh_devices(self):store.save_devices([DEVICE])
            async def send(self,action):
                self.calls+=1
                raise SubmissionError(state,"test_outcome","Sanitized fake outcome")
        provider=Fake();dispatcher=Dispatcher(store,lambda:provider)
        await dispatcher.dispatch_once();await dispatcher.dispatch_once()
        assert provider.calls==1 and store.action(action_id)['state']==state
        store.close()
    asyncio.run(run())


def test_device_revalidation_after_approval_prevents_submission(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));action_id=prepared(store)
        class Fake:
            send_ready=True
            async def refresh_devices(self):store.save_devices([])
            async def send(self,action):pytest.fail("Inactive device must not receive POST")
        dispatcher=Dispatcher(store,lambda:Fake())
        await dispatcher.dispatch_once()
        assert store.action(action_id)['state']=='failed'
        assert store.action(action_id)['error_code']=='device_unavailable'
        store.close()
    asyncio.run(run())


def test_refresh_failure_keeps_approval_queued_without_send(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));action_id=prepared(store)
        class Fake:
            send_ready=True
            async def refresh_devices(self):raise RuntimeError('fake unavailable')
            async def send(self,action):pytest.fail("Unverified device cache must not send")
        await Dispatcher(store,lambda:Fake()).dispatch_once()
        assert store.action(action_id)['state']=='ready'
        store.close()
    asyncio.run(run())


def test_cancellation_after_claim_is_uncertain(tmp_path):
    async def run():
        store=Store(Settings(tmp_path));action_id=prepared(store);attempted=asyncio.Event()
        class Fake:
            send_ready=True
            calls=0
            async def refresh_devices(self):store.save_devices([DEVICE])
            async def send(self,action):
                self.calls+=1
                attempted.set();await asyncio.Event().wait()
        provider=Fake()
        dispatcher=Dispatcher(store,lambda:provider)
        task=asyncio.create_task(dispatcher.dispatch_once())
        await attempted.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert store.action(action_id)['state']=='uncertain'
        await dispatcher.dispatch_once()
        assert provider.calls==1
        store.close()
    asyncio.run(run())
