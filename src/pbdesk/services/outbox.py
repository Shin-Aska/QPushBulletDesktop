"""Exactly one dispatch owner; durable claims and conservative uncertain outcomes."""
import asyncio
from datetime import datetime
from pbdesk.domain.actions import InvalidDestination
from pbdesk.pushbullet.provider import SubmissionError
from pbdesk.storage.workflows import utcnow


class Dispatcher:
    def __init__(self,store,provider_getter):
        self.store=store
        self.provider_getter=provider_getter
        self.lock=asyncio.Lock()

    async def dispatch_once(self):
        async with self.lock:
            ids=await asyncio.to_thread(self.store.ready_actions)
            if not ids:
                return
            provider=self.provider_getter()
            if provider is None or not provider.send_ready:
                return
            # A failed refresh leaves approved actions queued; it does not send
            # against an unverifiable stale device cache.
            try:
                await asyncio.wait_for(provider.refresh_devices(),timeout=20)
            except asyncio.CancelledError:
                raise
            except Exception:
                return
            for action_id in ids:
                action=await asyncio.to_thread(self.store.claim_action,action_id)
                if action is None:
                    continue
                try:
                    await asyncio.to_thread(self.store.validate_device,action)
                    if datetime.fromisoformat(action["expires_at"])<=utcnow():
                        raise SubmissionError("failed","expired","Action expired before submission")
                    upstream_id=await provider.send(action)
                except InvalidDestination:
                    await asyncio.to_thread(self.store.finish_action,action_id,"failed","device_unavailable","Device no longer supports the approved channel")
                except SubmissionError as exc:
                    await asyncio.to_thread(self.store.finish_action,action_id,exc.state,exc.code,exc.message)
                except asyncio.CancelledError:
                    # No automatic retry. Shield persistence if cancellation arrives
                    # after claim; restart recovery provides the same fallback.
                    await asyncio.shield(asyncio.to_thread(self.store.finish_action,action_id,"uncertain","submission_interrupted","Submission was interrupted; do not blindly retry"))
                    raise
                except Exception:
                    await asyncio.to_thread(self.store.finish_action,action_id,"uncertain","submission_outcome_unknown","Submission outcome is unknown; do not blindly retry")
                else:
                    await asyncio.to_thread(self.store.finish_action,action_id,"upstream_accepted",None,None,upstream_id)

    async def run(self):
        while True:
            await self.dispatch_once()
            await asyncio.sleep(.5)
