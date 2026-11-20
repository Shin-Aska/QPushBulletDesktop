"""Desktop-only AI configuration, model catalog, recent digests and reviewed drafts."""
import asyncio
from typing import Literal
from fastapi import Depends, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from pbdesk.services.ai import AiService, AiError, load_settings, save_settings, configure_key


class Prepare(BaseModel):
    """Request payload for preparing an AI summary or draft reply context.

    :ivar account_id: Target Pushbullet account identifier, or None when unbound.
    :type account_id: str | None
    :ivar mode: AI workflow mode to prepare.
    :type mode: Literal['summary', 'draft_reply']
    :ivar source: Data source type supplying input items or messages.
    :type source: Literal['notifications', 'sms']
    :ivar item_ids: Selected inbox item IDs when summarizing notifications.
    :type item_ids: list[int]
    :ivar device_id: Target device identifier for SMS context preparation.
    :type device_id: str | None
    :ivar conversation_id: Target SMS conversation identifier for draft or summary preparation.
    :type conversation_id: str | None
    :ivar message_ids: Explicit SMS message IDs selected for reply drafting.
    :type message_ids: list[int] | None
    :ivar tone: Requested writing style tone for generated draft replies.
    :type tone: str
    """

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
        """Strip surrounding whitespace from the requested tone value.

        :param value: Raw tone input value prior to string validation.
        :type value: object
        :return: Whitespace-trimmed tone string.
        :rtype: str
        :raises ValueError: If ``value`` is not a string.
        """
        if not isinstance(value, str):
            raise ValueError('Tone must be text')
        return value.strip()


class Generate(BaseModel):
    """Request payload identifying a prepared AI context to generate.

    :ivar context_id: Opaque identifier of the previously prepared AI preview context.
    :type context_id: str
    """

    model_config = ConfigDict(extra='forbid')
    context_id: str = Field(min_length=1, max_length=128)


class Digest(BaseModel):
    """Request payload for triggering a recent digest generation.

    :ivar account_id: Target Pushbullet account identifier for the digest lookup.
    :type account_id: str | None
    :ivar scope: Digest scope selector; currently fixed to ``'recent'``.
    :type scope: Literal['recent']
    :ivar force: Whether to consume one explicit retry when a digest error is latched.
    :type force: bool
    """

    model_config = ConfigDict(extra='forbid')
    account_id: str | None = Field(max_length=1024)
    scope: Literal['recent'] = 'recent'
    force: StrictBool = False


class ModelSetting(BaseModel):
    """Request payload for selecting the active OpenAI model.

    :ivar model: OpenAI model identifier to persist as the active model.
    :type model: str
    """

    model_config = ConfigDict(extra='forbid')
    model: str = Field(min_length=1, max_length=100)


class KeySetting(BaseModel):
    """Request payload carrying an OpenAI API key to configure.

    :ivar key: OpenAI API key secret to validate and store in KWallet.
    :type key: str
    """

    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=4096, repr=False)


class Conversation(BaseModel):
    """Identifier pair selecting a device SMS conversation.

    :ivar device_id: Device identifier owning the SMS conversation.
    :type device_id: str
    :ivar conversation_id: SMS conversation identifier within the device.
    :type conversation_id: str
    """

    model_config = ConfigDict(extra='forbid')
    device_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=256)


class PresetConfig(BaseModel):
    """Configuration payload defining a summary preset and its filters.

    :ivar name: Human-readable display name for the summary preset.
    :type name: str
    :ivar sources: Activity sources included in the preset summary.
    :type sources: list[Literal['notifications', 'sms']]
    :ivar device_ids: Allowed Pushbullet device identifiers for preset filtering.
    :type device_ids: list[str]
    :ivar conversations: Specific SMS device/conversation pairs included when filtering selected threads.
    :type conversations: list[Conversation]
    :ivar conversation_filter: Whether to include all SMS conversations or only ``conversations``.
    :type conversation_filter: Literal['all', 'selected']
    :ivar window_hours: Lookback time window in hours for recent activity.
    :type window_hours: Literal[1, 6, 24, 72, 168]
    :ivar model_override: Optional OpenAI model ID overriding the global AI model for this preset.
    :type model_override: str | None
    """

    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    sources: list[Literal['notifications', 'sms']] = Field(min_length=1, max_length=2)
    device_ids: list[str] = Field(max_length=100)
    conversations: list[Conversation] = Field(max_length=100)
    conversation_filter: Literal['all', 'selected']
    window_hours: Literal[1, 6, 24, 72, 168]
    model_override: str | None


class AccountMutation(BaseModel):
    """Base payload scoped to a single Pushbullet account.

    :ivar account_id: Pushbullet account identifier scoped to the mutation.
    :type account_id: str
    """

    model_config = ConfigDict(extra='forbid')
    account_id: str = Field(min_length=1, max_length=256)


class VersionMutation(AccountMutation):
    """Base account mutation payload carrying an optimistic concurrency revision.

    :ivar expected_revision: Expected optimistic concurrency revision number for summary settings.
    :type expected_revision: int
    """

    expected_revision: int = Field(ge=0, strict=True)


class PresetMutation(VersionMutation):
    """Request payload for creating or updating a summary preset.

    :ivar config: Summary preset configuration payload to validate and persist.
    :type config: PresetConfig
    """

    config: PresetConfig


class ActiveMutation(VersionMutation):
    """Request payload for activating a saved summary preset.

    :ivar preset_id: Identifier of the summary preset to activate.
    :type preset_id: str
    """

    preset_id: str = Field(min_length=1, max_length=64)


class PinMutation(AccountMutation):
    """Request payload for pinning or unpinning a summary history entry.

    :ivar pinned: Desired pinned state for the summary history entry.
    :type pinned: bool
    """

    pinned: StrictBool


class RetentionMutation(VersionMutation):
    """Request payload for previewing or updating summary history retention.

    :ivar retention_days: Target summary history retention period in days.
    :type retention_days: Literal[7, 30, 90, 365]
    :ivar preview: When True, compute pruning impact without modifying retention settings.
    :type preview: bool
    :ivar confirmed_retention_days: Confirmed retention period required when shortening retention deletes entries.
    :type confirmed_retention_days: Literal[7, 30, 90, 365] | None
    """

    retention_days: Literal[7, 30, 90, 365]
    preview: StrictBool
    confirmed_retention_days: Literal[7, 30, 90, 365] | None = None


def install_ai_routes(app, authorization, settings):
    """Register desktop-only AI configuration, generation, and summary routes.

    :param app: FastAPI application instance to register routes on.
    :type app: fastapi.FastAPI
    :param authorization: FastAPI dependency verifying the bearer service role.
    :type authorization: collections.abc.Callable[..., collections.abc.Awaitable[str]]
    :param settings: Application configuration settings instance.
    :type settings: pbdesk.config.Settings
    :return: Initial ``service`` binding (``None`` until ``app.state.initialize_ai`` runs).
    :rtype: pbdesk.services.ai.AiService | None
    """
    from pbdesk.storage.summaries import SummaryConflict, SummaryNotFound, normalize_config
    service = None
    app.state.ai = None
    def initialize():
        """Lazily instantiate the AiService bound to the active store and gate."""
        nonlocal service
        if service is None:
            service = AiService(settings, lambda: app.state.store)
            service.operation_gate = lambda: app.state.core_settings.gate.operation()
            app.state.ai = service
    app.state.initialize_ai = initialize

    async def desktop(role=Depends(authorization)):
        """Enforce local desktop authorization for AI endpoints.

        :param role: Authenticated caller role resolved by the authorization dependency.
        :type role: str
        :raises fastapi.HTTPException: If the caller role is not ``'desktop'`` (HTTP 403).
        """
        if role != 'desktop':
            raise HTTPException(403, 'AI operations require the local desktop')

    async def local(function, *args, **kwargs):
        """Execute a local AI service call and translate domain errors to HTTP exceptions.

        :param function: Synchronous callable to run inside the AI service's local thread wrapper.
        :type function: collections.abc.Callable[..., object]
        :param args: Positional arguments forwarded to ``function``.
        :type args: object
        :param kwargs: Keyword arguments forwarded to ``function``.
        :type kwargs: object
        :return: Result returned by ``function``.
        :rtype: object
        :raises fastapi.HTTPException: If ``AiError`` (using its status code), ``SummaryConflict`` (HTTP 409), ``SummaryNotFound`` (HTTP 404), ``ValueError`` (HTTP 422), or an unexpected error (HTTP 409) occurs, or re-raises an existing ``HTTPException``.
        """
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
        """Return the current AI configuration settings.

        :return: Current AI settings dictionary including model, configured flag, revision, and key reference.
        :rtype: dict[str, object]
        """
        return await local(load_settings, settings)

    @app.post('/api/v1/ai/settings', dependencies=[Depends(desktop)])
    async def set_settings(value: ModelSetting):
        """Save the selected OpenAI model and notify the AI service.

        :param value: Validated payload containing the selected OpenAI model identifier.
        :type value: ModelSetting
        :return: Updated AI settings dictionary.
        :rtype: dict[str, object]
        """
        result = await local(save_settings, settings, value.model)
        await service.configuration_changed()
        return result

    @app.post('/api/v1/ai/key', dependencies=[Depends(desktop)])
    async def set_key(value: KeySetting):
        """Store an OpenAI API key in the wallet and reload AI configuration.

        :param value: Validated payload containing the OpenAI API key to store.
        :type value: KeySetting
        :return: Updated AI settings dictionary reflecting key configuration.
        :rtype: dict[str, object]
        """
        result = await local(configure_key, settings, app.state.store, value.key)
        await service.configuration_changed()
        return result

    @app.post('/api/v1/ai/key/use', dependencies=[Depends(desktop)])
    async def use_key():
        """Activate an existing wallet OpenAI API key for the AI service.

        :return: Updated AI settings dictionary reflecting key activation.
        :rtype: dict[str, object]
        """
        result = await local(configure_key, settings, app.state.store)
        await service.configuration_changed()
        return result

    @app.post('/api/v1/ai/prepare', dependencies=[Depends(desktop)])
    async def prepare(value: Prepare):
        """Prepare and return an AI prompt context for review before generation.

        :param value: Validated AI context preparation request payload.
        :type value: Prepare
        :return: Prepared preview metadata including ``context_id``, ``expires_in``, and redacted text.
        :rtype: dict[str, object]
        """
        return await local(service.prepare, value.model_dump())

    @app.post('/api/v1/ai/generate', dependencies=[Depends(desktop)])
    async def generate(value: Generate, request: Request):
        """Run AI generation for a prepared context while watching for client disconnect.

        :param value: Validated payload identifying the prepared context to generate from.
        :type value: Generate
        :param request: Incoming FastAPI request monitored for client disconnection.
        :type request: fastapi.Request
        :return: Generated text and metadata dictionary returned by ``AiService.generate``.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If the client disconnects (HTTP 499), ``AiError`` is raised (using its status code), generation times out (HTTP 504), or an unexpected error occurs (HTTP 502).
        """
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
        """Return the available OpenAI model catalog.

        :return: Available OpenAI model catalog with selected and default model metadata.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If ``AiError`` is raised (using its status code) or loading the model catalog fails (HTTP 502).
        """
        try:
            return await service.models()
        except AiError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except Exception:
            raise HTTPException(502, 'The OpenAI model list could not be loaded.') from None

    @app.post('/api/v1/ai/digest', dependencies=[Depends(desktop)])
    async def digest(value: Digest):
        """Generate or return a cached digest for recent activity.

        :param value: Validated digest lookup and refresh request payload.
        :type value: Digest
        :return: Current recent digest status, cached output, and scheduling metadata.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If ``AiError`` is raised (using its status code) or digest evaluation fails (HTTP 409).
        """
        try:
            return await service.digest(value.account_id, value.scope, value.force)
        except AiError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except Exception:
            raise HTTPException(409, 'The recent summary is unavailable.') from None

    async def repository(account_id, method, *args, **kwargs):
        """Run an account-scoped summary repository operation through the local wrapper.

        :param account_id: Pushbullet account identifier owning the summary repository data.
        :type account_id: str
        :param method: Name of the ``SummaryRepository`` method to invoke.
        :type method: str
        :param args: Positional arguments forwarded to the repository method.
        :type args: object
        :param kwargs: Keyword arguments forwarded to the repository method.
        :type kwargs: object
        :return: Result returned by the invoked repository method.
        :rtype: object
        """
        return await local(service.summary_operation, account_id, method, *args, **kwargs)

    @app.get('/api/v1/ai/preset-options', dependencies=[Depends(desktop)])
    async def preset_options(account_id: str = Query(min_length=1, max_length=256)):
        """Return available devices and conversations for summary preset filters.

        :param account_id: Pushbullet account identifier to query retained devices and conversations for.
        :type account_id: str
        :return: Dictionary containing available devices, SMS conversations, and truncation indicator.
        :rtype: dict[str, object]
        """
        return await local(service.preset_options, account_id)

    @app.get('/api/v1/ai/presets', dependencies=[Depends(desktop)])
    async def presets(account_id: str = Query(min_length=1, max_length=256), include_archived: bool = False):
        """List saved summary presets for the specified account.

        :param account_id: Pushbullet account identifier owning the presets.
        :type account_id: str
        :param include_archived: Whether archived summary presets should be included in the response.
        :type include_archived: bool
        :return: Dictionary containing ``account_id`` and the list of matching ``presets``.
        :rtype: dict[str, object]
        """
        return {'account_id': account_id, 'presets': await repository(account_id, 'list_presets', include_archived)}

    @app.post('/api/v1/ai/presets', dependencies=[Depends(desktop)])
    async def create_preset(value: PresetMutation):
        """Validate and save a new summary preset for the account.

        :param value: Validated preset creation payload and expected revision.
        :type value: PresetMutation
        :return: Saved preset record and updated repository revision.
        :rtype: dict[str, object]
        """
        config = await local(normalize_config, value.config.model_dump())
        await local(service.validate_scope, value.account_id, config)
        return await repository(value.account_id, 'save_preset', config, value.expected_revision)

    @app.patch('/api/v1/ai/presets/{preset_id}', dependencies=[Depends(desktop)])
    async def update_preset(preset_id: str, value: PresetMutation):
        """Validate and update an existing summary preset.

        :param preset_id: Identifier of the existing summary preset to update.
        :type preset_id: str
        :param value: Validated updated preset configuration and expected revision.
        :type value: PresetMutation
        :return: Updated preset record and new repository revision.
        :rtype: dict[str, object]
        """
        config = await local(normalize_config, value.config.model_dump())
        await local(service.validate_scope, value.account_id, config)
        return await local(service.update_preset, value.account_id, preset_id, config, value.expected_revision)

    @app.delete('/api/v1/ai/presets/{preset_id}', dependencies=[Depends(desktop)])
    async def archive_preset(preset_id: str, value: VersionMutation):
        """Archive a saved summary preset for the account.

        :param preset_id: Identifier of the summary preset to archive.
        :type preset_id: str
        :param value: Validated account identifier and expected concurrency revision.
        :type value: VersionMutation
        :return: Archived preset metadata and updated repository revision.
        :rtype: dict[str, object]
        """
        return await repository(value.account_id, 'archive_preset', preset_id, value.expected_revision)

    @app.get('/api/v1/ai/active-preset', dependencies=[Depends(desktop)])
    async def active_preset(account_id: str = Query(min_length=1, max_length=256)):
        """Return the active summary preset for the account.

        :param account_id: Pushbullet account identifier to query the active preset for.
        :type account_id: str
        :return: Active preset record and current repository revision.
        :rtype: dict[str, object]
        """
        return await repository(account_id, 'get_active_preset')

    @app.put('/api/v1/ai/active-preset', dependencies=[Depends(desktop)])
    async def activate_preset(value: ActiveMutation):
        """Set the active summary preset for the account.

        :param value: Validated payload specifying the preset to activate and expected revision.
        :type value: ActiveMutation
        :return: Activated preset record and updated repository revision.
        :rtype: dict[str, object]
        """
        return await repository(value.account_id, 'set_active_preset', value.preset_id, value.expected_revision)

    @app.get('/api/v1/ai/history', dependencies=[Depends(desktop)])
    async def history(account_id: str = Query(min_length=1, max_length=256), query: str | None = Query(default=None, max_length=256),
            preset_id: str | None = None, pinned: bool | None = None, date_from: str | None = None,
            date_to: str | None = None, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000)):
        """List filtered summary history entries after importing any legacy entries.

        :param account_id: Pushbullet account identifier owning the summary history.
        :type account_id: str
        :param query: Optional substring filter applied to summary text and metadata.
        :type query: str | None
        :param preset_id: Optional preset identifier filter.
        :type preset_id: str | None
        :param pinned: Optional filter matching pinned or unpinned entries.
        :type pinned: bool | None
        :param date_from: Optional inclusive lower ISO date/timestamp bound.
        :type date_from: str | None
        :param date_to: Optional inclusive upper ISO date/timestamp bound.
        :type date_to: str | None
        :param limit: Maximum number of history entries to return.
        :type limit: int
        :param offset: Number of matching history entries to skip for pagination.
        :type offset: int
        :return: Paginated summary history entries and metadata.
        :rtype: dict[str, object]
        """
        await local(service._import_legacy, account_id)
        return await repository(account_id, 'list_history', query=query, preset_id=preset_id, pinned=pinned,
            date_from=date_from, date_to=date_to, limit=limit, offset=offset)

    @app.get('/api/v1/ai/history/{entry_id}', dependencies=[Depends(desktop)])
    async def history_detail(entry_id: str, account_id: str = Query(min_length=1, max_length=256)):
        """Return full details for a single summary history entry.

        :param entry_id: Identifier of the summary history entry to fetch.
        :type entry_id: str
        :param account_id: Pushbullet account identifier owning the history entry.
        :type account_id: str
        :return: Full summary history entry dictionary.
        :rtype: dict[str, object]
        """
        return await repository(account_id, 'get_history', entry_id)

    @app.patch('/api/v1/ai/history/{entry_id}', dependencies=[Depends(desktop)])
    async def pin_history(entry_id: str, value: PinMutation):
        """Update the pinned status of a summary history entry.

        :param entry_id: Identifier of the summary history entry to pin or unpin.
        :type entry_id: str
        :param value: Validated account identifier and target ``pinned`` flag.
        :type value: PinMutation
        :return: Updated summary history entry dictionary.
        :rtype: dict[str, object]
        """
        return await repository(value.account_id, 'set_pin', entry_id, value.pinned)

    @app.get('/api/v1/ai/history-settings', dependencies=[Depends(desktop)])
    async def history_settings(account_id: str = Query(min_length=1, max_length=256)):
        """Return summary history retention settings for the account.

        :param account_id: Pushbullet account identifier owning the retention policy.
        :type account_id: str
        :return: Current summary history retention days and revision settings.
        :rtype: dict[str, object]
        """
        return await repository(account_id, 'get_retention')

    @app.put('/api/v1/ai/history-settings', dependencies=[Depends(desktop)])
    async def save_history_settings(value: RetentionMutation):
        """Preview or persist summary history retention settings for the account.

        :param value: Validated retention update or preview payload.
        :type value: RetentionMutation
        :return: Retention pruning preview or updated retention policy dictionary.
        :rtype: dict[str, object]
        """
        if value.preview:
            return await repository(value.account_id, 'preview_retention', value.retention_days, value.expected_revision)
        return await repository(value.account_id, 'set_retention', value.retention_days, value.expected_revision,
            value.confirmed_retention_days)

    return service
