"""Fake-worker checks: no network, provider account, SMS, or persistent database."""
from copy import deepcopy

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel

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
                 created_at="2026-11-14T00:00:00Z")
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
    assert "cached history kept" in widget.sync_status.text()
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
    assert "2 conversations unavailable" in widget.sync_status.text()
    assert "available conversations synced" in widget.sync_status.text()
    assert "refresh unavailable" not in widget.sync_status.text()
    assert widget.threads.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert widget.threads.wordWrap()
