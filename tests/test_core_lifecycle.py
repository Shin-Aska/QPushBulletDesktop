import errno
import httpx
import pytest
from pbdesk.config import Settings
from pbdesk.credentials import health_headers,health_response
from pbdesk.services.lifecycle import _probe,CoreStartupError


def test_foreign_listener_never_gets_bearer(tmp_path):
    settings=Settings(tmp_path)
    requests=[]
    def foreign(request):
        requests.append(request)
        return httpx.Response(200,json={"application":"pbdesk-core","api_version":1,"instance_id":"faked","ready":True})
    with httpx.Client(transport=httpx.MockTransport(foreign)) as client:
        with pytest.raises(CoreStartupError): _probe(settings,"secret",client)
    assert requests[0].headers.get("authorization") is None
    assert "secret" not in str(requests[0].headers)
    assert "X-PBDesk-Proof".lower() in requests[0].headers


def test_authenticated_probe(tmp_path):
    import uuid
    settings=Settings(tmp_path)
    def server(request):
        return httpx.Response(200,json=health_response(settings,request.headers,str(uuid.uuid4()),True))
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        assert _probe(settings,"not-transmitted",client)["ready"]


def test_connection_permission_error_not_eligible_start(tmp_path):
    settings=Settings(tmp_path)
    def bad(request):
        try: raise PermissionError(errno.EACCES,"denied")
        except PermissionError as exc: raise httpx.ConnectError("cannot connect") from exc
    with httpx.Client(transport=httpx.MockTransport(bad)) as client:
        with pytest.raises(CoreStartupError,match="permissions"): _probe(settings,"",client)


def test_available_systemd_uses_transient_unit(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from pbdesk.services import lifecycle
    settings=Settings(tmp_path)
    calls=[]
    answers=iter([None,{"ready":True,"client_lifecycle":1}])
    monkeypatch.delenv("PBDESK_LIFECYCLE",raising=False)
    monkeypatch.setattr(lifecycle,"_probe",lambda *a:next(answers))
    def runner(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(lifecycle.subprocess,"run",runner)
    monkeypatch.setattr(lifecycle.subprocess,"Popen",lambda *a,**k:pytest.fail("foreground must not also start"))
    assert lifecycle.ensure_core(settings,demo=True)["ready"]
    transient=calls[1]
    assert transient[:3] == ["systemd-run","--user","--collect"]
    assert "--service-type=exec" in transient and "--demo" in transient and "--notifications" in transient
    assert "--setenv=PBDESK_STATE_DIR=" + str(tmp_path) in transient
    assert "Authorization" not in str(transient)


def test_available_manager_rejection_does_not_fallback(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from pbdesk.services import lifecycle
    settings=Settings(tmp_path)
    answers=iter([0,1])
    monkeypatch.delenv("PBDESK_LIFECYCLE",raising=False)
    monkeypatch.setattr(lifecycle,"_probe",lambda *a:None)
    monkeypatch.setattr(lifecycle.subprocess,"run",lambda *a,**k:SimpleNamespace(returncode=next(answers)))
    monkeypatch.setattr(lifecycle.subprocess,"Popen",lambda *a,**k:pytest.fail("must not launch around manager failure"))
    with pytest.raises(CoreStartupError,match="rejected"):
        lifecycle.ensure_core(settings)
