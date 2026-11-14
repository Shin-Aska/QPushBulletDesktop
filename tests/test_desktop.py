import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from pbdesk.desktop.app import InboxModel, MainWindow
from pbdesk.desktop.worker import CoreWorker
from pbdesk.config import Settings


ITEM = {"id": 1, "title": "Sanitized stored note", "body": "Hello <b>world</b>", "type": "stored_note", "created_at": "2026-11-13T00:00:00Z", "url": None}
WEB = {"enabled": False, "url": "http://127.0.0.1:8766", "host": "127.0.0.1", "port": 8766, "error": None}


class FakeWorker(QObject):
    snapshot = pyqtSignal(dict)
    connection = pyqtSignal(str)
    failed = pyqtSignal(str)
    stopped_all = pyqtSignal()
    result = pyqtSignal(dict)
    def __init__(self):
        super().__init__()
        self.requests = []
        self.stopped = False
    def request(self, *args):
        self.requests.append(args)
    def stop(self):
        self.stopped = True
    def isRunning(self):
        return True


def test_inbox_selection_search_and_plain_text(qtbot, monkeypatch):
    monkeypatch.setattr("pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable", lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    window.show()
    worker.snapshot.emit({"items": [ITEM], "web": WEB, "instance_id": "first"})
    assert window.model.rowCount() == 1
    assert "Hello <b>world</b>" in window.detail.toPlainText()
    assert "Sanitized" in window.list.currentIndex().data()
    qtbot.keyClicks(window.search, "note")
    qtbot.waitUntil(lambda: ("search", "note") in worker.requests)
    updated = dict(ITEM, body="Updated once")
    worker.snapshot.emit({"items": [updated], "web": WEB, "instance_id": "restarted"})
    assert "Updated once" in window.detail.toPlainText()
    assert window.list.currentIndex().data(Qt.ItemDataRole.UserRole)["id"] == 1
    window.exit_desktop()
    assert worker.stopped
    assert not any(request[0] == "shutdown" for request in worker.requests)


def test_startup_error_off_gui_thread(qtbot, tmp_path, monkeypatch):
    gui_thread = threading.get_ident()
    calls = []
    def fail(*args, **kwargs):
        calls.append(threading.get_ident())
        raise RuntimeError("Port conflict: choose PBDESK_CORE_PORT")
    monkeypatch.setattr("pbdesk.desktop.worker.ensure_core", fail)
    worker = CoreWorker(Settings(tmp_path))
    with qtbot.waitSignal(worker.failed, timeout=2000) as result:
        worker.start()
    worker.wait(2000)
    assert calls and calls[0] != gui_thread
    assert "Port conflict" in result.args[0]


def test_empty_model():
    model = InboxModel()
    assert model.rowCount() == 0
    assert model.data(model.index(0)) is None


def test_worker_authenticated_snapshot_and_stop(qtbot, tmp_path, monkeypatch):
    import httpx
    from pbdesk.credentials import health_response
    settings = Settings(tmp_path)
    monkeypatch.setattr("pbdesk.desktop.worker.ensure_core", lambda *args, **kwargs: None)
    actual = httpx.Client
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path.endswith("health"):
            assert "Authorization" not in request.headers
            return httpx.Response(200, json=health_response(settings, request.headers, "11111111-1111-1111-1111-111111111111", True))
        assert request.headers["Authorization"].startswith("Bearer ")
        if request.url.path.endswith("items"):
            return httpx.Response(200, json={"items": [ITEM]})
        if request.method == "POST":
            assert request.extensions["timeout"]["read"] >= 20
        return httpx.Response(200, json=WEB)
    monkeypatch.setattr("pbdesk.desktop.worker.httpx.Client", lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    try:
        with qtbot.waitSignal(worker.snapshot, timeout=2000) as data:
            worker.start()
        assert data.args[0]["items"][0]["id"] == 1
        assert seen[0].url.path == "/api/v1/health"
        with qtbot.waitSignal(worker.snapshot, timeout=2000):
            worker.request("web", True)
        assert any(request.method == "POST" for request in seen)
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_stop_all_does_not_respawn_from_another_desktop(qtbot, tmp_path, monkeypatch):
    import httpx
    from pbdesk.credentials import health_response
    settings = Settings(tmp_path)
    starts = []
    online = threading.Event()
    online.set()
    monkeypatch.setattr("pbdesk.desktop.worker.ensure_core", lambda *args, **kwargs: starts.append(threading.get_ident()))
    actual = httpx.Client
    def handle(request):
        if not online.is_set():
            raise httpx.ConnectError("core stopped", request=request)
        if request.url.path.endswith("health"):
            return httpx.Response(200, json=health_response(settings, request.headers, "11111111-1111-1111-1111-111111111111", True))
        if request.url.path.endswith("shutdown"):
            online.clear()
            return httpx.Response(200, json={"stopping": True})
        if request.url.path.endswith("items"):
            return httpx.Response(200, json={"items": [ITEM]})
        return httpx.Response(200, json=WEB)
    monkeypatch.setattr("pbdesk.desktop.worker.httpx.Client", lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    first, second = CoreWorker(settings), CoreWorker(settings)
    snapshots, failures = [], []
    first.snapshot.connect(lambda data: snapshots.append("first"))
    second.snapshot.connect(lambda data: snapshots.append("second"))
    second.failed.connect(failures.append)
    try:
        first.start()
        second.start()
        qtbot.waitUntil(lambda: len(snapshots) >= 2)
        with qtbot.waitSignal(first.stopped_all, timeout=2000):
            first.request("shutdown")
        second.request("search", "")
        qtbot.waitUntil(lambda: bool(failures))
        assert len(starts) == 2  # Initial launches only; another client cannot undo Stop all.
        assert not online.is_set()
    finally:
        first.stop()
        second.stop()
        assert first.wait(3000)
        assert second.wait(3000)
