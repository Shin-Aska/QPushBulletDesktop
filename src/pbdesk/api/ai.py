"""Desktop-only AI configuration, model catalog, recent digests and reviewed drafts."""
import asyncio
from typing import Literal
from fastapi import Depends, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from pbdesk.services.ai import AiService, AiError, load_settings, save_settings, configure_key


class Prepare(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: str | None = Field(max_length=1024)
    mode: Literal['summary', 'draft_reply']
    source: Literal['notifications', 'sms']
    item_ids: list[int] = Field(default_factory=list, max_length=500)
    device_id: str | None = Field(default=None, max_length=1024)
    conversation_id: str | None = Field(default=None, max_length=1024)
    message_ids: list[StrictInt] | None = Field(default=None, max_length=20)
    tone: str = Field(default='Natural', min_length=1, max_length=200)

    @field_validator('tone', mode='before')
    @classmethod
    def trim_tone(cls, value):
        if not isinstance(value, str):
            raise ValueError('Tone must be text')
        return value.strip()


class Generate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context_id: str = Field(min_length=1, max_length=128)


class Digest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: str | None = Field(max_length=1024)
    scope: Literal['recent'] = 'recent'
    force: StrictBool = False


class ModelSetting(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(min_length=1, max_length=100)


class KeySetting(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=4096, repr=False)


class Conversation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=256)


class PresetConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    sources: list[Literal['notifications', 'sms']] = Field(min_length=1, max_length=2)
    device_ids: list[str] = Field(max_length=100)
    conversations: list[Conversation] = Field(max_length=100)
    conversation_filter: Literal['all', 'selected']
    window_hours: Literal[1, 6, 24, 72, 168]
    model_override: str | None


class AccountMutation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: str = Field(min_length=1, max_length=256)


class VersionMutation(AccountMutation):
    expected_revision: int = Field(ge=0, strict=True)


class PresetMutation(VersionMutation):
    config: PresetConfig


class ActiveMutation(VersionMutation):
    preset_id: str = Field(min_length=1, max_length=64)


class PinMutation(AccountMutation):
    pinned: StrictBool


class RetentionMutation(VersionMutation):
    retention_days: Literal[7, 30, 90, 365]
    preview: StrictBool
    confirmed_retention_days: Literal[7, 30, 90, 365] | None = None


def install_ai_routes(app, authorization, settings):
    from pbdesk.storage.summaries import SummaryConflict, SummaryNotFound, normalize_config
    service = None
    app.state.ai = None
    def initialize():
        nonlocal service
        if service is None:
            service = AiService(settings, lambda: app.state.store)
            service.operation_gate = lambda: app.state.core_settings.gate.operation()
            app.state.ai = service
    app.state.initialize_ai = initialize

    async def desktop(role=Depends(authorization)):
        if role != 'desktop':
            raise HTTPException(403, 'AI operations require the local desktop')

    async def local(function, *args, **kwargs):
        try:
            return await service.local(function, *args, **kwargs)
        except AiError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except HTTPException:
            raise
        except SummaryConflict:
            raise HTTPException(409, 'The saved summary settings changed. Reload and try again.') from None
        except SummaryNotFound:
            raise HTTPException(404, 'The saved summary or preset is unavailable.') from None
        except ValueError:
            raise HTTPException(422, 'The summary filters or values are invalid.') from None
        except Exception:
            raise HTTPException(409, 'AI configuration or selected text is unavailable.') from None

    @app.get('/api/v1/ai/settings', dependencies=[Depends(desktop)])
    async def get_settings():
        return await local(load_settings, settings)

    @app.post('/api/v1/ai/settings', dependencies=[Depends(desktop)])
    async def set_settings(value: ModelSetting):
        result = await local(save_settings, settings, value.model)
        await service.configuration_changed()
        return result

    @app.post('/api/v1/ai/key', dependencies=[Depends(desktop)])
    async def set_key(value: KeySetting):
        result = await local(configure_key, settings, app.state.store, value.key)
        await service.configuration_changed()
        return result

    @app.post('/api/v1/ai/key/use', dependencies=[Depends(desktop)])
    async def use_key():
        result = await local(configure_key, settings, app.state.store)
        await service.configuration_changed()
        return result

    @app.post('/api/v1/ai/prepare', dependencies=[Depends(desktop)])
    async def prepare(value: Prepare):
        return await local(service.prepare, value.model_dump())

    @app.post('/api/v1/ai/generate', dependencies=[Depends(desktop)])
    async def generate(value: Generate, request: Request):
        task = asyncio.create_task(asyncio.wait_for(service.generate(value.context_id), timeout=50))
        try:
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    raise HTTPException(499, 'AI request cancelled')
                await asyncio.wait({task}, timeout=.1)
            return await task
        except AiError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except HTTPException:
            raise
        except TimeoutError:
            raise HTTPException(504, 'The AI request timed out. No automatic retry was made.') from None
        except Exception:
            raise HTTPException(502, 'The AI request could not finish.') from None
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @app.get('/api/v1/ai/models', dependencies=[Depends(desktop)])
    async def models():
        try:
            return await service.models()
        except AiError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except Exception:
            raise HTTPException(502, 'The OpenAI model list could not be loaded.') from None

    @app.post('/api/v1/ai/digest', dependencies=[Depends(desktop)])
    async def digest(value: Digest):
        try:
            return await service.digest(value.account_id, value.scope, value.force)
        except AiError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except Exception:
            raise HTTPException(409, 'The recent summary is unavailable.') from None

    async def repository(account_id, method, *args, **kwargs):
        return await local(service.summary_operation, account_id, method, *args, **kwargs)

    @app.get('/api/v1/ai/preset-options', dependencies=[Depends(desktop)])
    async def preset_options(account_id: str = Query(min_length=1, max_length=256)):
        return await local(service.preset_options, account_id)

    @app.get('/api/v1/ai/presets', dependencies=[Depends(desktop)])
    async def presets(account_id: str = Query(min_length=1, max_length=256), include_archived: bool = False):
        return {'account_id': account_id, 'presets': await repository(account_id, 'list_presets', include_archived)}

    @app.post('/api/v1/ai/presets', dependencies=[Depends(desktop)])
    async def create_preset(value: PresetMutation):
        config = await local(normalize_config, value.config.model_dump())
        await local(service.validate_scope, value.account_id, config)
        return await repository(value.account_id, 'save_preset', config, value.expected_revision)

    @app.patch('/api/v1/ai/presets/{preset_id}', dependencies=[Depends(desktop)])
    async def update_preset(preset_id: str, value: PresetMutation):
        config = await local(normalize_config, value.config.model_dump())
        await local(service.validate_scope, value.account_id, config)
        return await local(service.update_preset, value.account_id, preset_id, config, value.expected_revision)

    @app.delete('/api/v1/ai/presets/{preset_id}', dependencies=[Depends(desktop)])
    async def archive_preset(preset_id: str, value: VersionMutation):
        return await repository(value.account_id, 'archive_preset', preset_id, value.expected_revision)

    @app.get('/api/v1/ai/active-preset', dependencies=[Depends(desktop)])
    async def active_preset(account_id: str = Query(min_length=1, max_length=256)):
        return await repository(account_id, 'get_active_preset')

    @app.put('/api/v1/ai/active-preset', dependencies=[Depends(desktop)])
    async def activate_preset(value: ActiveMutation):
        return await repository(value.account_id, 'set_active_preset', value.preset_id, value.expected_revision)

    @app.get('/api/v1/ai/history', dependencies=[Depends(desktop)])
    async def history(account_id: str = Query(min_length=1, max_length=256), query: str | None = Query(default=None, max_length=256),
            preset_id: str | None = None, pinned: bool | None = None, date_from: str | None = None,
            date_to: str | None = None, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000)):
        await local(service._import_legacy, account_id)
        return await repository(account_id, 'list_history', query=query, preset_id=preset_id, pinned=pinned,
            date_from=date_from, date_to=date_to, limit=limit, offset=offset)

    @app.get('/api/v1/ai/history/{entry_id}', dependencies=[Depends(desktop)])
    async def history_detail(entry_id: str, account_id: str = Query(min_length=1, max_length=256)):
        return await repository(account_id, 'get_history', entry_id)

    @app.patch('/api/v1/ai/history/{entry_id}', dependencies=[Depends(desktop)])
    async def pin_history(entry_id: str, value: PinMutation):
        return await repository(value.account_id, 'set_pin', entry_id, value.pinned)

    @app.get('/api/v1/ai/history-settings', dependencies=[Depends(desktop)])
    async def history_settings(account_id: str = Query(min_length=1, max_length=256)):
        return await repository(account_id, 'get_retention')

    @app.put('/api/v1/ai/history-settings', dependencies=[Depends(desktop)])
    async def save_history_settings(value: RetentionMutation):
        if value.preview:
            return await repository(value.account_id, 'preview_retention', value.retention_days, value.expected_revision)
        return await repository(value.account_id, 'set_retention', value.retention_days, value.expected_revision,
            value.confirmed_retention_days)

    return service
