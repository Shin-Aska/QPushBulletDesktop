"""Fake-worker checks: no network, provider account, SMS, or persistent database."""
from copy import deepcopy

import pytest

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel

from pbdesk.desktop.activity import ActivityDialog
from pbdesk.desktop.chat import MessageText, SmsPage


class FakeWorker(QObject):
    result = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.requests = []

    def request(self, command, value=None):
        self.requests.append((command, deepcopy(value)))


PHONES = [
    {"id": "a", "nickname": "Phone A", "active": True, "can_sms": True},
    {"id": "b", "nickname": "Phone B", "active": True, "can_sms": True},
    {"id": "removed", "nickname": "Old phone", "active": False, "can_sms": False,
     "history": {"count": 3}},
    {"id": "desktop", "nickname": "Desktop", "active": True, "can_sms": False},
]


def message(number=1, conversation="one", device="a", account="acct", **changes):
    value = {"id": number, "account_id": account, "device_id": device,
             "conversation_id": conversation, "message_id": str(number),
             "direction": "incoming", "body": f"Message {number}",
             "timestamp": 1780000000 + number * 60,
             "addresses": ["+639171234567"], "name": "Friend", "decrypted": True}
    value.update(changes)
    return value


def snapshot(messages=None, device="a", account="acct", actions=None):
    return {"capabilities": {"account_id": account, "send_ready": True,
        "features": {"send": True, "send_sms": True, "sms_history": True}},
        "devices": {"account_id": account, "devices": deepcopy(PHONES)},
        "actions": deepcopy(actions or []), "sms_device_id": device,
        "sms": {"account_id": account, "messages": messages or [],
                "sync": {"state": "ready", "last_success_at": 1780000000}}}


def page(qtbot, messages=None):
    worker = FakeWorker()
    widget = SmsPage(worker)
    qtbot.addWidget(widget)
    widget.resize(1100, 740)
    widget.show()
    widget.apply_snapshot(snapshot(messages if messages is not None else [message()]))
    qtbot.wait(50)
    return widget, worker


def create_request(widget, worker, body="Hello"):
    widget.composer.setPlainText(body)
    widget.submit()
    command, value = worker.requests[-1]
    assert command == "create_action"
    return value


def successful_action(request, **changes):
    value = dict(request["payload"], id=71, revision=1, state="pending_approval",
                 fingerprint="f" * 64, account_id=request["account_id"],
                 created_at="2026-11-14T00:00:00Z", updated_at="2026-11-14T00:00:00Z",
                 expires_at="2026-11-14T01:00:00Z")
    value.update(changes)
    return value


def succeed(worker, request, action=None):
    worker.result.emit({"command": "create_action", "request_id": request["request_id"],
                        "account_id": request["account_id"],
                        "data": action or successful_action(request)})


def test_native_bubbles_literal_text_alignment_dates_and_bounded_wrap(qtbot):
    literal = '<img src="https://example.invalid/image" onerror="boom">\n' + "x" * 1200
    widget, worker = page(qtbot, [message(body=literal),
        message(2, direction="outgoing", timestamp=1780100000)])
    incoming = widget.findChild(QFrame, "smsIncoming")
    outgoing = widget.findChild(QFrame, "smsOutgoing")
    assert incoming.property("direction") == "incoming"
    assert outgoing.property("direction") == "outgoing"
    assert incoming.parentWidget().layout().itemAt(0).widget() is incoming
    assert outgoing.parentWidget().layout().itemAt(1).widget() is outgoing
    text = incoming.findChild(MessageText)
    assert text.toPlainText() == literal
    assert text.isReadOnly() and not text.acceptRichText()
    assert incoming.width() <= 560 and text.width() <= 560
    dates = widget.findChildren(QLabel, "smsDate")
    assert len(dates) == 2
    assert any("You · phone SMS" in label.text() for label in outgoing.findChildren(QLabel))
    assert not any(command in {"approve_action", "create_action"} for command, _ in worker.requests)


def test_reply_routing_group_conflicting_invalid_and_inactive_are_blocked(qtbot):
    widget, worker = page(qtbot, [message(addresses=["+639171234567", "+12025550123"])])
    widget.composer.setPlainText("No group inference")
    assert not widget.send_button.isEnabled()
    widget.submit()
    assert not any(command == "create_action" for command, _ in worker.requests)
    widget.apply_snapshot(snapshot([message(), message(2, addresses=["+12025550123"])]))
    assert not widget.send_button.isEnabled()
    widget.apply_snapshot(snapshot([message(addresses=["Friend"])]))
    assert not widget.send_button.isEnabled()
    assert widget.phone.findData("desktop") < 0
    assert widget.phone.findData("removed") >= 0
    widget.phone.setCurrentIndex(widget.phone.findData("removed"))
    widget.start_new()
    widget.recipient.setText("+12025550123")
    widget.composer.setPlainText("Old device")
    assert "history only" in widget.validation_message()
    assert not widget.send_button.isEnabled()


def test_new_message_explicit_e164_drafts_preserved_per_recipient_and_thread(qtbot):
    widget, worker = page(qtbot)
    widget.composer.setPlainText("Reply draft")
    widget.start_new()
    widget.recipient.setText("Friend")
    widget.composer.setPlainText("Invalid draft")
    assert not widget.send_button.isEnabled()
    widget.recipient.setText("+12025550123")
    widget.composer.setPlainText("First new draft")
    assert widget.send_button.isEnabled()
    widget.recipient.setText("+12025550124")
    assert widget.composer.toPlainText() == ""
    widget.composer.setPlainText("Second new draft")
    widget.recipient.setText("+12025550123")
    assert widget.composer.toPlainText() == "First new draft"
    widget._activate_thread(("history", "one"))
    assert widget.composer.toPlainText() == "Reply draft"
    widget.apply_snapshot(snapshot([message(), message(2)]))
    assert widget.composer.toPlainText() == "Reply draft"
    widget.start_new()
    assert widget.recipient.text() == "+12025550123"
    assert widget.composer.toPlainText() == "First new draft"


def test_pending_create_uses_review_pipeline_no_approval_and_separate_local_card(qtbot):
    widget, worker = page(qtbot)
    reviewed, created = [], []
    widget.review_requested.connect(reviewed.append)
    widget.action_created.connect(created.append)
    request = create_request(widget, worker, "Literal <script>text</script>")
    assert request["account_id"] == "acct"
    assert request["payload"]["client_request_id"] == request["request_id"]
    assert "allow_provider_plaintext" not in request["payload"]
    assert not widget.composer.isEnabled()
    succeed(worker, request)
    assert len(reviewed) == len(created) == 1
    assert reviewed[0]["state"] == "pending_approval"
    assert widget.composer.toPlainText() == ""
    assert widget.findChild(QFrame, "smsLocal")
    assert len(widget._threads[("history", "one")]["messages"]) == 1
    assert not any(command == "approve_action" for command, _ in worker.requests)


def test_accepted_request_disappears_from_chat_but_remains_in_activity(qtbot):
    widget, worker = page(qtbot)
    request = create_request(widget, worker, "A fictional reply")
    succeed(worker, request)
    assert widget.findChild(QFrame, "smsLocal")
    actions = [successful_action(request, state="upstream_accepted",
        upstream_id="fictional-acceptance")]
    # A second accepted request has no matching history. Visibility depends on
    # acceptance state alone, never a guessed body/time delivery correlation.
    actions.append(dict(actions[0], id=72, body="Another fictional reply"))
    history = [message(), message(2, direction="outgoing", body="A fictional reply")]
    requests = deepcopy(worker.requests)
    widget.apply_snapshot(snapshot(history, actions=actions))
    assert widget.current_thread == ("history", "one")
    assert widget.findChild(QFrame, "smsLocal") is None
    assert len(widget.findChildren(QFrame, "smsOutgoing")) == 1
    assert [text.toPlainText() for text in widget.timeline_content.findChildren(MessageText)] == [
        "Message 1", "A fictional reply"]
    assert widget.actions == actions
    assert widget._threads[("history", "one")]["actions"] == []
    assert worker.requests == requests
    activity = ActivityDialog(widget)
    qtbot.addWidget(activity)
    activity.apply_actions(widget.actions, PHONES)
    assert activity.model.rowCount() == 2
    assert "Accepted by Pushbullet" in activity.detail.toPlainText()
    assert "delivery not confirmed" in activity.detail.toPlainText()


@pytest.mark.parametrize("state", ["pending_approval", "ready", "submitting", "failed", "uncertain"])
def test_unresolved_request_stays_visible_even_with_matching_phone_history(qtbot, state):
    widget, worker = page(qtbot, [message(direction="outgoing", body="A fictional reply")])
    action = successful_action({"account_id": "acct", "payload": {
        "kind": "sms", "device_id": "a", "recipient": "+639171234567",
        "body": "A fictional reply", "transport": "provider_plaintext"}}, state=state)
    requests = deepcopy(worker.requests)
    widget.apply_snapshot(snapshot([message(direction="outgoing", body="A fictional reply")], actions=[action]))
    assert widget.findChild(QFrame, "smsLocal")
    assert len(widget.findChildren(QFrame, "smsOutgoing")) == 1
    assert widget._threads[("history", "one")]["actions"] == [action]
    assert worker.requests == requests


def test_accepted_request_without_phone_history_is_activity_only(qtbot):
    widget, worker = page(qtbot, [])
    action = successful_action({"account_id": "acct", "payload": {
        "kind": "sms", "device_id": "a", "recipient": "+12025550123",
        "body": "A fictional reply", "transport": "provider_plaintext"}}, state="upstream_accepted")
    requests = deepcopy(worker.requests)
    widget.apply_snapshot(snapshot(actions=[action]))
    assert widget.actions == [action]
    assert widget.threads.count() == 0 and not widget._threads
    assert widget.current_thread is None
    assert widget.findChild(QFrame, "smsLocal") is None
    assert worker.requests == requests


def test_last_local_request_accepted_keeps_recipient_and_other_drafts(qtbot):
    widget, worker = page(qtbot)
    widget.composer.setPlainText("Unrelated conversation draft")
    widget.start_new()
    widget.recipient.setText("+12025550123")
    widget.composer.setPlainText("Saved recipient draft")
    action = successful_action({"account_id": "acct", "payload": {
        "kind": "sms", "device_id": "a", "recipient": "+12025550123",
        "body": "A fictional reply", "transport": "provider_plaintext"}})
    widget.open_action(action)
    assert widget.current_thread == ("local", "+12025550123")
    requests = deepcopy(worker.requests)
    accepted = dict(action, state="upstream_accepted")
    widget.apply_snapshot(snapshot([message()], actions=[accepted]))
    assert widget.current_thread == ("new", "+12025550123")
    assert widget.recipient.text() == "+12025550123" and widget.recipient.isVisible()
    assert widget.composer.toPlainText() == "Saved recipient draft"
    assert ("local", "+12025550123") not in widget._threads
    assert widget.findChild(QFrame, "smsLocal") is None
    assert widget.actions == [accepted]
    assert worker.requests == requests
    widget._activate_thread(("history", "one"))
    assert widget.composer.toPlainText() == "Unrelated conversation draft"


def test_local_request_accepted_with_new_history_restores_its_reply_draft(qtbot):
    widget, worker = page(qtbot)
    widget.composer.setPlainText("Unrelated conversation draft")
    action = successful_action({"account_id": "acct", "payload": {
        "kind": "sms", "device_id": "a", "recipient": "+12025550123",
        "body": "A fictional reply", "transport": "provider_plaintext"}})
    widget.open_action(action)
    assert widget.current_thread == ("local", "+12025550123")
    widget._drafts[("acct", "a", ("history", "two"))] = ("Saved phone-history draft", 3)
    requests = deepcopy(worker.requests)
    history = [message(), message(2, conversation="two", direction="outgoing",
        addresses=["+12025550123"], body="A fictional reply")]
    widget.apply_snapshot(snapshot(history, actions=[dict(action, state="upstream_accepted")]))
    assert widget.current_thread == ("history", "two")
    assert widget.composer.toPlainText() == "Saved phone-history draft"
    assert widget._route() == "+12025550123"
    assert not widget.recipient.isVisible()
    assert widget.findChild(QFrame, "smsLocal") is None
    assert len(widget.findChildren(QFrame, "smsOutgoing")) == 1
    assert worker.requests == requests
    widget._activate_thread(("history", "one"))
    assert widget.composer.toPlainText() == "Unrelated conversation draft"


def test_ambiguous_creation_locks_exact_payload_and_uuid_until_explicit_reconcile(qtbot):
    widget, worker = page(qtbot)
    request = create_request(widget, worker, "Exactly this payload")
    count = len(worker.requests)
    worker.result.emit({"command": "create_action", "request_id": request["request_id"],
                        "account_id": "acct", "error": "timeout", "ambiguous": True})
    assert len(worker.requests) == count
    assert not widget.composer.isEnabled()
    assert widget.send_button.text() == "Reconcile same request"
    widget.submit()
    assert worker.requests[-1][1] == request
    count = len(worker.requests)
    widget.submit()
    assert len(worker.requests) == count
    succeed(worker, request)
    assert widget.composer.isEnabled()


def test_late_success_does_not_clear_or_review_other_thread_draft(qtbot):
    widget, worker = page(qtbot, [message(), message(2, conversation="two", addresses=["+12025550123"])])
    widget._activate_thread(("history", "one"))
    reviewed, created = [], []
    widget.review_requested.connect(reviewed.append)
    widget.action_created.connect(created.append)
    request = create_request(widget, worker, "First thread pending")
    widget._activate_thread(("history", "two"))
    widget.composer.setPlainText("Other thread draft")
    succeed(worker, request)
    assert not reviewed and len(created) == 1
    assert widget.composer.toPlainText() == "Other thread draft"
    widget._activate_thread(("history", "one"))
    assert widget.composer.toPlainText() == ""


def test_stale_device_partial_snapshot_cached_without_retargeting_and_account_isolation(qtbot):
    widget, worker = page(qtbot)
    request = create_request(widget, worker)
    reviewed, created = [], []
    widget.review_requested.connect(reviewed.append)
    widget.action_created.connect(created.append)
    widget.phone.setCurrentIndex(widget.phone.findData("b"))
    widget.apply_snapshot({"sms_device_id": "a", "sms": snapshot([message(4)])["sms"]})
    assert widget.selected_device_id == "b"
    assert widget.sms_cache["a"]["messages"][0]["id"] == 4
    widget.apply_snapshot(snapshot([message(10, device="b", account="new")], device="b", account="new"))
    widget.apply_snapshot({"sms_device_id": "a", "sms": snapshot([message(100)])["sms"]})
    succeed(worker, request)
    assert not reviewed and not created
    assert widget.account_id == "new"
    assert "a" not in widget.sms_cache
    assert all(key[0] == "new" for key in widget._drafts)


def test_same_phone_stale_selection_result_rejected_and_draft_version_protected(qtbot):
    widget, worker = page(qtbot)
    old_selection = widget.selection_request_id
    widget.phone.setCurrentIndex(widget.phone.findData("b"))
    widget.phone.setCurrentIndex(widget.phone.findData("a"))
    widget.apply_snapshot({"sms_device_id": "a", "sms_request_id": old_selection,
                           "sms": snapshot([message(99)])["sms"]})
    assert widget.sms_cache["a"]["messages"][0]["id"] == 1
    request = create_request(widget, worker)
    # Simulate an explicit programmatic draft replacement while the RPC is pending.
    key = widget._key()
    widget._drafts[key] = ("Newer draft", widget._drafts[key][1] + 1)
    succeed(worker, request)
    assert widget._drafts[key][0] == "Newer draft"


def test_phone_switch_restores_each_conversation_and_new_recipient_draft(qtbot):
    widget, worker = page(qtbot, [message(), message(2, conversation="two", addresses=["+12025550123"])])
    widget._activate_thread(("history", "two"))
    widget.composer.setPlainText("Phone A thread two")
    widget.phone.setCurrentIndex(widget.phone.findData("b"))
    widget.start_new()
    widget.recipient.setText("+12025550124")
    widget.composer.setPlainText("Phone B new recipient")
    widget.phone.setCurrentIndex(widget.phone.findData("a"))
    assert widget.current_thread == ("history", "two")
    assert widget.composer.toPlainText() == "Phone A thread two"
    widget.phone.setCurrentIndex(widget.phone.findData("b"))
    assert widget.current_thread == ("new", "+12025550124")
    assert widget.recipient.text() == "+12025550124"
    assert widget.composer.toPlainText() == "Phone B new recipient"


def test_ambiguous_address_association_keeps_local_recipient_thread_and_activity(qtbot):
    action = dict(successful_action({"payload": {"kind": "sms", "device_id": "a",
        "recipient": "+639171234567", "body": "Same body", "transport": "provider_plaintext"},
        "account_id": "acct"}), state="uncertain")
    widget, worker = page(qtbot, [message(body="Same body"), message(2, conversation="two")])
    widget.apply_snapshot(snapshot([message(body="Same body"), message(2, conversation="two")], actions=[action]))
    local = ("local", "+639171234567")
    assert widget._threads[local]["actions"] == [action]
    assert all(not thread["actions"] for key, thread in widget._threads.items() if key[0] == "history")
    widget.open_action(action)
    assert widget.current_thread == local
    assert widget.findChild(QFrame, "smsLocal")
    assert not widget.send_button.isEnabled()
    assert not any(command == "approve_action" for command, _ in worker.requests)


def test_history_insert_preserves_reading_anchor_and_bottom_follow(qtbot):
    messages = [message(number, body="A message with enough text to occupy a bubble. " * 3)
                for number in range(20, 60)]
    widget, worker = page(qtbot, messages)
    scroll = widget.timeline.verticalScrollBar()
    assert scroll.maximum() > 0
    scroll.setValue(scroll.maximum() // 2)
    anchor = widget._anchor()
    widget.apply_snapshot(snapshot([message(number) for number in range(1, 20)] + messages))
    qtbot.wait(80)
    assert widget._anchor()[0] == anchor[0]
    assert abs(widget._anchor()[1] - anchor[1]) <= 3
    scroll.setValue(scroll.maximum())
    widget.apply_snapshot(snapshot(messages + [message(100)]))
    qtbot.wait(80)
    assert scroll.maximum() - scroll.value() <= 3


def test_mismatched_success_remains_locked_even_after_reconciliation_error(qtbot):
    widget, worker = page(qtbot)
    reviewed = []
    widget.review_requested.connect(reviewed.append)
    request = create_request(widget, worker)
    succeed(worker, request, successful_action(request, recipient="+12025550123"))
    assert not reviewed
    assert widget.send_button.text() == "Reconcile same request"
    assert widget.composer.toPlainText() == "Hello"
    worker.result.emit({"command": "create_action", "request_id": request["request_id"],
                        "account_id": "acct", "error": "Rejected", "ambiguous": False, "status": 422})
    assert not widget.composer.isEnabled()
    assert widget.send_button.text() == "Reconcile same request"
    assert widget.composer.toPlainText() == "Hello"


def test_initial_definite_rejection_keeps_editable_draft(qtbot):
    widget, worker = page(qtbot)
    request = create_request(widget, worker)
    worker.result.emit({"command": "create_action", "request_id": request["request_id"],
                        "account_id": "acct", "error": "Rejected", "ambiguous": False, "status": 422})
    assert widget.composer.isEnabled()
    assert widget.composer.toPlainText() == "Hello"


def test_failed_empty_refresh_keeps_cached_history_sync_and_draft(qtbot):
    widget, worker = page(qtbot)
    widget.composer.setPlainText("Keep my draft")
    requests = len(worker.requests)
    widget.apply_snapshot({"sms_device_id": "a", "sms": {"account_id": "acct", "messages": [],
        "sync": {"state": "error", "error": "provider unavailable", "last_success_at": None}}})
    assert widget.sms_cache["a"]["messages"][0]["id"] == 1
    assert widget.sms_cache["a"]["sync"]["last_success_at"] == 1780000000
    assert widget.composer.toPlainText() == "Keep my draft"
    assert "Refresh unavailable" in widget.sync_status.text()
    assert "cached history kept" in widget.sync_status.accessibleDescription()
    assert "provider unavailable" in widget.sync_status.accessibleDescription()
    assert len(worker.requests) == requests


def test_scheduled_scroll_callback_safe_after_immediate_page_destruction(qtbot):
    widget, worker = page(qtbot)
    widget.apply_snapshot(snapshot([message(), message(2)]))
    widget.deleteLater()
    qtbot.wait(80)


def test_context_change_hides_previous_bubbles_synchronously(qtbot):
    widget, worker = page(qtbot)
    previous_rows = [row for _, row in widget._rows]
    assert previous_rows and previous_rows[0].isVisible()
    widget.start_new()
    assert all(not row.isVisible() and row.parentWidget() is None for row in previous_rows)
    assert not widget.timeline_content.findChildren(MessageText)
    assert widget.composer.height() == 86
    widget._activate_thread(("history", "one"))
    previous_rows = [row for _, row in widget._rows]
    widget.phone.setCurrentIndex(widget.phone.findData("b"))
    assert all(not row.isVisible() and row.parentWidget() is None for row in previous_rows)
    assert not widget.timeline_content.findChildren(MessageText)


def test_partial_sync_copy_counts_conversations_and_chat_metadata_omits_action_id(qtbot):
    widget, worker = page(qtbot)
    request = create_request(widget, worker)
    succeed(worker, request)
    card = widget.findChild(QFrame, "smsLocal")
    text = "\n".join(label.text() for label in card.findChildren(QLabel))
    assert "To +639171234567" in text
    assert "Action #" not in text
    widget.apply_snapshot({"sms_device_id": "a", "sms": {"account_id": "acct",
        "messages": [message()], "sync": {"state": "partial", "error": "some threads missing",
        "last_success_at": 1780000000, "unavailable_count": 2}}})
    assert "Partial · 2 unavailable" in widget.sync_status.text()
    assert "2 conversations unavailable" in widget.sync_status.accessibleDescription()
    assert "Available conversations synced" in widget.sync_status.accessibleDescription()
    assert "some threads missing" in widget.sync_status.accessibleDescription()
    assert "Refresh unavailable" not in widget.sync_status.accessibleDescription()
    assert widget.threads.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert widget.threads.wordWrap()


def test_sync_details_keep_provider_text_plain_and_tooltip_escaped(qtbot):
    widget, worker = page(qtbot)
    detail = 'Fixture failure <img src="https://example.invalid/image"> & retry'
    widget.apply_snapshot({'sms_device_id': 'a', 'sms': {'account_id': 'acct',
        'messages': [message()], 'sync': {'state': 'error', 'error': detail,
        'last_success_at': 1780000000, 'unavailable_count': 3}}})
    assert 'Refresh unavailable · 3 unavailable' in widget.sync_status.text()
    assert 'cached history kept' in widget.sync_status.accessibleDescription()
    assert detail in widget.sync_status.accessibleDescription()
    assert '<img' not in widget.sync_status.toolTip()
    assert '&lt;img src=&quot;https://example.invalid/image&quot;&gt; &amp; retry' in widget.sync_status.toolTip()


@pytest.mark.parametrize('scheme', ['system', 'dark'])
def test_narrow_main_window_partial_history_has_no_horizontal_overflow(qtbot, qapp, tmp_path, monkeypatch, scheme):
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QFontMetrics, QPalette
    from pbdesk.desktop.app import MainWindow
    from pbdesk.desktop.theme import apply_theme, theme_signals
    from test_desktop import FakeWorker as WindowWorker

    monkeypatch.setenv('PBDESK_STATE_DIR', str(tmp_path / 'ui-state'))
    monkeypatch.setenv('PBDESK_APPLICATION_DIR', str(tmp_path / 'ui-app'))
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    manager = theme_signals(qapp)
    old = (QPalette(qapp.palette()), qapp.styleSheet(), manager.scheme, manager._last_colors)
    apply_theme(qapp, scheme)
    worker = WindowWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    try:
        data = snapshot([message()])
        data['sms']['sync'] = {'state': 'partial', 'error': '3 listed conversations are currently unavailable; cached history kept',
            'last_success_at': 1780000000, 'unavailable_count': 3}
        worker.snapshot.emit(data)
        window.navigate(1)
        window.resize(850, 540)
        window.show()
        window.sms_page.composer.setPlainText('Fictional draft remains editable')
        worker.result.emit({'command': 'refresh_devices', 'error': 'Device refresh failed. Check the provider connection and retry.'})
        qtbot.wait(40)
        page = window.sms_page
        viewport = window.sms_scroll.viewport()
        assert window.error.isVisible() and window.command_error
        assert (window.width(), window.height()) == (850, 540)
        assert 'Partial · 3 unavailable' in page.sync_status.text()
        assert 'Available conversations synced' in page.sync_status.accessibleDescription()
        assert '3 conversations unavailable' in page.sync_status.accessibleDescription()
        assert page.width() <= viewport.width()
        assert window.sms_scroll.horizontalScrollBar().maximum() == 0
        assert QFontMetrics(page.sync_status.font()).horizontalAdvance(page.sync_status.text()) <= page.sync_status.width()
        assert page.composer.height() == 86
        assert page.composer.toPlainText() == 'Fictional draft remains editable'
        for button in (window.sms_ai_button, page.send_button):
            assert button.height() >= button.sizeHint().height()
            window.sms_scroll.ensureWidgetVisible(button, 0, 0)
            qtbot.wait(10)
            top_left = button.mapTo(viewport, button.rect().topLeft())
            assert viewport.rect().contains(QRect(top_left, button.size()))
        window.sms_scroll.ensureWidgetVisible(page.composer, 0, 0)
        qtbot.wait(10)
        top_left = page.composer.mapTo(viewport, page.composer.rect().topLeft())
        assert viewport.rect().contains(QRect(top_left, page.composer.size()))
        assert not any(command in {'create_action', 'approve_action', 'media_image'} for command, *unused in worker.requests)
    finally:
        window.exit_desktop()
        manager._updating = True
        try:
            qapp.setStyleSheet(old[1])
            qapp.setPalette(old[0])
            qapp.processEvents()
            manager.scheme, manager._last_colors = old[2:]
            manager._scheduled = False
        finally:
            manager._updating = False
