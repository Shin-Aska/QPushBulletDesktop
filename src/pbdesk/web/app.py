"""Optional loopback status host. Browser inbox/authentication remain deferred."""
from pathlib import Path
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pbdesk.config import Settings
from pbdesk.credentials import service_token, health_headers, verify_health

ROOT = Path(__file__).parent
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.web_host not in LOOPBACK or settings.core_host not in LOOPBACK:
        raise ValueError("Only loopback hosting is supported; remote authentication is not implemented.")
    token = service_token(settings, "web")
    app = FastAPI(title="pbdesk local status", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOOPBACK))
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    async def host_health():
        return {"application": "pbdesk-web", "api_version": 1, "instance_id": os.environ.get("PBDESK_WEB_INSTANCE_ID", "standalone")}

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
                capabilities = await client.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})
                capabilities.raise_for_status()
                ready = bool(identity.get("ready"))
                provider = str(capabilities.json().get("provider_state", "unknown"))
        except (httpx.HTTPError, ValueError, KeyError):
            error = "The private core is unavailable. Open the desktop application or run pbdesk-diagnostics."
        return templates.TemplateResponse(request=request, name="status.html", context={"ready": ready, "provider": provider, "error": error})

    return app


def main():
    import uvicorn
    settings = Settings.from_env()
    app = create_app(settings)
    uvicorn.run(app, host=settings.web_host, port=settings.web_port, workers=1, proxy_headers=False, access_log=False)
