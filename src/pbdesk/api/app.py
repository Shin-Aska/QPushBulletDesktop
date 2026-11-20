"""Version 1 authenticated, scoped internal API."""
import asyncio
from contextlib import asynccontextmanager
from functools import wraps
import json
import os
from pathlib import Path
import time
import uuid
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pbdesk.api.streaming import BoundedStreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pbdesk.config import Settings
from pbdesk.credentials import CredentialLocked, identify_service, provider_token, service_token, health_response
from pbdesk.pushbullet.normalize import DEMO, normalize
from pbdesk.pushbullet.provider import Provider, SubmissionError
from pbdesk.pushbullet.crypto import EncryptionService
from pbdesk.domain.actions import ActionRequest, ActionEdit, Approval, Cancellation, ActionConflict, InvalidDestination
from pbdesk.services.outbox import Dispatcher
from pbdesk.services.instance import InstanceLock
from pbdesk.services.notifications import Notifier
from pbdesk.services.webhost import WebHost
from pbdesk.services.client_lifecycle import LeaseRegistry, stop_generation
from pbdesk.services.core_settings import CoreSettingsService, cancel_restart, finish_mutation
from pbdesk.storage.database import Store
from pbdesk.services.history_security import HistorySecurity, HistorySecurityError


def create_app(settings=None, fixture_path=None, notifier=None, demo=False, notifications=False, provider_factory=None, standalone=False, lease_timing=None, launch_generation=None, restart_launcher=None):
    """Create and configure the internal FastAPI core application.

    :param settings: Optional application configuration settings; defaults to ``Settings.from_env()``.
    :type settings: pbdesk.config.Settings | None
    :param fixture_path: Optional path to a JSON array of Pushbullet events to ingest at startup.
    :type fixture_path: str | pathlib.Path | None
    :param notifier: Optional custom notification adapter instance overriding the default notifier.
    :type notifier: pbdesk.services.notifications.Notifier | None
    :param demo: When True, seed demo push items and disable live provider setup.
    :type demo: bool
    :param notifications: Default desktop notification preference when none is stored yet.
    :type notifications: bool
    :param provider_factory: Optional factory callable returning a custom provider instance.
    :type provider_factory: collections.abc.Callable[..., object] | None
    :param standalone: When True, disable automatic core shutdown when client leases expire.
    :type standalone: bool
    :param lease_timing: Optional custom client lease renewal and expiration timing configuration.
    :type lease_timing: pbdesk.services.client_lifecycle.LeaseTiming | None
    :param launch_generation: Optional Stop All generation token overriding the persisted generation file.
    :type launch_generation: str | None
    :param restart_launcher: Optional async callback used to spawn a replacement core during restart.
    :type restart_launcher: collections.abc.Callable[[pbdesk.config.Settings, str], collections.abc.Awaitable[object]] | None
    :return: Configured FastAPI application instance for the private core API.
    :rtype: fastapi.FastAPI
    """
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        """Manage startup locks, history activation, background tasks, and shutdown.

        :param app: FastAPI application instance whose lifecycle state is managed.
        :type app: fastapi.FastAPI
        :return: Yields control to FastAPI while the application runs.
        :rtype: collections.abc.AsyncIterator[None]
        """
        lock = await asyncio.to_thread(InstanceLock, settings)
        tasks = []
        store = None
        provider = None
        host = WebHost(settings)
        try:
            for role in ("desktop", "web", "mcp"):
                await asyncio.to_thread(service_token, settings, role)
            app.state.instance_id = str(uuid.uuid4())
            app.state.history_security = HistorySecurity(settings)
            app.state.history_lock = asyncio.Lock()
            app.state.history_status_cache = {**await asyncio.to_thread(app.state.history_security.inspect),
                'history_ready': False, 'unlocked': False}
            app.state.store = None
            app.state.host = host
            app.state.provider = None
            app.state.history_ready = False
            app.state.clients = LeaseRegistry(app.state.instance_id, launch_generation or await asyncio.to_thread(stop_generation, settings),
                lambda: app.state.shutdown_callback and app.state.shutdown_callback(), standalone=standalone, timing=lease_timing)
            tasks.append(asyncio.create_task(app.state.clients.run()))
            async def activate(opened_store):
                """Activate an opened history store, provider, dispatcher, and auxiliary services.

                :param opened_store: Unlocked local SQLite history store instance to activate.
                :type opened_store: pbdesk.storage.database.Store
                :raises ValueError: If ``fixture_path`` does not contain a JSON list of at most 10000 events.
                """
                nonlocal store, provider
                if app.state.history_ready:
                    return
                store = opened_store
                store.instance_id = app.state.instance_id
                app.state.store = store
                host.store = store
                app.state.provider_state = "demo" if demo or fixture_path else "unconfigured"
                app.state.provider = None
                saved_notifications = await asyncio.to_thread(store.get_setting, "core_notifications_enabled", notifications)
                adapter = notifier or Notifier(saved_notifications if type(saved_notifications) is bool else notifications)
                app.state.notifier = adapter
                async def consume_plain(payload):
                    """Normalize, ingest, and optionally notify on a decrypted push payload.

                    :param payload: Decrypted or plaintext Pushbullet event dictionary to normalize and store.
                    :type payload: dict[str, object]
                    :return: Stored item change event dictionary if ingested, or None when ignored or handled as an SMS refresh signal.
                    :rtype: dict[str, object] | None
                    """
                    push=payload.get('push') if isinstance(payload,dict) and payload.get('type')=='push' else payload
                    if isinstance(push,dict) and push.get('type')=='sms_changed':
                        selected=app.state.provider
                        if selected is not None and hasattr(selected,'request_sms_refresh'):
                            try:
                                await selected.request_sms_refresh(push.get('source_device_iden'))
                            except ValueError:
                                await selected.request_sms_refresh()
                        return None
                    item = normalize(payload)
                    if item is None:
                        return None
                    selected=app.state.provider
                    account=getattr(selected,"account_id",None)
                    if account is None:
                        account=await asyncio.to_thread(store.account_key)
                    change = await asyncio.to_thread(store.ingest, item,account)
                    if change and change["type"] == "item.created" and adapter.enabled:
                        if await asyncio.to_thread(store.claim_notification, change["item_id"]):
                            await adapter.notify(await asyncio.to_thread(store.item, change["item_id"]))
                    return change
                encryption=EncryptionService(store,consume_plain)
                app.state.encryption=encryption
                ingest=encryption.ingest
                app.state.ingest = ingest
                dispatcher=Dispatcher(store,lambda:app.state.provider)
                app.state.dispatcher=dispatcher
                app.state.provider_reload_lock=asyncio.Lock()
                app.state.configuration_lock=asyncio.Lock()
                timing = app.state.clients.timing
                profile = None if provider_factory is not None or notifier is not None else {
                    'demo': demo, 'fixture': str(Path(fixture_path).resolve()) if fixture_path else None,
                    'standalone': standalone, 'notifications': notifications,
                    'timing': {'renew': timing.renew, 'expiry': timing.expiry,
                        'release_grace': timing.release_grace, 'startup_grace': timing.startup_grace}}
                app.state.core_settings = CoreSettingsService(app, settings, profile, launcher=restart_launcher)
                app.state.provider_task=None
                await asyncio.to_thread(app.state.initialize_ai)
                if demo:
                    await ingest(DEMO)
                if fixture_path:
                    payloads = json.loads(await asyncio.to_thread(Path(fixture_path).read_text))
                    if not isinstance(payloads, list) or len(payloads) > 10000:
                        raise ValueError("Fixture must be a JSON array of at most 10000 events")
                    for payload in payloads:
                        await ingest(payload)
                async def start_provider():
                    """Instantiate and start the configured Pushbullet provider task if available.

                    :return: Started Pushbullet provider instance, or None when unconfigured or in demo/fixture mode.
                    :rtype: object | None
                    """
                    if provider_factory is not None:
                        selected=provider_factory(settings,store,ingest,encryption)
                    elif not demo and not fixture_path:
                        from pbdesk.configuration import get
                        established = await asyncio.to_thread(get, settings, 'setup', {})
                        source = await asyncio.to_thread(get, settings, 'provider')
                        token = (await asyncio.to_thread(provider_token, settings)
                            if source is not None or established.get('completed') else None)
                        if token:
                            from pbdesk.configuration import get
                            from pbdesk.credentials import _save_provider_source
                            if await asyncio.to_thread(get, settings, 'provider') is None:
                                await asyncio.to_thread(_save_provider_source, settings, {'backend': 'keyring'})
                        selected=Provider(token,store,ingest,settings=settings,encryption=encryption) if token else None
                    else:
                        selected=None
                    app.state.provider=selected
                    if selected is not None:
                        app.state.provider_task=asyncio.create_task(selected.run())
                        tasks.append(app.state.provider_task)
                    return selected
                app.state.start_provider=start_provider
                try:
                    provider=await start_provider()
                except CredentialLocked:
                    app.state.provider_state="locked"
                tasks.append(asyncio.create_task(dispatcher.run()))
                tasks.append(asyncio.create_task(host.watch()))
                if await asyncio.to_thread(store.get_setting, "web_enabled", False):
                    tasks.append(asyncio.create_task(host.set_enabled(True)))
                app.state.history_ready = True
            app.state.activate_history = activate
            async def resume_history():
                """Attempt non-interactive history unlock and activation at startup."""
                async with app.state.history_lock:
                    try:
                        opened = await asyncio.to_thread(app.state.history_security.open_existing, interactive=False)
                    except HistorySecurityError:
                        return
                    app.state.store = opened
                    if not (await asyncio.to_thread(app.state.history_security.inspect))["recovery_offer_pending"]:
                        await activate(opened)
            app.state.ready = True
            tasks.append(asyncio.create_task(finish_mutation(resume_history())))
            yield
        finally:
            app.state.ready = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not app.state.restart_intent:
                await host.set_enabled(False)
            if getattr(app.state,"provider",None):
                await app.state.provider.close()
            if getattr(app.state, "ai", None):
                await app.state.ai.close()
            opened = getattr(app.state, "store", None)
            if opened:
                await asyncio.to_thread(opened.close)
            lock.close()

    app = FastAPI(title="pbdesk private core", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.ready = False
    app.state.shutdown_callback = None
    app.state.restart_intent = False
    app.state.core_settings = None

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        """Format FastAPI request validation errors while redacting history payloads.

        :param request: Incoming FastAPI request that failed validation.
        :type request: fastapi.Request
        :param exc: Validation error raised by FastAPI request parsing.
        :type exc: fastapi.exceptions.RequestValidationError
        :return: HTTP 422 JSON response with sanitized or redacted validation details.
        :rtype: fastapi.responses.JSONResponse
        """
        if request.url.path.startswith('/api/v1/history/'):
            return JSONResponse(status_code=422, content={'detail': 'Invalid history security request'})
        return JSONResponse(status_code=422,content={'detail':[
            {'type':error['type'],'loc':error['loc'],'msg':error['msg']} for error in exc.errors()
        ]})

    async def authorization(request: Request, authorization: str | None = Header(default=None)):
        """Authenticate bearer service tokens and enforce role and history-ready scopes.

        :param request: Incoming FastAPI request evaluated for method and path scope.
        :type request: fastapi.Request
        :param authorization: ``Authorization`` header containing a ``Bearer`` service credential.
        :type authorization: str | None
        :return: Authenticated service role (``'desktop'``, ``'web'``, or ``'mcp'``).
        :rtype: str
        :raises fastapi.HTTPException: If the bearer credential is missing or invalid (HTTP 401), the caller role lacks permission for the route (HTTP 403), or history encryption bootstrap has not completed (HTTP 423).
        """
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Service credential required")
        role = await asyncio.to_thread(identify_service, settings, authorization[7:])
        if role is None:
            raise HTTPException(401, "Invalid service credential")
        path = request.url.path
        if role == "web" and not ((request.method == "GET" and path in {"/api/v1/health", "/api/v1/capabilities"}) or path == "/api/v1/clients" or path.startswith("/api/v1/clients/")):
            raise HTTPException(403, "Web inbox is unavailable in this slice")
        if role == "mcp" and not (request.method == "GET" and (path in {"/api/v1/health", "/api/v1/capabilities", "/api/v1/items", "/api/v1/events", "/api/v1/devices", "/api/v1/actions"} or path.startswith("/api/v1/items/") or path.startswith("/api/v1/actions/"))) and not (request.method=="POST" and path=="/api/v1/actions"):
            raise HTTPException(403, "MCP may read or request an action; local desktop approval is required")
        bootstrap = path.startswith("/api/v1/history/")
        if bootstrap and role != "desktop":
            raise HTTPException(403, "Local desktop authorization required")
        permitted = path in {"/api/v1/health", "/api/v1/capabilities", "/api/v1/shutdown", "/api/v1/clients"} or path.startswith("/api/v1/clients/") or bootstrap
        if not app.state.history_ready and not permitted:
            raise HTTPException(423, "Unlock and protect local history in the desktop before continuing")
        return role

    def guard_mutation(function):
        """Serialize mutating requests under the core-settings operation gate.

        :param function: Async route handler to serialize under the core-settings operation gate.
        :type function: collections.abc.Callable[..., collections.abc.Awaitable[object]]
        :return: Wrapped async endpoint function guarded by ``core_settings.gate.operation()``.
        :rtype: collections.abc.Callable[..., collections.abc.Awaitable[object]]
        """
        @wraps(function)
        async def guarded(*args, **kwargs):
            """Run the wrapped endpoint under the operation gate until completion.

            :param args: Positional arguments forwarded to the wrapped route handler.
            :type args: object
            :param kwargs: Keyword arguments forwarded to the wrapped route handler.
            :type kwargs: object
            :return: Return value of the wrapped route handler.
            :rtype: object
            """
            async with app.state.core_settings.gate.operation():
                return await finish_mutation(function(*args, **kwargs))
        return guarded

    from pbdesk.api.history_security import install_history_security_routes
    install_history_security_routes(app, authorization)
    from pbdesk.api.core_settings import install_core_settings_routes
    install_core_settings_routes(app, authorization)
    from pbdesk.api.ai import install_ai_routes
    from pbdesk.api.media import install_media_routes
    install_ai_routes(app, authorization, settings)
    install_media_routes(app, authorization, settings)

    @app.middleware("http")
    async def protect_origin(request, call_next):
        """Reject browser Origin headers and disable response caching on all API routes.

        :param request: Incoming HTTP request inspected for a browser ``Origin`` header.
        :type request: fastapi.Request
        :param call_next: Next ASGI middleware or route handler in the request chain.
        :type call_next: collections.abc.Callable[[fastapi.Request], collections.abc.Awaitable[starlette.responses.Response]]
        :return: HTTP 403 JSON response when an ``Origin`` header is present, or the downstream response with ``Cache-Control: no-store``.
        :rtype: starlette.responses.Response
        """
        # Service APIs accept no browser cross-origin requests, even with a leaked token.
        if request.headers.get("origin"):
            return __import__("starlette.responses", fromlist=["JSONResponse"]).JSONResponse({"detail": "Browser origins are not accepted by private core"}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    async def health(request: Request):
        """Return signed service health and readiness metadata.

        :param request: Incoming FastAPI request carrying signed health-probe headers.
        :type request: fastapi.Request
        :return: Signed health and readiness dictionary including ``client_lifecycle``.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If the signed service identity headers are missing or invalid (HTTP 401).
        """
        body = await asyncio.to_thread(health_response, settings, request.headers, app.state.instance_id, app.state.ready, history_ready=app.state.history_ready)
        if body is None:
            raise HTTPException(401, "Signed service identity required")
        body["client_lifecycle"] = 1
        return body

    @app.get("/api/v1/capabilities", dependencies=[Depends(authorization)])
    async def capabilities():
        """Return enabled core feature flags, provider status, and encryption state.

        :return: Dictionary describing enabled feature flags, provider state, sync state, and encryption readiness.
        :rtype: dict[str, object]
        """
        if not app.state.history_ready:
            return {"features": {"history_encryption": True, "client_lifecycle": True}, "history_ready": False}
        selected=app.state.provider
        provider_state=selected.state if selected else app.state.provider_state
        send_ready=bool(selected and selected.send_ready)
        devices=await asyncio.to_thread(app.state.store.device_list)
        return {"account_id":await asyncio.to_thread(app.state.store.sms_account),"history_ready":True,"features":{"history_encryption":True,"inbox":True,"search":True,"send":True,"send_note":True,"send_link":True,
                "send_sms":any(device["can_sms"] for device in devices),"sms_history":True,"device_management":True,"devices":True,"e2e_decrypt":True,"ai":True,"ai_summary":True,"ai_draft_reply":True,"ai_saved_summaries":True,"media_images":True,"web_inbox":False,"client_lifecycle":True,"core_settings":True,"core_restart":True,"configuration":True},
            "provider_state":provider_state,"push_sync":await asyncio.to_thread(app.state.store.get_setting,"push_sync",{"state":"idle","last_success_at":None,"error":None}),"encryption_state":app.state.encryption.state,
            "encrypted_pending":await app.state.encryption.pending_count(),"send_ready":send_ready,
            "send_unavailable_reason":None if send_ready else "Provider is not connected; sending is unavailable"}

    async def encryption_response(device):
        """Build the encryption configuration and key-readiness response for a device.

        :param device: Pushbullet device identifier to inspect encryption settings for.
        :type device: str
        :return: Device encryption mode, KWallet entry, and live key-readiness state dictionary.
        :rtype: dict[str, object]
        """
        reference=await asyncio.to_thread(app.state.store.device_encryption,device)
        selected=app.state.provider
        reference['state']=getattr(selected,'key_states',{}).get(device,'unconfigured')
        return reference

    async def device_response(state=None):
        """Build the device list snapshot enriched with per-device encryption state.

        :param state: Optional override string for the returned ``provider_state``.
        :type state: str | None
        :return: Devices snapshot enriched with per-device encryption state and provider status.
        :rtype: dict[str, object]
        """
        selected=app.state.provider
        result=await asyncio.to_thread(app.state.store.devices_snapshot)
        states=getattr(selected,'key_states',{}) if getattr(selected,'account_id',result['account_id'])==result['account_id'] else {}
        for device in result['devices']:
            device['encryption']['state']=states.get(device['id'],'unconfigured')
            device['reachable']=None
        return {**result,'provider_state':state or (selected.state if selected else app.state.provider_state)}

    @app.get('/api/v1/devices', dependencies=[Depends(authorization)])
    async def device_list():
        """Return the current device list and provider status.

        :return: Current device list snapshot and provider status dictionary.
        :rtype: dict[str, object]
        """
        return await device_response()

    @app.post('/api/v1/devices/refresh', dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_refresh():
        """Refresh device registrations and encryption keys from the provider.

        :return: Refreshed device list snapshot and provider status dictionary.
        :rtype: dict[str, object]
        """
        async with app.state.provider_reload_lock:
            return await refresh_device_list()

    async def refresh_device_list():
        """Fetch updated devices and encryption state under the provider reload lock.

        :return: Updated device list snapshot, or a ``device_refresh_failed`` snapshot on error.
        :rtype: dict[str, object]
        """
        selected=app.state.provider
        if selected is None:
            return await device_response()
        try:
            await asyncio.wait_for(selected.refresh_devices(),timeout=20)
            if hasattr(selected,'reload_encryption'):
                await selected.reload_encryption()
        except Exception:
            return await device_response('device_refresh_failed')
        return await device_response()

    class Nickname(BaseModel):
        """Request payload specifying a device nickname.

        :ivar nickname: Trimmed non-empty nickname for a Pushbullet device.
        :type nickname: str
        """

        model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
        nickname: str=Field(min_length=1,max_length=128)

    class DeviceSelection(BaseModel):
        """Request payload identifying a target device.

        :ivar device_id: Target Pushbullet device identifier.
        :type device_id: str
        """

        model_config=ConfigDict(extra='forbid')
        device_id: str=Field(min_length=1,max_length=1024)

    class EncryptionSelection(BaseModel):
        """Request payload configuring shared or device-specific E2E encryption.

        :ivar mode: Encryption key selection mode (``'shared'`` or ``'device'``).
        :type mode: str
        :ivar entry: KWallet entry name when selecting device-specific encryption.
        :type entry: str | None
        :ivar password: Optional new encryption passphrase to store in the device KWallet entry.
        :type password: str | None
        """

        model_config=ConfigDict(extra='forbid')
        mode: str
        entry: str | None=Field(default=None,max_length=256)
        password: str | None=Field(default=None,max_length=16384,repr=False)

    async def require_device(device_id):
        """Verify that the given device ID exists in the local store.

        :param device_id: Pushbullet device identifier to verify in the local store.
        :type device_id: str
        :raises fastapi.HTTPException: If ``device_id`` is not found in the local device list (HTTP 404).
        """
        if not any(row['id']==device_id for row in await asyncio.to_thread(app.state.store.device_list)):
            raise HTTPException(404,'Device not found')

    def require_provider():
        """Return the active Pushbullet provider or raise HTTP 409 if unavailable.

        :return: Active Pushbullet provider instance.
        :rtype: object
        :raises fastapi.HTTPException: If no provider instance is currently configured or active (HTTP 409).
        """
        selected=app.state.provider
        if selected is None:
            raise HTTPException(409,'Provider is unavailable')
        return selected

    async def mutate_device(operation,*args):
        """Execute a serialized device mutation through the active provider.

        :param operation: Name of the provider method to execute (such as ``'register_device'``, ``'rename_device'``, or ``'remove_device'``).
        :type operation: str
        :param args: Positional arguments passed to the provider method.
        :type args: object
        :return: Updated device list snapshot after the mutation completes.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: Re-raises ``HTTPException``; translates ``SubmissionError`` (HTTP 409 if uncertain, otherwise HTTP 502) and unexpected errors (HTTP 502).
        """
        try:
            async with app.state.provider_reload_lock:
                selected=require_provider()
                if operation!='register_device':
                    await require_device(args[0])
                async with app.state.dispatcher.lock:
                    await getattr(selected,operation)(*args)
        except HTTPException:
            raise
        except SubmissionError as exc:
            raise HTTPException(409 if exc.state=='uncertain' else 502,{'state':exc.state,'code':exc.code,'message':exc.message}) from None
        except Exception:
            raise HTTPException(502,'Device operation could not finish; refresh device registrations') from None
        return await device_response()

    @app.post('/api/v1/devices',dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_add(value:Nickname):
        """Register a new Pushbullet device with the given nickname.

        :param value: Validated device nickname payload.
        :type value: Nickname
        :return: Updated device list snapshot including the registered device.
        :rtype: dict[str, object]
        """
        return await mutate_device('register_device',value.nickname)

    class RegistrationResolution(BaseModel):
        """Request payload for resolving an uncertain device registration.

        :ivar device_id: Optional device identifier associated with the uncertain registration.
        :type device_id: str | None
        :ivar acknowledge_unknown_outcome: Whether the caller acknowledges clearing an uncertain registration outcome.
        :type acknowledge_unknown_outcome: bool
        """

        model_config=ConfigDict(extra='forbid')
        device_id: str | None=None
        acknowledge_unknown_outcome: StrictBool

    @app.post('/api/v1/devices/registration/resolve',dependencies=[Depends(authorization)])
    @guard_mutation
    async def resolve_device_registration(value:RegistrationResolution):
        """Resolve a pending uncertain device registration under the reload lock.

        :param value: Validated payload for resolving a pending device registration.
        :type value: RegistrationResolution
        :return: Updated device list snapshot after resolution.
        :rtype: dict[str, object]
        """
        async with app.state.provider_reload_lock:
            return await resolve_registration(value)

    async def resolve_registration(value):
        """Complete or clear an uncertain device registration on the provider.

        :param value: Validated registration resolution payload.
        :type value: RegistrationResolution
        :return: Updated device list snapshot after provider resolution.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If the provider rejects the resolution request with ``ValueError`` (HTTP 409).
        """
        try:
            await require_provider().resolve_registration(value.device_id,value.acknowledge_unknown_outcome)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None
        return await device_response()

    @app.patch('/api/v1/devices/{device_id}',dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_rename(device_id:str,value:Nickname):
        """Rename an existing Pushbullet device.

        :param device_id: Identifier of the device to rename.
        :type device_id: str
        :param value: Validated payload carrying the new device nickname.
        :type value: Nickname
        :return: Updated device list snapshot after renaming.
        :rtype: dict[str, object]
        """
        await require_device(device_id)
        return await mutate_device('rename_device',device_id,value.nickname)

    @app.delete('/api/v1/devices/{device_id}',dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_remove(device_id:str):
        """Remove an existing Pushbullet device registration.

        :param device_id: Identifier of the device registration to remove.
        :type device_id: str
        :return: Updated device list snapshot after removal.
        :rtype: dict[str, object]
        """
        await require_device(device_id)
        return await mutate_device('remove_device',device_id)

    @app.post('/api/v1/devices/{device_id}/encryption',dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_encryption_select(device_id:str,value:EncryptionSelection):
        """Configure shared or device-specific E2E encryption for a device.

        :param device_id: Identifier of the target device.
        :type device_id: str
        :param value: Validated shared or device-specific encryption selection payload.
        :type value: EncryptionSelection
        :return: Dictionary containing the updated ``encryption`` configuration for the device.
        :rtype: dict[str, dict[str, object]]
        """
        async with app.state.provider_reload_lock:
            return await select_encryption(device_id,value)

    async def select_encryption(device_id,value):
        """Validate, persist, and reload E2E encryption configuration for a device.

        :param device_id: Identifier of the target device.
        :type device_id: str
        :param value: Validated encryption selection payload.
        :type value: EncryptionSelection
        :return: Dictionary containing the updated ``encryption`` configuration for the device.
        :rtype: dict[str, dict[str, object]]
        :raises fastapi.HTTPException: If mode, entry, or password options are invalid (HTTP 422), or if credential storage or account verification fails (HTTP 409).
        """
        from pbdesk.credentials import create_device_e2e_password,device_e2e_password
        account=await asyncio.to_thread(app.state.store.account_key)
        await require_device(device_id)
        if value.mode not in ('shared','device'):
            raise HTTPException(422,'Choose shared or device encryption mode')
        entry='e2e-password' if value.mode=='shared' else value.entry
        if not entry or not entry.strip() or entry in {'api-key','ai-openai-api-key'} or (value.mode=='device' and entry=='e2e-password'):
            raise HTTPException(422,'Choose a device-specific KWallet entry')
        if value.password is not None and value.mode!='device':
            raise HTTPException(422,'Shared default credentials are preserved; choose a new device entry')
        try:
            if value.password is not None:
                await asyncio.to_thread(create_device_e2e_password,settings,entry,value.password)
            else:
                password=await asyncio.to_thread(device_e2e_password,settings,entry)
                if not password:
                    raise CredentialLocked('Selected KWallet entry is missing or empty')
                password=None
            await asyncio.to_thread(app.state.store.set_device_encryption,device_id,value.mode,entry,account)
        except (CredentialLocked,ValueError) as exc:
            raise HTTPException(409,str(exc)) from None
        selected=app.state.provider
        if selected is not None:
            try:
                await selected.reload_encryption()
            except Exception:
                pass
        return {'encryption':await encryption_response(device_id)}

    @app.post('/api/v1/devices/{device_id}/encryption/test',dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_encryption_test(device_id:str):
        """Test local E2E key readiness for a specific device.

        :param device_id: Identifier of the device whose local E2E key readiness is tested.
        :type device_id: str
        :return: Encryption status, test outcome, and human-readable detail dictionary.
        :rtype: dict[str, object]
        """
        async with app.state.provider_reload_lock:
            return await test_device_encryption(device_id)

    async def test_device_encryption(device_id):
        """Verify local decryption against stored ciphertexts for a device.

        :param device_id: Identifier of the device to test decryption against.
        :type device_id: str
        :return: Dictionary with ``encryption`` reference, ``tested`` flag, ``verified`` boolean, and ``detail`` message.
        :rtype: dict[str, object]
        """
        await require_device(device_id)
        selected=require_provider()
        try:
            verified=await selected.test_encryption(device_id)
        except Exception:
            verified=False
        reference=await encryption_response(device_id)
        return {'encryption':reference,'tested':True,'verified':verified,
            'detail':'Authenticated local decryption succeeded' if verified else 'Key readiness is shown; no matching ciphertext has been authenticated yet'}

    @app.get('/api/v1/sms',dependencies=[Depends(authorization)])
    async def sms_messages(device_id:str,direction:str|None=None):
        """Return retained SMS conversations and messages for a device.

        :param device_id: Pushbullet device identifier owning the SMS threads.
        :type device_id: str
        :param direction: Optional message direction filter (``'incoming'`` or ``'outgoing'``).
        :type direction: str | None
        :return: Snapshot dictionary of retained SMS messages and sync metadata.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If ``direction`` is neither None nor ``'incoming'``/``'outgoing'`` (HTTP 422).
        """
        if direction is not None and direction not in ('incoming','outgoing'):
            raise HTTPException(422,'Direction must be incoming or outgoing')
        return await asyncio.to_thread(app.state.store.sms_snapshot,device_id,direction)

    @app.post('/api/v1/sms/refresh',dependencies=[Depends(authorization)])
    @guard_mutation
    async def sms_refresh(value:DeviceSelection):
        """Request an SMS history refresh for a device and return the snapshot.

        :param value: Validated payload identifying the device whose SMS history to refresh.
        :type value: DeviceSelection
        :return: Updated SMS snapshot dictionary for the selected device.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If requesting the refresh fails with ``ValueError`` (HTTP 422).
        """
        await require_device(value.device_id)
        selected=app.state.provider
        if selected is not None and hasattr(selected,'request_sms_refresh'):
            try:
                await selected.request_sms_refresh(value.device_id)
            except ValueError as exc:
                raise HTTPException(422,str(exc)) from None
        return await asyncio.to_thread(app.state.store.sms_snapshot,value.device_id)

    @app.post("/api/v1/provider/reload", dependencies=[Depends(authorization)])
    @guard_mutation
    async def provider_reload():
        """Stop and restart the Pushbullet provider and reload encryption keys.

        :return: Updated capabilities dictionary reflecting the reloaded provider and encryption state.
        :rtype: dict[str, object]
        """
        async with app.state.provider_reload_lock:
            async with app.state.dispatcher.lock:
                selected=app.state.provider
                if provider_factory is not None:
                    if selected:
                        await selected.reload_encryption()
                else:
                    task=app.state.provider_task
                    if task:
                        task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                    if selected:
                        await selected.close()
                    app.state.provider=None
                    try:
                        selected=await app.state.start_provider()
                        if selected:
                            await selected.reload_encryption()
                        else:
                            app.state.provider_state="demo" if demo or fixture_path else "unconfigured"
                            await app.state.encryption.clear()
                    except CredentialLocked:
                        app.state.provider_state="locked"
                        await app.state.encryption.clear("locked")
        return await capabilities()

    from pbdesk.api.configuration import install_configuration_routes
    install_configuration_routes(app, authorization, settings, guard_mutation, provider_reload, demo or bool(fixture_path))

    def action_result(value):
        """Return an action record or raise HTTP 404 when missing.

        :param value: Action record dictionary returned by the store, or None if not found.
        :type value: dict[str, object] | None
        :return: Validated non-None action record dictionary.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If ``value`` is None (HTTP 404).
        """
        if value is None:
            raise HTTPException(404,"Action not found")
        return value

    async def action_call(method,*args):
        """Execute a synchronous store action method and map domain errors.

        :param method: Synchronous store action method to run in a worker thread.
        :type method: collections.abc.Callable[..., dict[str, object] | None]
        :param args: Positional arguments forwarded to ``method``.
        :type args: object
        :return: Non-None action record dictionary returned by ``method``.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If ``ActionConflict`` (HTTP 409) or ``InvalidDestination`` (HTTP 422) is raised.
        """
        try:
            return action_result(await asyncio.to_thread(method,*args))
        except ActionConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except InvalidDestination as exc:
            raise HTTPException(422,str(exc)) from None

    @app.post("/api/v1/actions")
    @guard_mutation
    async def action_create(value:ActionRequest,role:str=Depends(authorization)):
        """Create a pending outbound action request.

        :param value: Validated outbound message action request payload.
        :type value: pbdesk.domain.actions.ActionRequest
        :param role: Authenticated caller role acting as the requester.
        :type role: str
        :return: Created or idempotently matched public action dictionary.
        :rtype: dict[str, object]
        """
        return await action_call(app.state.store.create_action,value,role)

    @app.get("/api/v1/actions", dependencies=[Depends(authorization)])
    async def action_list():
        """Return the current snapshot of outbound actions.

        :return: Active account identifier and recent outbound actions snapshot dictionary.
        :rtype: dict[str, object]
        """
        return await asyncio.to_thread(app.state.store.actions_snapshot)

    @app.get("/api/v1/actions/{action_id}", dependencies=[Depends(authorization)])
    async def action_details(action_id:int):
        """Return details for a single outbound action.

        :param action_id: Database identifier of the outbound action to inspect.
        :type action_id: int
        :return: Public action record dictionary.
        :rtype: dict[str, object]
        """
        return action_result(await asyncio.to_thread(app.state.store.action,action_id))

    @app.patch("/api/v1/actions/{action_id}")
    @guard_mutation
    async def action_edit(action_id:int,value:ActionEdit,role:str=Depends(authorization)):
        """Edit a pending outbound action before approval.

        :param action_id: Database identifier of the action to edit.
        :type action_id: int
        :param value: Validated edit payload with expected revision.
        :type value: pbdesk.domain.actions.ActionEdit
        :param role: Authenticated caller role performing the edit.
        :type role: str
        :return: Updated public action record dictionary.
        :rtype: dict[str, object]
        """
        return await action_call(app.state.store.edit_action,action_id,value,role)

    @app.post("/api/v1/actions/{action_id}/approve")
    @guard_mutation
    async def action_approve(action_id:int,value:Approval,role:str=Depends(authorization)):
        """Approve a pending outbound action for dispatch.

        :param action_id: Database identifier of the pending action to approve.
        :type action_id: int
        :param value: Validated approval payload confirming revision, fingerprint, and plaintext delivery.
        :type value: pbdesk.domain.actions.Approval
        :param role: Authenticated caller role approving the action.
        :type role: str
        :return: Approved public action record dictionary.
        :rtype: dict[str, object]
        """
        return await action_call(app.state.store.approve_action,action_id,value,role)

    @app.post("/api/v1/actions/{action_id}/cancel")
    @guard_mutation
    async def action_cancel(action_id:int,value:Cancellation,role:str=Depends(authorization)):
        """Cancel an outbound action with optimistic revision checking.

        :param action_id: Database identifier of the action to cancel.
        :type action_id: int
        :param value: Validated cancellation payload carrying the expected revision.
        :type value: pbdesk.domain.actions.Cancellation
        :param role: Authenticated caller role cancelling the action.
        :type role: str
        :return: Cancelled public action record dictionary.
        :rtype: dict[str, object]
        """
        return await action_call(app.state.store.cancel_action,action_id,value.revision,role)

    @app.get("/api/v1/items", dependencies=[Depends(authorization)])
    async def item_list(q: str | None = None):
        """Return stored inbox items filtered by an optional search query.

        :param q: Optional full-text search query string.
        :type q: str | None
        :return: Snapshot dictionary of account items, latest event sequence, and instance ID.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If ``q`` exceeds 4096 characters (HTTP 422).
        """
        if q and len(q) > 4096:
            raise HTTPException(422, "Search query too long")
        return await asyncio.to_thread(app.state.store.snapshot, q)

    @app.get("/api/v1/items/{item_id}", dependencies=[Depends(authorization)])
    async def item_details(item_id: int):
        """Return a single inbox item by ID.

        :param item_id: Database identifier of the inbox item to retrieve.
        :type item_id: int
        :return: Public dictionary representation of the inbox item.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If the item does not exist for the active account (HTTP 404).
        """
        item = await asyncio.to_thread(app.state.store.item, item_id)
        if item is None:
            raise HTTPException(404, "Item not found")
        return item

    @app.get("/api/v1/events", dependencies=[Depends(authorization)])
    async def event_feed(request: Request, after: int | None = None, instance_id: str | None = None,
                         last_event_id: str | None = Header(default=None)):
        """Stream server-sent change events with cursor replay and resync fallback.

        :param request: Incoming FastAPI request used to monitor disconnects and revalidate bearer credentials.
        :type request: fastapi.Request
        :param after: Optional query-parameter event sequence cursor to resume from.
        :type after: int | None
        :param instance_id: Optional core instance ID expected by the reconnecting client.
        :type instance_id: str | None
        :param last_event_id: Optional ``Last-Event-ID`` SSE header used when ``after`` is omitted.
        :type last_event_id: str | None
        :return: SSE streaming response emitting resync, change, and keepalive events.
        :rtype: pbdesk.api.streaming.BoundedStreamingResponse
        """
        supplied = after is not None or last_event_id is not None
        if after is None:
            try:
                after = int(last_event_id) if last_event_id is not None else 0
            except ValueError:
                after = -1
        async def stream():
            """Generate SSE frames for resyncs, incremental item changes, and keepalives.

            :return: Server-sent event formatted strings for resyncs, item changes, and keepalives.
            :rtype: collections.abc.AsyncIterator[str]
            """
            cursor = after
            store = app.state.store
            low, high = await asyncio.to_thread(store.event_bounds)
            if not supplied or (instance_id is not None and instance_id != store.instance_id) or cursor < max(low - 1, 0) or cursor > high:
                cursor = high
                data = {"sequence": high, "instance_id": store.instance_id, "reason": "initial" if not supplied else "resync"}
                yield f"id: {high}\nevent: resync\ndata: {json.dumps(data)}\n\n"
            heartbeat = time.monotonic()
            checked_credential = heartbeat
            credential = request.headers.get("authorization", "")[7:]
            while app.state.ready and not await request.is_disconnected():
                if time.monotonic() - checked_credential > 1:
                    if await asyncio.to_thread(identify_service, settings, credential) is None:
                        return
                    checked_credential = time.monotonic()
                low, high = await asyncio.to_thread(store.event_bounds)
                if cursor < low - 1:
                    # A slow reader has fallen outside bounded replay: force a fresh snapshot.
                    yield f"id: {high}\nevent: resync\ndata: {json.dumps({'sequence': high, 'instance_id': store.instance_id, 'reason': 'slow_client'})}\n\n"
                    return
                changes = await asyncio.to_thread(store.events_after, cursor)
                for change in changes:
                    yield f"id: {change['sequence']}\nevent: change\ndata: {json.dumps(change)}\n\n"
                    cursor = change["sequence"]
                if time.monotonic() - heartbeat > 15:
                    yield ": keepalive\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(.25)
        return BoundedStreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    class WebSetting(BaseModel):
        """Request payload toggling the optional local web host.

        :ivar enabled: Desired enabled state for the optional local web host.
        :type enabled: bool
        """

        model_config = ConfigDict(extra="forbid")
        enabled: bool

    @app.get("/api/v1/web", dependencies=[Depends(authorization)])
    async def web_status():
        """Return the current status of the local web host.

        :return: Dictionary describing web host enabled state, URL, ownership, and error status.
        :rtype: dict[str, object]
        """
        return app.state.host.status()

    @app.post("/api/v1/web", dependencies=[Depends(authorization)])
    @guard_mutation
    async def web_setting(value: WebSetting):
        """Enable or disable the local web host and persist the preference.

        :param value: Validated request payload toggling the web host.
        :type value: WebSetting
        :return: Updated web host status dictionary.
        :rtype: dict[str, object]
        """
        return await app.state.host.set_enabled(value.enabled, persist=True)

    class ClientRegistration(BaseModel):
        """Request payload for registering a client lifecycle lease.

        :ivar instance_id: Core instance identifier the client expects to lease against.
        :type instance_id: str
        :ivar generation: Stop All generation token matching the active launcher generation.
        :type generation: str
        """

        model_config = ConfigDict(extra="forbid")
        instance_id: str = Field(max_length=64)
        generation: str = Field(min_length=64, max_length=64)

    class ClientInstance(BaseModel):
        """Request payload identifying a client instance for lease updates.

        :ivar instance_id: Core instance identifier owning the active lease.
        :type instance_id: str
        """

        model_config = ConfigDict(extra="forbid")
        instance_id: str = Field(max_length=64)

    def client_role(role):
        """Verify that the caller role is eligible to hold a client lifecycle lease.

        :param role: Authenticated caller role to validate for client lease ownership.
        :type role: str
        :raises fastapi.HTTPException: If ``role`` is not ``'desktop'`` or ``'web'`` (HTTP 403).
        """
        if role not in {"desktop", "web"}:
            raise HTTPException(403, "Only desktop and web processes hold client leases")

    @app.post("/api/v1/clients")
    async def register_client(value: ClientRegistration, role: str = Depends(authorization)):
        """Register a new desktop or web client lifecycle lease.

        :param value: Validated registration payload with core instance ID and Stop All generation.
        :type value: ClientRegistration
        :param role: Authenticated caller role requesting the lease.
        :type role: str
        :return: Issued lease dictionary containing ``lease_id``, ``instance_id``, and ``renew_after``.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If the core instance or generation mismatches or core is stopping (HTTP 409).
        """
        client_role(role)
        try:
            return app.state.clients.register(role, value.instance_id, value.generation)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.put("/api/v1/clients/{lease_id}")
    async def renew_client(lease_id: str, value: ClientInstance, role: str = Depends(authorization)):
        """Renew an active client lifecycle lease.

        :param lease_id: Identifier of the active client lease to renew.
        :type lease_id: str
        :param value: Validated payload carrying the expected core ``instance_id``.
        :type value: ClientInstance
        :param role: Authenticated caller role renewing the lease.
        :type role: str
        :return: Lease update dictionary with ``released`` flag and ``instance_id``.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If the lease is unknown or belongs to another role or instance (HTTP 409).
        """
        client_role(role)
        try:
            return app.state.clients.update(role, value.instance_id, lease_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/v1/clients/{lease_id}")
    async def release_client(lease_id: str, instance_id: str, role: str = Depends(authorization)):
        """Release an active client lifecycle lease.

        :param lease_id: Identifier of the active client lease to release.
        :type lease_id: str
        :param instance_id: Core instance identifier owning the lease.
        :type instance_id: str
        :param role: Authenticated caller role releasing the lease.
        :type role: str
        :return: Lease release dictionary with ``released`` flag and ``instance_id``.
        :rtype: dict[str, object]
        :raises fastapi.HTTPException: If the lease is unknown or belongs to another role or instance (HTTP 409).
        """
        client_role(role)
        try:
            return app.state.clients.update(role, instance_id, lease_id, release=True)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/shutdown", dependencies=[Depends(authorization)])
    async def shutdown():
        """Advance stop generation, disable the web host, and schedule core shutdown.

        :return: Dictionary indicating whether a core shutdown callback was scheduled.
        :rtype: dict[str, bool]
        """
        # Persist before stopping either process. Existing clients cannot undo Stop All.
        await asyncio.to_thread(stop_generation, settings, advance=True)
        await asyncio.to_thread(cancel_restart, settings, app.state.instance_id)
        app.state.restart_intent = False
        app.state.clients.stopping = True
        await app.state.host.set_enabled(False)
        if app.state.shutdown_callback:
            asyncio.get_running_loop().call_later(.2, app.state.shutdown_callback)
        return {"stopping": app.state.shutdown_callback is not None}

    return app
