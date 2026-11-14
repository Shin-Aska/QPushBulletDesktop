"""Desktop-only, instance-checked core settings and restart contract."""
from uuid import UUID
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class CoreUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_instance_id: UUID
    notifications_enabled: StrictBool


class CoreRestart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_instance_id: UUID
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')


def install_core_settings_routes(app, authorization):
    async def desktop(role=Depends(authorization)):
        if role != 'desktop':
            raise HTTPException(403, 'Core settings require the local desktop')

    @app.get('/api/v1/core/settings', dependencies=[Depends(desktop)])
    async def settings():
        return app.state.core_settings.snapshot()

    @app.patch('/api/v1/core/settings', dependencies=[Depends(desktop)])
    async def update(value: CoreUpdate):
        return await app.state.core_settings.update(str(value.expected_instance_id), value.notifications_enabled)

    @app.post('/api/v1/core/restart', dependencies=[Depends(desktop)], status_code=202)
    async def restart(value: CoreRestart):
        return await app.state.core_settings.restart(str(value.expected_instance_id), value.request_id)
