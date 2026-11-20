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
    """Create the loopback-only FastAPI web status application.

    :param settings: Optional application settings; defaults to ``Settings.from_env()``.
    :type settings: Settings | None
    :param standalone: If True, skip creating and polling a core client lease.
    :type standalone: bool
    :return: Configured FastAPI web status application.
    :rtype: FastAPI
    :raises ValueError: If ``settings.web_host`` or ``settings.core_host`` is not a loopback address.
    """
    settings = settings or Settings.from_env()
    if settings.web_host not in LOOPBACK or settings.core_host not in LOOPBACK:
        raise ValueError("Only loopback hosting is supported; remote authentication is not implemented.")
    token = service_token(settings, "web")
    instance_id = os.environ.get('PBDESK_WEB_INSTANCE_ID') or secrets.token_hex(32)
    generation = os.environ.get('PBDESK_STOP_GENERATION') or stop_generation(settings)
    @asynccontextmanager
    async def lifespan(app):
        """Manage the web client lease and stop-generation monitor across app lifespan.

        :param app: FastAPI application instance whose state holds the shutdown callback.
        :type app: FastAPI
        :return: Async context iterator spanning application startup through shutdown.
        :rtype: AsyncIterator[None]
        """
        lease = None
        monitor = None
        try:
            if not standalone:
                lease = ClientLease(settings, 'web', generation=generation,
                    demo=os.environ.get('PBDESK_LIFECYCLE_DEMO') == '1')
                await asyncio.to_thread(lease.start)
                async def check_stop():
                    """Poll for stop-generation changes and trigger application shutdown."""
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
        """Reject cross-origin requests and attach strict security headers.

        :param request: Incoming HTTP request to inspect.
        :type request: Request
        :param call_next: Next ASGI/FastAPI request handler in the middleware chain.
        :type call_next: Callable[[Request], Awaitable[Response]]
        :return: HTTP response with strict security headers, or a 403 JSON error for browser origins.
        :rtype: Response
        """
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
        """Return a signed web host health response for authorized probes.

        :param request: Incoming HTTP health probe request containing signed headers.
        :type request: Request
        :return: Signed web host health payload dictionary.
        :rtype: dict[str, Any]
        :raises HTTPException: With status 401 if the request lacks a valid signed web probe proof.
        """
        body = await asyncio.to_thread(web_health_response, settings, request.headers, instance_id, generation)
        if body is None:
            raise HTTPException(401, 'Signed web identity required')
        return body

    @app.post('/internal/shutdown')
    async def shutdown(request: Request):
        """Validate a desktop shutdown request and schedule server exit.

        :param request: Incoming HTTP POST request authenticated with the desktop service token.
        :type request: Request
        :return: Status dictionary indicating whether a shutdown callback was scheduled.
        :rtype: dict[str, bool]
        :raises HTTPException: With status 401, 422, or 409 if authorization, JSON payload, or instance ID fails validation.
        """
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
        """Render the local core and provider status page.

        :param request: Incoming HTTP GET request for the status page.
        :type request: Request
        :return: Rendered HTML template response displaying core and provider status.
        :rtype: HTMLResponse
        """
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
    """Parse CLI flags and run the local web status server."""
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
