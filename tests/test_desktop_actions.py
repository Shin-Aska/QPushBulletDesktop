import json
import threading
import uuid

import httpx
import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from pbdesk.config import Settings
from pbdesk.credentials import health_response
from pbdesk.desktop.actions import ComposeDialog, ReviewDialog, PLAINTEXT_NOTICE
from pbdesk.desktop.app import MainWindow
from pbdesk.desktop.worker import CoreWorker

DEVICES = [
    {"id": "phone-a", "nickname": "Phone <b>A</b>", "type": "android", "manufacturer": "Example", "model": "One", "active": True, "can_push": True, "can_sms": True},
    {"id": "desktop-b", "nickname": "Desktop", "type": "windows", "manufacturer": None, "model": None, "active": True, "can_push": True, "can_sms": False},
    {"id": "inactive-c", "nickname": "Old phone", "type": "android", "manufacturer": None, "model": None, "active": False, "can_push": True, "can_sms": True},
]
CAPABILITIES = {"account_id": "fictional-account", "features": {"devices": True, "send": True, "send_sms": True, "sms_history": True, "device_management": True}, "provider_state": "connected", "encryption_state": "ready", "encrypted_pending": 0, "send_ready": True, "send_unavailable_reason": None}
WEB = {"enabled": False, "url": "http://127.0.0.1:8766", "host": "127.0.0.1", "port": 8766, "error": None}


def action(**kwargs):
    value = {"id": 7, "revision": 1, "fingerprint": "exact-fingerprint", "kind": "sms", "transport": "provider_plaintext", "device_id": "phone-a", "recipient": "+639171234567", "title": "", "body": "Literal <script>alert(1)</script>", "url": None, "state": "pending_approval", "created_at": "2026-11-14T00:00:00+00:00", "updated_at": "2026-11-14T00:00:00+00:00", "expires_at": "2026-11-14T01:00:00+00:00", "upstream_id": None, "error_code": None, "error_message": None, "requested_by": "mcp"}
    value.update(kwargs)
    return value


class FakeWorker(QObject):
    snapshot = pyqtSignal(dict)
    connection = pyqtSignal(str)
    failed = pyqtSignal(str)
    result = pyqtSignal(dict)
    stopped_all = pyqtSignal()
    def __init__(self):
        super().__init__()
        self.requests = []
    def request(self, *value):
        self.requests.append(value)
    def stop(self):
        pass
    def isRunning(self):
        return True


def snapshot(actions=None, capabilities=None):
    return {"items": [], "web": WEB, "capabilities": capabilities or CAPABILITIES, "devices": {"account_id": "fictional-account", "devices": DEVICES, "synced_at": "2026-11-14T00:00:00Z"}, "actions": actions or [], "instance_id": "first"}


def test_native_devices_filters_encryption_and_outbox_states(qtbot, monkeypatch):
    monkeypatch.setattr("pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable", lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    worker.snapshot.emit(snapshot([action(state="upstream_accepted", upstream_id="accepted-id")]))
    assert window.tabs.count() == 3
    assert window.device_model.rowCount() == 3
    window.device_filter.setCurrentIndex(1)
    assert window.device_model.rowCount() == 2
    window.device_filter.setCurrentIndex(2)
    assert [item["id"] for item in window.device_model.records] == ["phone-a"]
    window.device_filter.setCurrentIndex(3)
    assert [item["id"] for item in window.device_model.records] == ["inactive-c"]
    assert "Connectivity" in window.device_detail.toPlainText() and "Unknown" in window.device_detail.toPlainText()
    window.navigate(1)
    assert "delivery not confirmed" in window.action_model.index(0, 3).data()
    worker.snapshot.emit(snapshot([action(state="uncertain")], dict(CAPABILITIES, encryption_state="locked", encrypted_pending=2)))
    assert "locked" in window.provider_status.text() and "2 encrypted items waiting" in window.provider_status.text()
    assert "do not retry" in window.action_model.index(0, 3).data()
    assert "No automatic retry" in window.action_detail.toPlainText()
    assert "<script>" in window.action_detail.toPlainText()
    window.exit_desktop()


def test_compose_requires_explicit_sms_destination_and_never_infers_phone(qtbot):
    worker = FakeWorker()
    dialog = ComposeDialog(worker, DEVICES)
    qtbot.addWidget(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData("sms"))
    assert dialog.recipient.text() == ""
    assert dialog.device.count() == 2  # Placeholder plus only one active SMS-capable device.
    dialog.body.setPlainText("A message")
    dialog.device.setCurrentIndex(dialog.device.findData("phone-a"))
    assert not dialog.save_button.isEnabled()
    dialog.recipient.setText("Phone A")
    assert not dialog.save_button.isEnabled()
    dialog.recipient.setText("+639171234567")
    assert dialog.save_button.isEnabled()
    dialog.submit()
    command, request = worker.requests[0]
    assert command == "create_action"
    assert request["payload"]["transport"] == "provider_plaintext"
    assert request["payload"]["recipient"] == "+639171234567"
    assert request["payload"]["device_id"] == "phone-a"
    assert "allow_provider_plaintext" not in request["payload"]
    uuid.UUID(request["payload"]["client_request_id"])
    dialog.reject()


def test_compose_unknown_create_reconciles_exact_uuid_and_payload(qtbot):
    worker = FakeWorker()
    dialog = ComposeDialog(worker, DEVICES)
    qtbot.addWidget(dialog)
    dialog.device.setCurrentIndex(dialog.device.findData("desktop-b"))
    dialog.body.setPlainText("Original content")
    dialog.submit()
    original = worker.requests[0][1]
    worker.result.emit({"request_id": original["request_id"], "error": "Unknown result", "ambiguous": True})
    assert not dialog.body.isEnabled()
    assert dialog.save_button.text() == "Reconcile same request"
    dialog.submit()
    assert worker.requests[1] == worker.requests[0]
    assert worker.requests[1][1]["payload"]["expires_in_seconds"] == 3600
    assert all(command == "create_action" for command, value in worker.requests)
    dialog.reject()


def test_link_validation_and_edit_preserve_expiry_and_revision(qtbot):
    worker = FakeWorker()
    dialog = ComposeDialog(worker, DEVICES, action=action(kind="link", recipient=None, url="https://example.com"))
    qtbot.addWidget(dialog)
    dialog.url.setText("javascript:alert(1)")
    assert not dialog.save_button.isEnabled()
    dialog.url.setText("https://user:secret@example.com")
    assert not dialog.save_button.isEnabled()
    dialog.url.setText("https://example.com/new")
    dialog.submit()
    command, request = worker.requests[0]
    assert command == "edit_action"
    assert request["action_id"] == 7
    assert request["payload"]["expected_revision"] == 1
    assert "expires_in_seconds" not in request["payload"]
    assert "client_request_id" not in request["payload"]
    worker.result.emit({"request_id": request["request_id"], "error": "Changed", "status": 409, "ambiguous": False})
    assert not dialog.save_button.isEnabled()
    assert dialog.url.text() == "https://example.com/new"  # Preserve unsaved text on conflict.
    dialog.reject()


def test_review_exact_payload_unchecked_ack_and_matching_approval(qtbot):
    worker = FakeWorker()
    dialog = ReviewDialog(worker, action(), DEVICES, CAPABILITIES)
    qtbot.addWidget(dialog)
    assert not dialog.ack.isChecked()
    assert not dialog.approve_button.isEnabled()
    text = dialog.preview.toPlainText() + dialog.advanced.toPlainText()
    for expected in ("phone-a", "+639171234567", "exact-fingerprint", "Not end-to-end encrypted; sent over HTTPS to Pushbullet", "Requested by: mcp", "<script>alert(1)</script>"):
        assert expected in text
    assert "Pushbullet can process" in PLAINTEXT_NOTICE
    dialog.ack.setChecked(True)
    dialog.mutate("approve_action")
    command, request = worker.requests[0]
    assert command == "approve_action"
    assert request["payload"] == {"revision": 1, "fingerprint": "exact-fingerprint", "allow_provider_plaintext": True}
    assert not dialog.approve_button.isEnabled()
    worker.result.emit({"request_id": request["request_id"], "data": action(state="ready")})
    assert not dialog.ack.isChecked()
    assert not dialog.approve_button.isEnabled()
    dialog.reject()


def test_review_closes_on_send_acceptance_and_keeps_chat_open(qtbot, monkeypatch):
    monkeypatch.setattr("pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable", lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    window.show()
    worker.snapshot.emit(snapshot([action()]))
    window.navigate(1)
    window.review(action())
    dialog = window.dialogs[-1]
    closed = []
    dialog.accepted.connect(lambda: closed.append(True))
    dialog.ack.setChecked(True)
    dialog.approve_button.click()
    request = worker.requests[-1][1]
    worker.result.emit({"command": "approve_action", "request_id": request["request_id"],
                        "data": action(state="ready")})
    assert dialog.isVisible() and not closed
    worker.snapshot.emit(snapshot([action(state="submitting")]))
    assert dialog.isVisible() and not closed
    accepted = action(state="upstream_accepted", upstream_id="fictional-acceptance")
    worker.snapshot.emit(snapshot([accepted]))
    assert closed == [True]
    assert not window.dialogs
    assert window.isVisible() and window.tabs.currentIndex() == 1
    assert window.action_model.records == [accepted]
    assert len([request for request in worker.requests if request[0] == "approve_action"]) == 1
    window.exit_desktop()


def test_review_closes_when_approval_response_already_contains_acceptance(qtbot):
    worker = FakeWorker()
    dialog = ReviewDialog(worker, action(), DEVICES, CAPABILITIES)
    qtbot.addWidget(dialog)
    dialog.show()
    closed = []
    dialog.accepted.connect(lambda: closed.append(True))
    dialog.ack.setChecked(True)
    dialog.approve_button.click()
    request = worker.requests[-1][1]
    worker.result.emit({"request_id": request["request_id"],
                        "data": action(state="upstream_accepted")})
    assert closed == [True] and not dialog.isVisible()
    # Closing disconnects from later updates rather than accepting twice.
    worker.snapshot.emit(snapshot([action(state="upstream_accepted")]))
    assert closed == [True]


@pytest.mark.parametrize("state", ["failed", "uncertain"])
def test_review_stays_open_when_send_fails_or_is_uncertain(qtbot, state):
    worker = FakeWorker()
    dialog = ReviewDialog(worker, action(), DEVICES, CAPABILITIES)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.ack.setChecked(True)
    dialog.approve_button.click()
    request = worker.requests[-1][1]
    worker.result.emit({"request_id": request["request_id"], "data": action(state="ready")})
    worker.snapshot.emit(snapshot([action(state=state)]))
    assert dialog.isVisible() and dialog.action["state"] == state
    assert not dialog.approve_button.isEnabled()
    assert len(worker.requests) == 1
    dialog.reject()


def test_review_stays_open_on_approval_error(qtbot):
    worker = FakeWorker()
    dialog = ReviewDialog(worker, action(), DEVICES, CAPABILITIES)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.ack.setChecked(True)
    dialog.approve_button.click()
    request = worker.requests[-1][1]
    worker.result.emit({"request_id": request["request_id"], "error": "Result could not be confirmed",
                        "ambiguous": True})
    assert dialog.isVisible() and dialog.awaiting_refresh
    assert "could not be confirmed" in dialog.feedback.text()
    assert len(worker.requests) == 1
    dialog.reject()


def test_review_of_previously_accepted_action_stays_open_for_inspection(qtbot):
    worker = FakeWorker()
    dialog = ReviewDialog(worker, action(state="upstream_accepted"), DEVICES, CAPABILITIES)
    qtbot.addWidget(dialog)
    dialog.show()
    worker.snapshot.emit(snapshot([action(state="upstream_accepted")]))
    assert dialog.isVisible()
    assert "Accepted by Pushbullet" in dialog.preview.toPlainText()
    assert not worker.requests
    dialog.reject()


def test_review_does_not_close_for_acceptance_of_another_revision(qtbot):
    worker = FakeWorker()
    dialog = ReviewDialog(worker, action(), DEVICES, CAPABILITIES)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.ack.setChecked(True)
    dialog.approve_button.click()
    request = worker.requests[-1][1]
    worker.result.emit({"request_id": request["request_id"], "data": action(state="ready")})
    worker.snapshot.emit(snapshot([action(state="upstream_accepted", revision=2, fingerprint="new-version")]))
    assert dialog.isVisible()
    assert len(worker.requests) == 1
    dialog.reject()


def test_review_changes_clear_ack_and_readiness_controls_sending(qtbot):
    worker = FakeWorker()
    dialog = ReviewDialog(worker, action(), DEVICES, CAPABILITIES)
    qtbot.addWidget(dialog)
    dialog.ack.setChecked(True)
    changed = action(revision=2, fingerprint="new", body="Changed content")
    worker.snapshot.emit(snapshot([changed]))
    assert not dialog.ack.isChecked()
    assert "Changed content" in dialog.preview.toPlainText()
    assert "revision 2" in dialog.advanced.toPlainText()
    dialog.ack.setChecked(True)
    worker.snapshot.emit(snapshot([changed], dict(CAPABILITIES, send_ready=False, send_unavailable_reason="Provider disconnected")))
    assert not dialog.approve_button.isEnabled()
    assert "Provider disconnected" in dialog.readiness.text()
    assert not worker.requests
    dialog.reject()


def test_worker_mutations_off_gui_thread_timeout_and_rejection_are_not_disconnect(qtbot, tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual = httpx.Client
    gui_thread = threading.get_ident()
    calls, requests, statuses, results = [], [], [], []
    monkeypatch.setattr("pbdesk.desktop.worker.ensure_core", lambda *args, **kwargs: None)
    def handle(request):
        calls.append(threading.get_ident())
        requests.append(request)
        if request.url.path.endswith("health"):
            assert "Authorization" not in request.headers
            return httpx.Response(200, json=health_response(settings, request.headers, "11111111-1111-1111-1111-111111111111", True))
        assert request.headers["Authorization"].startswith("Bearer ")
        if request.method == "POST":
            assert request.extensions["timeout"]["read"] >= 20
            assert requests[-2].url.path.endswith("health")
            if request.url.path.endswith("/actions"):
                raise httpx.ReadTimeout("result unknown", request=request)
            return httpx.Response(409, json={"detail": "Stale revision"})
        if request.url.path.endswith("items"):
            body = {"account_id": "fictional-account", "items": []}
        elif request.url.path.endswith("capabilities"):
            body = CAPABILITIES
        elif request.url.path.endswith("devices"):
            body = {"account_id": "fictional-account", "devices": DEVICES, "synced_at": None}
        elif request.url.path.endswith("actions"):
            body = {"account_id": "fictional-account", "actions": []}
        else:
            body = WEB
        return httpx.Response(200, json=body)
    monkeypatch.setattr("pbdesk.desktop.worker.httpx.Client", lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    worker.connection.connect(statuses.append)
    worker.result.connect(results.append)
    try:
        with qtbot.waitSignal(worker.snapshot, timeout=2000):
            worker.start()
        worker.request("create_action", {"request_id": "stable-uuid", "payload": {"client_request_id": "stable-uuid"}})
        qtbot.waitUntil(lambda: len(results) == 1)
        assert results[0]["ambiguous"] is True
        worker.request("approve_action", {"request_id": "approval", "action_id": 7, "payload": {"revision": 1, "fingerprint": "old", "allow_provider_plaintext": True}})
        qtbot.waitUntil(lambda: len(results) == 2)
        assert results[1]["status"] == 409
        assert all("Disconnected" not in status for status in statuses)
        assert len([request for request in requests if request.method == "POST"]) == 2
        assert all(call != gui_thread for call in calls)
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_poll_preserves_open_compose_text_and_stale_review_requires_new_consent(qtbot):
    worker = FakeWorker()
    compose = ComposeDialog(worker, DEVICES)
    qtbot.addWidget(compose)
    compose.device.setCurrentIndex(compose.device.findData("phone-a"))
    compose.body.setPlainText("Unsaved local text")
    worker.snapshot.emit(snapshot([action(body="Unrelated arriving content")]))
    assert compose.body.toPlainText() == "Unsaved local text"
    compose.reject()
    review = ReviewDialog(worker, action(), DEVICES, CAPABILITIES)
    qtbot.addWidget(review)
    review.ack.setChecked(True)
    review.mutate("approve_action")
    request = worker.requests[-1][1]
    worker.result.emit({"request_id": request["request_id"], "status": 409, "ambiguous": False, "error": "Revision changed"})
    assert not review.ack.isChecked()
    assert not review.approve_button.isEnabled()
    assert not review.cancel_button.isEnabled()
    worker.snapshot.emit(snapshot([action(revision=2, fingerprint="new-version")]))
    assert review.cancel_button.isEnabled()
    assert not review.approve_button.isEnabled()
    assert len(worker.requests) == 1  # A conflict never submits another approval.
    review.mutate("cancel_action")
    assert worker.requests[-1][0] == "cancel_action"
    assert worker.requests[-1][1]["payload"] == {"revision": 2}
    review.reject()


def sms_snapshot(device_id, messages, **sync):
    return {'sms_device_id': device_id, 'sms': {'account_id': 'fictional-account', 'messages': messages, 'sync': dict(state='ready', last_success_at=1794614400.0, **sync)}}


def sms_message(identity, device='phone-a', direction='incoming', body='Stored <b>text</b>', conversation='thread-a'):
    return {'id': identity, 'account_id': 'fictional-account', 'device_id': device, 'conversation_id': conversation, 'message_id': identity, 'direction': direction, 'body': body, 'timestamp': 1794614400.0 + identity, 'addresses': ['+12025550123']}


def timeline_text(page):
    from PyQt6.QtWidgets import QLabel, QTextEdit
    return "\n".join(widget.text() if isinstance(widget, QLabel) else widget.toPlainText()
        for widget in page.timeline_content.findChildren((QLabel, QTextEdit)))


def select_phone(window, identity):
    page = window.sms_page
    page.phone.setCurrentIndex(page.phone.findData(identity))


def test_sms_cached_switch_stale_replies_and_activity(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    data = snapshot([action()])
    data['devices']['devices'] = DEVICES + [dict(DEVICES[0], id='other-phone')]
    worker.snapshot.emit(data)
    select_phone(window, 'phone-a')
    page = window.sms_page
    worker.snapshot.emit(sms_snapshot('phone-a', [sms_message(1), sms_message(2, direction='outgoing', body='My sent reply')]))
    assert len([key for key in page._threads if key[0] == 'history']) == 1
    page._activate_thread(('history', 'thread-a'))
    assert 'Stored <b>text</b>' in timeline_text(page)
    assert 'My sent reply' in timeline_text(page)
    assert window.action_model.rowCount() == 1
    select_phone(window, 'other-phone')
    worker.snapshot.emit(sms_snapshot('phone-a', [sms_message(3, body='Stale reply for A')]))
    assert not page._threads
    select_phone(window, 'phone-a')
    page._activate_thread(('history', 'thread-a'))
    assert 'Stale reply for A' in timeline_text(page)
    worker.requests.clear()
    window.select_device('desktop-b')
    window.search.setText('notification search')
    window.search_changed()
    assert page.selected_device_id == 'phone-a'
    assert not any(command == 'select_device' for command, *_ in worker.requests)
    assert not any(command in {'create_action', 'approve_action'} for command, *_ in worker.requests)
    assert window.actions[0]['device_id'] == 'phone-a'
    window.exit_desktop()


def test_sms_refresh_preserves_selection_draft_and_errors(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    window.show()
    worker.snapshot.emit(snapshot())
    select_phone(window, 'phone-a')
    page = window.sms_page
    messages = [sms_message(i, conversation='thread-' + str(i), body='Long message\n' * 100) for i in range(1, 20)]
    worker.snapshot.emit(sms_snapshot('phone-a', messages))
    page._activate_thread(('history', 'thread-8'))
    page.composer.setPlainText('Unsaved conversation reply')
    selected = page.current_thread
    worker.snapshot.emit(sms_snapshot('phone-a', messages + [sms_message(30, conversation='new')], error='Connection interrupted'))
    assert page.current_thread == selected
    assert page.composer.toPlainText() == 'Unsaved conversation reply'
    assert 'refresh unavailable' in page.sync_status.text()
    assert page.sms_cache['phone-a']['sync']['error'] == 'Connection interrupted'
    assert 'Long message' in timeline_text(page)
    changed = snapshot()
    changed['devices']['devices'] = [DEVICES[1]]
    worker.snapshot.emit(changed)
    assert page.selected_device_id == 'phone-a'
    assert 'history only' in page.phone.currentText()
    assert len(page._threads) == 20
    window.exit_desktop()


def test_device_mutation_route_and_sanitized_error_are_distinct_from_sending(tmp_path):
    settings = Settings(tmp_path)
    worker = CoreWorker(settings)
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={'devices': []})
    with httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(handle)) as client:
        worker._mutate(client, {}, 'remove_device', {'device_id': 'exact/device'})
        worker._mutate(client, {}, 'encryption', {'device_id': 'phone-a', 'mode': 'device', 'entry': 'phone-key', 'password': 'fictional-test-secret'})
    assert seen[0].method == 'DELETE'
    assert seen[0].url.raw_path == b'/api/v1/devices/exact%2Fdevice'
    assert json.loads(seen[1].content)['entry'] == 'phone-key'
    message, ambiguous, _ = worker._mutation_error(httpx.ReadTimeout('fictional-test-secret'), 'encryption')
    assert ambiguous and 'secret' not in message and 'Outbox' not in message


def test_review_removed_device_never_approves_another_destination(qtbot):
    worker = FakeWorker()
    dialog = ReviewDialog(worker, action(), [DEVICES[1]], CAPABILITIES)
    qtbot.addWidget(dialog)
    dialog.ack.setChecked(True)
    assert not dialog.approve_button.isEnabled()
    dialog.mutate('approve_action')
    assert not worker.requests
    assert 'original destination is unavailable' in dialog.readiness.text()
    dialog.reject()


def test_old_core_feature_gate_preserves_sms_cache_without_new_routes(qtbot, tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual = httpx.Client
    seen = []
    monkeypatch.setattr('pbdesk.desktop.worker.ensure_core', lambda *args, **kwargs: None)
    old_cap = dict(CAPABILITIES, features={'devices': True, 'send': True})
    def handle(request):
        seen.append(request)
        if request.url.path.endswith('health'):
            return httpx.Response(200, json=health_response(settings, request.headers, '11111111-1111-1111-1111-111111111111', True))
        if request.url.path.endswith('capabilities'):
            body = old_cap
        elif request.url.path.endswith('devices'):
            body = {'account_id': 'fictional-account', 'devices': DEVICES}
        elif request.url.path.endswith('items'):
            body = {'account_id': 'fictional-account', 'items': []}
        elif request.url.path.endswith('actions'):
            body = {'account_id': 'fictional-account', 'actions': []}
        else:
            body = WEB
        return httpx.Response(200, json=body)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    results, cached = [], []
    worker.result.connect(results.append)
    worker.snapshot.connect(cached.append)
    worker.account_id, worker.account_known = 'fictional-account', True
    worker.sms_cache['phone-a'] = {'messages': [sms_message(1)], 'sync': {'state': 'ready'}}
    try:
        with qtbot.waitSignal(worker.snapshot, timeout=2000):
            worker.start()
        worker.request('select_device', {'device_id': 'phone-a', 'request_id': 'device-generation'})
        worker.request('refresh_sms', {'device_id': 'phone-a', 'request_id': 'refresh'})
        qtbot.waitUntil(lambda: bool(results))
        assert 'restart the shared core' in results[0]['error']
        assert not any(request.url.path.startswith('/api/v1/sms') for request in seen)
        assert not any(request.method != 'GET' for request in seen)
        assert any(item.get('sms_request_id') == 'device-generation' and (item.get('sms') or {}).get('messages') for item in cached)
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_uncertain_registration_never_invites_duplicate_add(qtbot):
    from pbdesk.desktop.devices import AddDeviceDialog
    worker = FakeWorker()
    dialog = AddDeviceDialog(worker)
    qtbot.addWidget(dialog)
    dialog.nickname.setText('Fictional workstation')
    dialog.submit()
    request_id = dialog.request_id
    response = httpx.Response(409, json={'detail': {'state': 'uncertain'}}, request=httpx.Request('POST', 'http://127.0.0.1/api/v1/devices'))
    error = httpx.HTTPStatusError('unknown outcome', request=response.request, response=response)
    message, ambiguous, status = CoreWorker._mutation_error(error, 'add_device')
    worker.result.emit({'command': 'add_device', 'request_id': request_id, 'error': message, 'ambiguous': ambiguous, 'status': status})
    assert not dialog.register.isEnabled()
    assert not dialog.nickname.isEnabled()
    assert 'Review uncertain registration' in dialog.feedback.text()
    assert len(worker.requests) == 1
    dialog.reject()


def test_saved_sms_edit_preserves_current_chat_and_draft(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    data = snapshot()
    data['devices']['devices'] = DEVICES + [dict(DEVICES[0], id='phone-b', nickname='Other fictional phone')]
    worker.snapshot.emit(data)
    select_phone(window, 'phone-a')
    page = window.sms_page
    page.start_new()
    page.recipient.setText('+12025550123')
    page.composer.setPlainText('Unsaved current chat draft')
    context = page.current_thread
    window.review = lambda action: None
    worker.requests.clear()
    saved = action(device_id='phone-b')
    window.action_saved(saved)
    assert page.selected_device_id == 'phone-a' and page.current_thread == context
    assert page.composer.toPlainText() == 'Unsaved current chat draft'
    assert window.actions[0]['device_id'] == 'phone-b'
    assert not worker.requests
    window.exit_desktop()


def test_notification_search_is_independent_of_sms_navigation(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    worker.snapshot.emit(snapshot())
    window.search.setText('first push search')
    window.search_changed()
    window.navigate(1)
    window.search.setText('second visible search')
    window.search_changed()
    worker.requests.clear()
    window.navigate(0)
    window.refresh_current()
    assert ('search', 'second visible search') in worker.requests
    window.exit_desktop()


def test_account_boundary_clears_ephemeral_cache_and_rejects_stale_sms(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    data = snapshot()
    data['capabilities'] = dict(CAPABILITIES, account_id='account-a')
    data['devices']['account_id'] = 'account-a'
    worker.snapshot.emit(data)
    window.sms_page.phone.setCurrentIndex(window.sms_page.phone.findData('phone-a'))
    window.navigate(1)
    messages = sms_snapshot('phone-a', [dict(sms_message(1), account_id='account-a')])
    messages['sms']['account_id'] = 'account-a'
    worker.snapshot.emit(messages)
    assert window.sms_page.threads.count() == 1
    changed = snapshot()
    changed['capabilities'] = dict(CAPABILITIES, account_id='account-b')
    changed['devices'] = {'account_id': 'account-b', 'devices': []}
    worker.snapshot.emit(changed)
    assert window.sms_page.threads.count() == 0 and not window.sms_page.sms_cache
    worker.snapshot.emit(messages)
    assert window.sms_page.threads.count() == 0 and not window.sms_page.sms_cache
    # Select the new account's own phone; no context crosses the account boundary.
    changed['devices']['devices'] = DEVICES
    worker.snapshot.emit(changed)
    select_phone(window, 'phone-a')
    # A failed key lookup for the same account must not erase readable messages.
    current = sms_snapshot('phone-a', [dict(sms_message(2), account_id='account-b')])
    current['sms']['account_id'] = 'account-b'
    worker.snapshot.emit(current)
    failed = sms_snapshot('phone-a', [], error='Local encryption key unavailable')
    failed['sms']['account_id'] = 'account-b'
    worker.snapshot.emit(failed)
    assert window.sms_page.threads.count() == 1
    assert 'Stored <b>text</b>' in timeline_text(window.sms_page)
    window.exit_desktop()


def test_unrepresentable_retained_timestamps_do_not_crash_reading():
    from pbdesk.desktop.app import stamp
    assert stamp(1e300) == 'Unknown time'
    assert stamp(float('nan')) == 'Unknown time'


def test_worker_discards_mixed_account_aggregate_before_ui_emission(qtbot, tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual = httpx.Client
    account = ['account-a']
    seen_items, published = [], []
    monkeypatch.setattr('pbdesk.desktop.worker.ensure_core', lambda *args, **kwargs: None)
    def handle(request):
        if request.url.path.endswith('health'):
            return httpx.Response(200, json=health_response(settings, request.headers, '11111111-1111-1111-1111-111111111111', True))
        if request.url.path.endswith('capabilities'):
            body = dict(CAPABILITIES, account_id=account[0])
        elif request.url.path.endswith('items'):
            seen_items.append(account[0])
            body = {'account_id': account[0], 'items': [{'id': account[0], 'body': 'private-' + account[0]}]}
        elif request.url.path.endswith('devices'):
            account[0] = 'account-b'  # Reload races with the first aggregate.
            body = {'account_id': account[0], 'devices': []}
        elif request.url.path.endswith('actions'):
            body = {'account_id': account[0], 'actions': []}
        else:
            body = WEB
        return httpx.Response(200, json=body)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    worker.snapshot.connect(published.append)
    try:
        worker.start()
        qtbot.waitUntil(lambda: any(item.get('items') for item in published), timeout=3000)
        assert seen_items[:2] == ['account-a', 'account-b']
        assert all(item['capabilities']['account_id'] == 'account-b' for item in published)
        assert all(row['id'] == 'account-b' for item in published for row in item['items'])
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_wrong_key_device_state_is_truthful_and_retains_readable_sms(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    worker.snapshot.emit(snapshot())
    window.sms_page.phone.setCurrentIndex(window.sms_page.phone.findData('phone-a'))
    window.navigate(1)
    worker.snapshot.emit(sms_snapshot('phone-a', [sms_message(1)]))
    wrong_key = snapshot()
    wrong_key['devices']['devices'] = [dict(DEVICES[0], encryption={'mode': 'shared', 'entry': 'e2e-password', 'state': 'locked'})]
    worker.snapshot.emit(wrong_key)
    worker.snapshot.emit(sms_snapshot('phone-a', [], error='Encrypted history could not be authenticated'))
    assert 'Key unavailable or could not decrypt' in window.device_detail.toPlainText()
    assert 'KWallet locked' not in window.device_detail.toPlainText()
    assert window.sms_page.threads.count() == 1
    assert 'Stored <b>text</b>' in timeline_text(window.sms_page)
    assert not any(command in {'create_action', 'approve_action'} for command, *_ in worker.requests)
    window.exit_desktop()


def test_shell_navigation_activity_all_actions_and_snapshot_once(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    assert [button.text() for button in window.navigation] == ['Notifications', 'SMS', 'Devices']
    originals = []
    apply = window.sms_page.apply_snapshot
    def observe(data):
        originals.append(data)
        apply(data)
    window.sms_page.apply_snapshot = observe
    records = [action(), action(id=8, kind='note', device_id='desktop-b', recipient=None, state='uncertain'), action(id=9, kind='link', device_id='desktop-b', recipient=None, state='failed')]
    data = snapshot(records)
    window.apply_snapshot(data)
    assert originals == [data] and originals[0] is data
    window.show_activity()
    assert window.activity.isVisible()
    assert {row['id'] for row in window.action_model.records} == {7, 8, 9}
    window.actions_view.setCurrentIndex(window.action_model.index(1, 0))
    window.select_device('phone-a')
    window.navigate(1)
    assert window.windowTitle() == 'pbdesk — SMS'
    assert window.compose_button.isHidden()
    assert {row['id'] for row in window.action_model.records} == {7, 8, 9}
    worker.snapshot.emit(snapshot([records[0], dict(records[1], state='failed'), records[2]]))
    assert window.actions_view.currentIndex().data(Qt.ItemDataRole.UserRole)['id'] == 8
    assert 'Failed' in window.action_detail.toPlainText()
    window.activity.review_selected()
    assert isinstance(window.dialogs[-1], ReviewDialog)
    assert not window.dialogs[-1].ack.isChecked()
    window.exit_desktop()


def test_notifications_compose_only_note_link_and_sms_shortcut_inline(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    worker.snapshot.emit(snapshot())
    window.compose()
    dialog = window.dialogs[-1]
    assert [dialog.kind.itemData(i) for i in range(dialog.kind.count())] == ['note', 'link']
    assert dialog.account_id == 'fictional-account'
    dialog.reject()
    window.navigate(1)
    window.compose()
    assert window.sms_page.current_thread[0] == 'new'
    assert not window.dialogs
    window.exit_desktop()


def test_worker_checks_fresh_account_before_queued_create_and_tags_result(qtbot, tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual = httpx.Client
    account = ['account-a']
    seen, results = [], []
    monkeypatch.setattr('pbdesk.desktop.worker.ensure_core', lambda *args, **kwargs: None)
    def handle(request):
        seen.append(request)
        path = request.url.path
        if path.endswith('health'):
            return httpx.Response(200, json=health_response(settings, request.headers, '11111111-1111-1111-1111-111111111111', True))
        if path.endswith('capabilities'):
            value = dict(CAPABILITIES, account_id=account[0])
        elif path.endswith('items'):
            value = {'account_id': account[0], 'items': []}
        elif path.endswith('devices'):
            value = {'account_id': account[0], 'devices': DEVICES}
        elif path.endswith('actions'):
            value = {'account_id': account[0], 'actions': []}
        else:
            value = WEB
        return httpx.Response(200, json=value)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    worker = CoreWorker(settings)
    worker.result.connect(results.append)
    try:
        with qtbot.waitSignal(worker.snapshot, timeout=2000):
            worker.start()
        assert worker.account_id == 'account-a'
        account[0] = 'account-b'  # Reload inside the same signed core instance.
        worker.request('create_action', {'account_id': 'account-a', 'request_id': 'stable-origin', 'payload': {'client_request_id': 'stable-origin'}})
        qtbot.waitUntil(lambda: bool(results))
        assert results[0]['account_id'] == 'account-a'
        assert results[0]['status'] == 409 and not results[0]['ambiguous']
        assert 'account changed' in results[0]['error']
        assert not any(request.method == 'POST' for request in seen)
        assert worker.account_id == 'account-b'
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_worker_action_origin_is_metadata_not_action_payload(tmp_path):
    worker = CoreWorker(Settings(tmp_path))
    seen = []
    payload = {'kind': 'sms', 'body': 'Fictional exact content'}
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={'account_id': 'account-a'} if request.method == 'GET' else action())
    with httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(handle)) as client:
        worker._mutate(client, {}, 'create_action', {'account_id': 'account-a', 'request_id': 'origin', 'payload': payload})
    assert [request.method for request in seen] == ['GET', 'POST']
    assert json.loads(seen[-1].content) == payload


def test_delayed_sms_save_updates_activity_without_review_on_other_page(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    worker.snapshot.emit(snapshot())
    window.navigate(1)
    select_phone(window, 'phone-a')
    page = window.sms_page
    page.start_new()
    page.recipient.setText('+12025550123')
    page.composer.setPlainText('Original inline content')
    page.submit()
    command, value = worker.requests[-1]
    assert command == 'create_action'
    window.navigate(0)
    worker.result.emit({'command': 'create_action', 'request_id': value['request_id'], 'account_id': 'fictional-account', 'data': action(body='Original inline content', recipient='+12025550123')})
    assert window.tabs.currentIndex() == 0
    assert window.action_model.records[0]['body'] == 'Original inline content'
    assert not window.dialogs
    window.exit_desktop()


def test_deferred_review_edit_cannot_open_previous_account_content(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    worker.snapshot.emit(snapshot([action()]))
    window.review(action())
    review = window.dialogs[-1]
    review.edit()
    next_account = snapshot()
    next_account['capabilities'] = dict(CAPABILITIES, account_id='next-account')
    next_account['devices']['account_id'] = 'next-account'
    worker.snapshot.emit(next_account)
    qtbot.wait(1)
    assert not window.dialogs
    window.exit_desktop()


def test_deferred_saved_action_review_stops_when_window_closes(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    worker.snapshot.emit(snapshot())
    window.action_saved(action())
    window.exit_desktop()
    qtbot.wait(1)
    assert not window.dialogs
