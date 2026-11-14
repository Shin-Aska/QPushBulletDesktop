import hashlib
import json
from pathlib import Path

import httpx
import asyncio

class TestClient:
    __test__ = False
    def __init__(self, app, base_url):
        self.app = app
        self.base_url = base_url
    def request(self, method, path, **kwargs):
        async def run():
            async with ORIGINAL_CLIENT(transport=httpx.ASGITransport(app=self.app), base_url=self.base_url) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(run())
    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)
    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

ORIGINAL_CLIENT = httpx.AsyncClient
from pbdesk.config import Settings
from pbdesk.credentials import health_response
from pbdesk.web import app as web


def test_status_local_assets_and_security(tmp_path, monkeypatch):
    actual = httpx.AsyncClient
    settings = Settings(tmp_path)
    def handler(request):
        if request.url.path.endswith("health"):
            assert "Authorization" not in request.headers
            return httpx.Response(200, json=health_response(settings, request.headers, "11111111-1111-1111-1111-111111111111", True))
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, json={"provider_state": "<script>unsafe</script>"})
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handler)))
    client = TestClient(web.create_app(Settings(tmp_path)), base_url="http://127.0.0.1")
    page = client.get("/")
    assert page.status_code == 200
    assert "Ready" in page.text
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in page.text
    assert "Bearer" not in page.text
    assert "8765" not in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    for asset in ("vendor/bootstrap/bootstrap.min.css", "vendor/bootstrap/bootstrap.bundle.min.js", "css/app.css", "js/app.js"):
        assert client.get("/static/" + asset).status_code == 200
    assert client.post("/").status_code == 405
    assert client.get("/api/v1/items").status_code == 404
    assert client.get("/", headers={"host": "untrusted.example"}).status_code == 400


def test_unavailable_status_has_no_private_payload(tmp_path, monkeypatch):
    actual = httpx.AsyncClient
    def fail(request):
        raise httpx.ConnectError("secret-provider-token", request=request)
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(fail)))
    client = TestClient(web.create_app(Settings(tmp_path)), base_url="http://localhost")
    page = client.get("/")
    assert "unavailable" in page.text
    assert "secret-provider-token" not in page.text


def test_vendor_integrity_and_no_remote_runtime_dependencies():
    root = Path(web.__file__).parent
    manifest = json.loads((root / "vendor-manifest.json").read_text())
    assert manifest["version"] == "5.3.8"
    for entry in manifest["assets"]:
        data = (root / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        if entry["file"].endswith((".css", ".js")):
            source = data.decode()
            assert "sourceMappingURL" not in source
            assert "@import" not in source
            # Bootstrap's embedded SVG data URLs and documentation comments are local assets.
            import re
            assert not re.search(r'url\([\s\"\']*https?://', source)
            assert not re.search(r'\bimport\s+.*[\"\']https?://', source)


def test_foreign_core_never_receives_bearer(tmp_path, monkeypatch):
    seen = []
    actual = httpx.AsyncClient
    def foreign(request):
        seen.append(request)
        return httpx.Response(200, json={"application": "pbdesk-core", "api_version": 1, "instance_id": "11111111-1111-1111-1111-111111111111", "ready": True, "proof": "forged"})
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(foreign)))
    client = TestClient(web.create_app(Settings(tmp_path)), base_url="http://localhost")
    assert "unavailable" in client.get("/").text
    assert len(seen) == 1
    assert seen[0].url.path == "/api/v1/health"
    assert "Authorization" not in seen[0].headers
