"""Version 1 authenticated, scoped internal API."""
import asyncio
from contextlib import asynccontextmanager
from functools import wraps
import json
import os
from pathlib import Path
import time
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


def create_app(settings=None, fixture_path=None, notifier=None, demo=False, notifications=False, provider_factory=None, standalone=False, lease_timing=None, launch_generation=None, restart_launcher=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        lock = await asyncio.to_thread(InstanceLock, settings)
        tasks = []
        store = None
        provider = None
        host = WebHost(settings)
        try:
            for role in ("desktop", "web", "mcp"):
                await asyncio.to_thread(service_token, settings, role)
            store = await asyncio.to_thread(Store, settings)
            host.store = store
            app.state.clients = LeaseRegistry(store.instance_id, launch_generation or await asyncio.to_thread(stop_generation, settings),
                lambda: app.state.shutdown_callback and app.state.shutdown_callback(), standalone=standalone, timing=lease_timing)
            tasks.append(asyncio.create_task(app.state.clients.run()))
            app.state.store = store
            app.state.host = host
            app.state.provider_state = "demo" if demo or fixture_path else "unconfigured"
            app.state.provider = None
            app.state.ready = True
            saved_notifications = await asyncio.to_thread(store.get_setting, "core_notifications_enabled", notifications)
            adapter = notifier or Notifier(saved_notifications if type(saved_notifications) is bool else notifications)
            app.state.notifier = adapter
            async def consume_plain(payload):
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
            timing = app.state.clients.timing
            profile = None if provider_factory is not None or notifier is not None else {
                'demo': demo, 'fixture': str(Path(fixture_path).resolve()) if fixture_path else None,
                'standalone': standalone, 'notifications': notifications,
                'timing': {'renew': timing.renew, 'expiry': timing.expiry,
                    'release_grace': timing.release_grace, 'startup_grace': timing.startup_grace}}
            app.state.core_settings = CoreSettingsService(app, settings, profile, launcher=restart_launcher)
            app.state.provider_task=None
            if demo:
                await ingest(DEMO)
            if fixture_path:
                payloads = json.loads(await asyncio.to_thread(Path(fixture_path).read_text))
                if not isinstance(payloads, list) or len(payloads) > 10000:
                    raise ValueError("Fixture must be a JSON array of at most 10000 events")
                for payload in payloads:
                    await ingest(payload)
            async def start_provider():
                if provider_factory is not None:
                    selected=provider_factory(settings,store,ingest,encryption)
                elif not demo and not fixture_path:
                    token=await asyncio.to_thread(provider_token,settings)
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
            if store:
                await asyncio.to_thread(store.close)
            lock.close()

    app = FastAPI(title="pbdesk private core", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.ready = False
    app.state.shutdown_callback = None
    app.state.restart_intent = False
    app.state.core_settings = None

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        return JSONResponse(status_code=422,content={'detail':[
            {'type':error['type'],'loc':error['loc'],'msg':error['msg']} for error in exc.errors()
        ]})

    async def authorization(request: Request, authorization: str | None = Header(default=None)):
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
        return role

    def guard_mutation(function):
        @wraps(function)
        async def guarded(*args, **kwargs):
            async with app.state.core_settings.gate.operation():
                return await finish_mutation(function(*args, **kwargs))
        return guarded

    from pbdesk.api.core_settings import install_core_settings_routes
    install_core_settings_routes(app, authorization)
    from pbdesk.api.ai import install_ai_routes
    from pbdesk.api.media import install_media_routes
    install_ai_routes(app, authorization, settings)
    install_media_routes(app, authorization, settings)

    @app.middleware("http")
    async def protect_origin(request, call_next):
        # Service APIs accept no browser cross-origin requests, even with a leaked token.
        if request.headers.get("origin"):
            return __import__("starlette.responses", fromlist=["JSONResponse"]).JSONResponse({"detail": "Browser origins are not accepted by private core"}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    async def health(request: Request):
        body = await asyncio.to_thread(health_response, settings, request.headers, app.state.store.instance_id, app.state.ready)
        if body is None:
            raise HTTPException(401, "Signed service identity required")
        body["client_lifecycle"] = 1
        return body

    @app.get("/api/v1/capabilities", dependencies=[Depends(authorization)])
    async def capabilities():
        selected=app.state.provider
        provider_state=selected.state if selected else app.state.provider_state
        send_ready=bool(selected and selected.send_ready)
        devices=await asyncio.to_thread(app.state.store.device_list)
        return {"account_id":await asyncio.to_thread(app.state.store.sms_account),"features":{"inbox":True,"search":True,"send":True,"send_note":True,"send_link":True,
                "send_sms":any(device["can_sms"] for device in devices),"sms_history":True,"device_management":True,"devices":True,"e2e_decrypt":True,"ai":True,"ai_summary":True,"ai_draft_reply":True,"ai_saved_summaries":True,"media_images":True,"web_inbox":False,"client_lifecycle":True,"core_settings":True,"core_restart":True},
            "provider_state":provider_state,"push_sync":await asyncio.to_thread(app.state.store.get_setting,"push_sync",{"state":"idle","last_success_at":None,"error":None}),"encryption_state":app.state.encryption.state,
            "encrypted_pending":await app.state.encryption.pending_count(),"send_ready":send_ready,
            "send_unavailable_reason":None if send_ready else "Provider is not connected; sending is unavailable"}

    async def encryption_response(device):
        reference=await asyncio.to_thread(app.state.store.device_encryption,device)
        selected=app.state.provider
        reference['state']=getattr(selected,'key_states',{}).get(device,'unconfigured')
        return reference

    async def device_response(state=None):
        selected=app.state.provider
        result=await asyncio.to_thread(app.state.store.devices_snapshot)
        states=getattr(selected,'key_states',{}) if getattr(selected,'account_id',result['account_id'])==result['account_id'] else {}
        for device in result['devices']:
            device['encryption']['state']=states.get(device['id'],'unconfigured')
            device['reachable']=None
        return {**result,'provider_state':state or (selected.state if selected else app.state.provider_state)}

    @app.get('/api/v1/devices', dependencies=[Depends(authorization)])
    async def device_list():
        return await device_response()

    @app.post('/api/v1/devices/refresh', dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_refresh():
        async with app.state.provider_reload_lock:
            return await refresh_device_list()

    async def refresh_device_list():
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
        model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
        nickname: str=Field(min_length=1,max_length=128)

    class DeviceSelection(BaseModel):
        model_config=ConfigDict(extra='forbid')
        device_id: str=Field(min_length=1,max_length=1024)

    class EncryptionSelection(BaseModel):
        model_config=ConfigDict(extra='forbid')
        mode: str
        entry: str | None=Field(default=None,max_length=256)
        password: str | None=Field(default=None,max_length=16384,repr=False)

    async def require_device(device_id):
        if not any(row['id']==device_id for row in await asyncio.to_thread(app.state.store.device_list)):
            raise HTTPException(404,'Device not found')

    def require_provider():
        selected=app.state.provider
        if selected is None:
            raise HTTPException(409,'Provider is unavailable')
        return selected

    async def mutate_device(operation,*args):
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
        return await mutate_device('register_device',value.nickname)

    class RegistrationResolution(BaseModel):
        model_config=ConfigDict(extra='forbid')
        device_id: str | None=None
        acknowledge_unknown_outcome: StrictBool

    @app.post('/api/v1/devices/registration/resolve',dependencies=[Depends(authorization)])
    @guard_mutation
    async def resolve_device_registration(value:RegistrationResolution):
        async with app.state.provider_reload_lock:
            return await resolve_registration(value)

    async def resolve_registration(value):
        try:
            await require_provider().resolve_registration(value.device_id,value.acknowledge_unknown_outcome)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None
        return await device_response()

    @app.patch('/api/v1/devices/{device_id}',dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_rename(device_id:str,value:Nickname):
        await require_device(device_id)
        return await mutate_device('rename_device',device_id,value.nickname)

    @app.delete('/api/v1/devices/{device_id}',dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_remove(device_id:str):
        await require_device(device_id)
        return await mutate_device('remove_device',device_id)

    @app.post('/api/v1/devices/{device_id}/encryption',dependencies=[Depends(authorization)])
    @guard_mutation
    async def device_encryption_select(device_id:str,value:EncryptionSelection):
        async with app.state.provider_reload_lock:
            return await select_encryption(device_id,value)

    async def select_encryption(device_id,value):
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
        async with app.state.provider_reload_lock:
            return await test_device_encryption(device_id)

    async def test_device_encryption(device_id):
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
        if direction is not None and direction not in ('incoming','outgoing'):
            raise HTTPException(422,'Direction must be incoming or outgoing')
        return await asyncio.to_thread(app.state.store.sms_snapshot,device_id,direction)

    @app.post('/api/v1/sms/refresh',dependencies=[Depends(authorization)])
    @guard_mutation
    async def sms_refresh(value:DeviceSelection):
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

    def action_result(value):
        if value is None:
            raise HTTPException(404,"Action not found")
        return value

    async def action_call(method,*args):
        try:
            return action_result(await asyncio.to_thread(method,*args))
        except ActionConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except InvalidDestination as exc:
            raise HTTPException(422,str(exc)) from None

    @app.post("/api/v1/actions")
    @guard_mutation
    async def action_create(value:ActionRequest,role:str=Depends(authorization)):
        return await action_call(app.state.store.create_action,value,role)

    @app.get("/api/v1/actions", dependencies=[Depends(authorization)])
    async def action_list():
        return await asyncio.to_thread(app.state.store.actions_snapshot)

    @app.get("/api/v1/actions/{action_id}", dependencies=[Depends(authorization)])
    async def action_details(action_id:int):
        return action_result(await asyncio.to_thread(app.state.store.action,action_id))

    @app.patch("/api/v1/actions/{action_id}")
    @guard_mutation
    async def action_edit(action_id:int,value:ActionEdit,role:str=Depends(authorization)):
        return await action_call(app.state.store.edit_action,action_id,value,role)

    @app.post("/api/v1/actions/{action_id}/approve")
    @guard_mutation
    async def action_approve(action_id:int,value:Approval,role:str=Depends(authorization)):
        return await action_call(app.state.store.approve_action,action_id,value,role)

    @app.post("/api/v1/actions/{action_id}/cancel")
    @guard_mutation
    async def action_cancel(action_id:int,value:Cancellation,role:str=Depends(authorization)):
        return await action_call(app.state.store.cancel_action,action_id,value.revision,role)

    @app.get("/api/v1/items", dependencies=[Depends(authorization)])
    async def item_list(q: str | None = None):
        if q and len(q) > 4096:
            raise HTTPException(422, "Search query too long")
        return await asyncio.to_thread(app.state.store.snapshot, q)

    @app.get("/api/v1/items/{item_id}", dependencies=[Depends(authorization)])
    async def item_details(item_id: int):
        item = await asyncio.to_thread(app.state.store.item, item_id)
        if item is None:
            raise HTTPException(404, "Item not found")
        return item

    @app.get("/api/v1/events", dependencies=[Depends(authorization)])
    async def event_feed(request: Request, after: int | None = None, instance_id: str | None = None,
                         last_event_id: str | None = Header(default=None)):
        supplied = after is not None or last_event_id is not None
        if after is None:
            try:
                after = int(last_event_id) if last_event_id is not None else 0
            except ValueError:
                after = -1
        async def stream():
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
        model_config = ConfigDict(extra="forbid")
        enabled: bool

    @app.get("/api/v1/web", dependencies=[Depends(authorization)])
    async def web_status():
        return app.state.host.status()

    @app.post("/api/v1/web", dependencies=[Depends(authorization)])
    @guard_mutation
    async def web_setting(value: WebSetting):
        return await app.state.host.set_enabled(value.enabled, persist=True)

    class ClientRegistration(BaseModel):
        model_config = ConfigDict(extra="forbid")
        instance_id: str = Field(max_length=64)
        generation: str = Field(min_length=64, max_length=64)

    class ClientInstance(BaseModel):
        model_config = ConfigDict(extra="forbid")
        instance_id: str = Field(max_length=64)

    def client_role(role):
        if role not in {"desktop", "web"}:
            raise HTTPException(403, "Only desktop and web processes hold client leases")

    @app.post("/api/v1/clients")
    async def register_client(value: ClientRegistration, role: str = Depends(authorization)):
        client_role(role)
        try:
            return app.state.clients.register(role, value.instance_id, value.generation)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.put("/api/v1/clients/{lease_id}")
    async def renew_client(lease_id: str, value: ClientInstance, role: str = Depends(authorization)):
        client_role(role)
        try:
            return app.state.clients.update(role, value.instance_id, lease_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/v1/clients/{lease_id}")
    async def release_client(lease_id: str, instance_id: str, role: str = Depends(authorization)):
        client_role(role)
        try:
            return app.state.clients.update(role, instance_id, lease_id, release=True)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/shutdown", dependencies=[Depends(authorization)])
    async def shutdown():
        # Persist before stopping either process. Existing clients cannot undo Stop All.
        await asyncio.to_thread(stop_generation, settings, advance=True)
        await asyncio.to_thread(cancel_restart, settings, app.state.store.instance_id)
        app.state.restart_intent = False
        app.state.clients.stopping = True
        await app.state.host.set_enabled(False)
        if app.state.shutdown_callback:
            asyncio.get_running_loop().call_later(.2, app.state.shutdown_callback)
        return {"stopping": app.state.shutdown_callback is not None}

    return app
