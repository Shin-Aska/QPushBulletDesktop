"""Desktop-only, instance-checked core settings and restart contract."""
from uuid import UUID
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class CoreUpdate(BaseModel):
    """Request payload for updating desktop core notification settings.

    :ivar expected_instance_id: UUID of the core instance the caller expects to update.
    :type expected_instance_id: uuid.UUID
    :ivar notifications_enabled: Desired enabled state for desktop notifications.
    :type notifications_enabled: bool
    """

    model_config = ConfigDict(extra='forbid')
    expected_instance_id: UUID
    notifications_enabled: StrictBool


class CoreRestart(BaseModel):
    """Request payload for requesting an instance-checked core restart.

    :ivar expected_instance_id: UUID of the core instance the caller expects to restart.
    :type expected_instance_id: uuid.UUID
    :ivar request_id: Idempotency identifier for the restart request.
    :type request_id: str
    """

    model_config = ConfigDict(extra='forbid')
    expected_instance_id: UUID
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')


def install_core_settings_routes(app, authorization):
    """Register desktop-only core settings and restart routes on the app.

    :param app: FastAPI application instance to register routes on.
    :type app: fastapi.FastAPI
    :param authorization: FastAPI dependency verifying the bearer service role.
    :type authorization: collections.abc.Callable[..., collections.abc.Awaitable[str]]
    """
    async def desktop(role=Depends(authorization)):
        """Enforce local desktop authorization for core settings endpoints.

        :param role: Authenticated caller role resolved by the authorization dependency.
        :type role: str
        :raises fastapi.HTTPException: If the caller role is not ``'desktop'`` (HTTP 403).
        """
        if role != 'desktop':
            raise HTTPException(403, 'Core settings require the local desktop')

    @app.get('/api/v1/core/settings', dependencies=[Depends(desktop)])
    async def settings():
        """Return the current core settings snapshot.

        :return: Snapshot dictionary of core lifecycle, addresses, paths, and notification state.
        :rtype: dict[str, object]
        """
        return app.state.core_settings.snapshot()

    @app.patch('/api/v1/core/settings', dependencies=[Depends(desktop)])
    async def update(value: CoreUpdate):
        """Update core notification settings for the expected core instance.

        :param value: Validated request payload with expected instance ID and notification flag.
        :type value: CoreUpdate
        :return: Updated core settings snapshot dictionary.
        :rtype: dict[str, object]
        """
        return await app.state.core_settings.update(str(value.expected_instance_id), value.notifications_enabled)

    @app.post('/api/v1/core/restart', dependencies=[Depends(desktop)], status_code=202)
    async def restart(value: CoreRestart):
        """Request a controlled restart for the expected core instance.

        :param value: Validated request payload with expected instance ID and request identifier.
        :type value: CoreRestart
        :return: Restart acceptance payload containing accepted flag, instance ID, and request ID.
        :rtype: dict[str, object]
        """
        return await app.state.core_settings.restart(str(value.expected_instance_id), value.request_id)
