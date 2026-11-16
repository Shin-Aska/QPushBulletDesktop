"""Optional loopback status host. Browser inbox/authentication remain deferred."""
from pathlib import Path
import os
import asyncio
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pbdesk.config import Settings
from pbdesk.credentials import service_token, health_headers, verify_health, identify_service

from pbdesk.services.client_lifecycle import ClientLease, stop_generation
from pbdesk.services.web_identity import web_health_response

ROOT = Path(__file__).parent
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def create_app(settings: Settings | None = None, *, standalone=False) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.web_host not in LOOPBACK or settings.core_host not in LOOPBACK:
        raise ValueError("Only loopback hosting is supported; remote authentication is not implemented.")
    token = service_token(settings, "web")
    instance_id = os.environ.get('PBDESK_WEB_INSTANCE_ID') or secrets.token_hex(32)
    generation = os.environ.get('PBDESK_STOP_GENERATION') or stop_generation(settings)
    @asynccontextmanager
    async def lifespan(app):
        lease = None
        monitor = None
        try:
            if not standalone:
                lease = ClientLease(settings, 'web', generation=generation,
                    demo=os.environ.get('PBDESK_LIFECYCLE_DEMO') == '1')
                await asyncio.to_thread(lease.start)
                async def check_stop():
                    while True:
                        await asyncio.sleep(.5)
                        if await asyncio.to_thread(stop_generation, settings) != generation:
                            if app.state.shutdown_callback:
                                app.state.shutdown_callback()
                            return
                monitor = asyncio.create_task(check_stop())
            yield
        finally:
            if monitor:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            if lease:
                await asyncio.to_thread(lease.close)
    app = FastAPI(lifespan=lifespan, title="pbdesk local status", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.shutdown_callback = None
    app.state.instance_id = instance_id
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOOPBACK))
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.headers.get('origin'):
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': 'Browser origins are not accepted'}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    async def host_health(request: Request):
        body = await asyncio.to_thread(web_health_response, settings, request.headers, instance_id, generation)
        if body is None:
            raise HTTPException(401, 'Signed web identity required')
        return body

    @app.post('/internal/shutdown')
    async def shutdown(request: Request):
        authorization = request.headers.get('authorization', '')
        if not authorization.startswith('Bearer ') or await asyncio.to_thread(identify_service, settings, authorization[7:]) != 'desktop':
            raise HTTPException(401, 'Desktop service credential required')
        try:
            value = await request.json()
        except ValueError:
            raise HTTPException(422, 'Expected web instance identity') from None
        if value != {'instance_id': instance_id}:
            raise HTTPException(409, 'Web instance changed')
        if app.state.shutdown_callback:
            asyncio.get_running_loop().call_later(.1, app.state.shutdown_callback)
        return {'stopping': app.state.shutdown_callback is not None}

    @app.get("/", response_class=HTMLResponse)
    async def status(request: Request):
        ready = False
        provider = "unavailable"
        error = None
        try:
            async with httpx.AsyncClient(base_url=f"http://{settings.core_host}:{settings.core_port}", timeout=2, trust_env=False, follow_redirects=False) as client:
                proof = health_headers(settings, "web")
                health = await client.get("/api/v1/health", headers=proof)
                health.raise_for_status()
                identity = health.json()
                if not verify_health(settings, "web", proof, identity):
                    raise ValueError("Unexpected core identity")
                if identity.get('history_encryption') != 1 or not identity.get('history_ready'):
                    raise ValueError('Encrypted history unavailable')
                capabilities = await client.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})
                capabilities.raise_for_status()
                ready = bool(identity.get("ready"))
                provider = str(capabilities.json().get("provider_state", "unknown"))
        except (httpx.HTTPError, ValueError, KeyError):
            error = "Encrypted history is locked or the private core is unavailable. Open the desktop to protect or unlock history; restart an older core."
        return templates.TemplateResponse(request=request, name="status.html", context={"ready": ready, "provider": provider, "error": error})

    return app


def main():
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser(description='Optional local web status server')
    parser.add_argument('--standalone', action='store_true', help='Serve unavailable status without managing a core (debug only)')
    args = parser.parse_args()
    settings = Settings.from_env()
    app = create_app(settings, standalone=args.standalone)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.web_host, port=settings.web_port,
        workers=1, proxy_headers=False, access_log=False, timeout_graceful_shutdown=5))
    app.state.shutdown_callback = lambda: setattr(server, 'should_exit', True)
    server.run()
