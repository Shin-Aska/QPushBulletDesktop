"""Desktop-only encrypted history bootstrap; no history access before completion."""
import asyncio
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool
from pbdesk.services.core_settings import finish_mutation
from pbdesk.services.history_security import HistorySecurityError


class Recovery(BaseModel):
    """Request payload containing a history recovery key.

    :ivar recovery_key: Secret recovery key supplied to restore encrypted history access.
    :type recovery_key: pydantic.SecretStr
    """

    model_config = ConfigDict(extra='forbid')
    recovery_key: SecretStr = Field(min_length=1, max_length=16384, repr=False)


class RecoveryOffer(BaseModel):
    """Request payload acknowledging whether the recovery key was saved.

    :ivar saved: Whether the user confirmed saving the recovery key.
    :type saved: bool
    """

    model_config = ConfigDict(extra='forbid')
    saved: StrictBool


def install_history_security_routes(app, authorization):
    """Register desktop-only encrypted history bootstrap and recovery routes.

    :param app: FastAPI application instance to register routes on.
    :type app: fastapi.FastAPI
    :param authorization: FastAPI dependency verifying the bearer service role.
    :type authorization: collections.abc.Callable[..., collections.abc.Awaitable[str]]
    """
    async def desktop(role=Depends(authorization)):
        """Enforce local desktop authorization for history security endpoints.

        :param role: Authenticated caller role resolved by the authorization dependency.
        :type role: str
        :raises fastapi.HTTPException: If the caller role is not ``'desktop'`` (HTTP 403).
        """
        if role != 'desktop':
            raise HTTPException(403, 'Local desktop authorization required')

    async def status():
        """Refresh and return cached history encryption and unlock status.

        :return: Updated history security and readiness status dictionary.
        :rtype: dict[str, object]
        """
        data = await asyncio.to_thread(app.state.history_security.inspect)
        app.state.history_status_cache = {**data, 'history_ready': app.state.history_ready,
                'unlocked': app.state.store is not None}
        return app.state.history_status_cache

    async def mutate(operation):
        """Run a history security mutation under the history lock until completion.

        :param operation: Zero-argument async callable performing the history security transition.
        :type operation: collections.abc.Callable[[], collections.abc.Awaitable[object]]
        :return: Updated history security status dictionary after completion.
        :rtype: dict[str, object]
        """
        # Shield the complete lock ownership, not just to_thread: a cancelled HTTP
        # request must never let a second enrollment overlap the first wallet call.
        async def owned():
            """Execute the mutation while holding the history lock and translate errors.

            :return: Updated history security status dictionary.
            :rtype: dict[str, object]
            :raises fastapi.HTTPException: If ``HistorySecurityError`` is raised during the operation (HTTP 409).
            """
            async with app.state.history_lock:
                try:
                    await operation()
                    return await status()
                except HistorySecurityError as exc:
                    raise HTTPException(409, {'code': exc.code, 'message': exc.message}) from None
        return await finish_mutation(owned())

    @app.get('/api/v1/history/security', dependencies=[Depends(desktop)])
    async def security():
        """Return current history security status, or cached status when busy.

        :return: Current or cached history security status dictionary.
        :rtype: dict[str, object]
        """
        if app.state.history_lock.locked():
            return {**app.state.history_status_cache, 'busy': True}
        async with app.state.history_lock:
            return await status()

    async def open_history(method, *args, **kwargs):
        """Open the encrypted store and activate history unless recovery offer is pending.

        :param method: Blocking ``HistorySecurity`` method returning an opened ``Store``.
        :type method: collections.abc.Callable[..., pbdesk.storage.database.Store]
        :param args: Positional arguments forwarded to ``method``.
        :type args: object
        :param kwargs: Keyword arguments forwarded to ``method``.
        :type kwargs: object
        """
        if app.state.store is None:
            app.state.store = await asyncio.to_thread(method, *args, **kwargs)
        if not (await asyncio.to_thread(app.state.history_security.inspect))['recovery_offer_pending']:
            await app.state.activate_history(app.state.store)

    @app.post('/api/v1/history/protect', dependencies=[Depends(desktop)])
    async def protect():
        """Enable encryption on local history and open the protected store.

        :return: Updated history security status dictionary.
        :rtype: dict[str, object]
        """
        return await mutate(lambda: open_history(app.state.history_security.protect))

    @app.post('/api/v1/history/unlock', dependencies=[Depends(desktop)])
    async def unlock():
        """Unlock an existing encrypted history store interactively.

        :return: Updated history security status dictionary.
        :rtype: dict[str, object]
        """
        return await mutate(lambda: open_history(app.state.history_security.open_existing, interactive=True))

    @app.post('/api/v1/history/migrate', dependencies=[Depends(desktop)])
    async def migrate():
        """Migrate legacy unencrypted history into an encrypted store.

        :return: Updated history security status dictionary.
        :rtype: dict[str, object]
        """
        return await mutate(lambda: open_history(app.state.history_security.migrate))

    @app.post('/api/v1/history/recover', dependencies=[Depends(desktop)])
    async def recover(value: Recovery):
        """Recover access to the encrypted history store using a recovery key.

        :param value: Validated request payload carrying the secret recovery key.
        :type value: Recovery
        :return: Updated history security status dictionary.
        :rtype: dict[str, object]
        """
        return await mutate(lambda: open_history(app.state.history_security.recover, value.recovery_key.get_secret_value()))

    @app.post('/api/v1/history/recovery-key', dependencies=[Depends(desktop)])
    async def recovery_key():
        """Export the recovery key for the currently unlocked history store.

        :return: Dictionary containing the exported ``recovery_key`` string.
        :rtype: dict[str, str]
        """
        async def disclose():
            """Read the recovery key under lock after verifying the store is unlocked.

            :return: Dictionary containing the exported ``recovery_key`` string.
            :rtype: dict[str, str]
            :raises fastapi.HTTPException: If the store is locked (HTTP 423) or exporting fails with ``HistorySecurityError`` (HTTP 409).
            """
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
        """Record recovery-key acknowledgement and activate local history.

        :param value: Validated request payload indicating whether the recovery key was saved.
        :type value: RecoveryOffer
        :return: Updated history security status dictionary.
        :rtype: dict[str, object]
        """
        async def finish():
            """Complete the recovery-offer acknowledgement and activate history.

            :raises fastapi.HTTPException: If the local history store is not yet unlocked (HTTP 423).
            """
            if app.state.store is None:
                raise HTTPException(423, 'Unlock local history before continuing')
            await asyncio.to_thread(app.state.history_security.finish_recovery_offer, app.state.store, saved=value.saved)
            await app.state.activate_history(app.state.store)
        return await mutate(finish)
