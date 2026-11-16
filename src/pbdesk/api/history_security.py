"""Desktop-only encrypted history bootstrap; no history access before completion."""
import asyncio
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool
from pbdesk.services.core_settings import finish_mutation
from pbdesk.services.history_security import HistorySecurityError


class Recovery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recovery_key: SecretStr = Field(min_length=1, max_length=16384, repr=False)


class RecoveryOffer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    saved: StrictBool


def install_history_security_routes(app, authorization):
    async def desktop(role=Depends(authorization)):
        if role != 'desktop':
            raise HTTPException(403, 'Local desktop authorization required')

    async def status():
        data = await asyncio.to_thread(app.state.history_security.inspect)
        app.state.history_status_cache = {**data, 'history_ready': app.state.history_ready,
                'unlocked': app.state.store is not None}
        return app.state.history_status_cache

    async def mutate(operation):
        # Shield the complete lock ownership, not just to_thread: a cancelled HTTP
        # request must never let a second enrollment overlap the first wallet call.
        async def owned():
            async with app.state.history_lock:
                try:
                    await operation()
                    return await status()
                except HistorySecurityError as exc:
                    raise HTTPException(409, {'code': exc.code, 'message': exc.message}) from None
        return await finish_mutation(owned())

    @app.get('/api/v1/history/security', dependencies=[Depends(desktop)])
    async def security():
        if app.state.history_lock.locked():
            return {**app.state.history_status_cache, 'busy': True}
        async with app.state.history_lock:
            return await status()

    async def open_history(method, *args, **kwargs):
        if app.state.store is None:
            app.state.store = await asyncio.to_thread(method, *args, **kwargs)
        if not (await asyncio.to_thread(app.state.history_security.inspect))['recovery_offer_pending']:
            await app.state.activate_history(app.state.store)

    @app.post('/api/v1/history/protect', dependencies=[Depends(desktop)])
    async def protect():
        return await mutate(lambda: open_history(app.state.history_security.protect))

    @app.post('/api/v1/history/unlock', dependencies=[Depends(desktop)])
    async def unlock():
        return await mutate(lambda: open_history(app.state.history_security.open_existing, interactive=True))

    @app.post('/api/v1/history/migrate', dependencies=[Depends(desktop)])
    async def migrate():
        return await mutate(lambda: open_history(app.state.history_security.migrate))

    @app.post('/api/v1/history/recover', dependencies=[Depends(desktop)])
    async def recover(value: Recovery):
        return await mutate(lambda: open_history(app.state.history_security.recover, value.recovery_key.get_secret_value()))

    @app.post('/api/v1/history/recovery-key', dependencies=[Depends(desktop)])
    async def recovery_key():
        async def disclose():
            async with app.state.history_lock:
                if app.state.store is None:
                    raise HTTPException(423, 'Unlock local history before exporting its recovery key')
                try:
                    return {'recovery_key': await asyncio.to_thread(app.state.history_security.recovery_key)}
                except HistorySecurityError as exc:
                    raise HTTPException(409, {'code': exc.code, 'message': exc.message}) from None
        return await finish_mutation(disclose())

    @app.post('/api/v1/history/recovery-offer', dependencies=[Depends(desktop)])
    async def recovery_offer(value: RecoveryOffer):
        async def finish():
            if app.state.store is None:
                raise HTTPException(423, 'Unlock local history before continuing')
            await asyncio.to_thread(app.state.history_security.finish_recovery_offer, app.state.store, saved=value.saved)
            await app.state.activate_history(app.state.store)
        return await mutate(finish)
