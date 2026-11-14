"""Native SMS conversations. Phone history and local send requests stay distinct."""
from dataclasses import dataclass
from datetime import datetime
import re
import uuid

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6 import sip
from PyQt6.QtGui import QKeySequence, QShortcut, QTextOption
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from .actions import PLAINTEXT_NOTICE, STATE_LABELS, plain_label
from .theme import primary

E164 = re.compile(r"\+[1-9][0-9]{1,14}\Z")
CHAT_STYLE = """
QWidget#smsPage { background: #faf9f6; }
QWidget#smsPage QScrollArea { background: #f2f5f1; border: 1px solid #d7dfda; border-radius: 6px; }
QWidget#smsTimeline { background: #f2f5f1; }
QFrame#smsIncoming { background: #ffffff; border: 1px solid #d8e0dc; border-radius: 12px; }
QFrame#smsOutgoing { background: #d2ebe1; border: 1px solid #b0d9c9; border-radius: 12px; }
QFrame#smsLocal { background: #fff6e5; border: 1px dashed #d3b77f; border-radius: 8px; }
QWidget#smsPage QLabel#smsMeta { color: #637773; font-size: 12px; }
QWidget#smsPage QLabel#smsDate { color: #657176; padding: 12px 0 5px; }
QWidget#smsPage QLabel#smsHeading { color: #20383e; font-size: 20px; font-weight: 600; }
QWidget#smsPage QListWidget::item { padding: 15px 12px; }
"""


def _time(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value).astimezone()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _order(value):
    value = _time(value)
    return value.timestamp() if value else 0


def _recipient(messages):
    """A reply route comes only from consistent, single-address phone records."""
    recipients = set()
    for message in messages:
        addresses = message.get("addresses")
        if not isinstance(addresses, list) or len(addresses) != 1:
            return None
        address = addresses[0]
        if not isinstance(address, str) or not E164.fullmatch(address):
            return None
        recipients.add(address)
    return next(iter(recipients)) if len(recipients) == 1 else None


@dataclass(frozen=True)
class PendingCreate:
    account_id: object
    device_id: str
    context: tuple
    recipient: str
    body: str
    version: int
    request_id: str

    def payload(self):
        return {"kind": "sms", "device_id": self.device_id,
                "recipient": self.recipient, "body": self.body, "title": "",
                "url": None, "transport": "provider_plaintext",
                "client_request_id": self.request_id, "expires_in_seconds": 3600}


class MessageText(QTextEdit):
    """Plain selectable text with native wrapping, including unbroken strings."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setPlainText(text)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setStyleSheet("background: transparent; border: none; padding: 0;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    def _fit(self, *unused):
        self.setFixedHeight(max(28, int(self.document().size().height()) + 4))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class SmsPage(QWidget):
    review_requested = pyqtSignal(dict)
    action_created = pyqtSignal(dict)
    activity_requested = pyqtSignal()

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.account_id = None
        self.account_known = False
        self.capabilities = {}
        self.devices = []
        self.actions = []
        self.sms_cache = {}
        self.selected_device_id = None
        self.selection_request_id = None
        self.current_thread = None
        self._threads = {}
        self._drafts = {}
        self._new_recipients = {}
        self._contexts = {}
        self._pending = {}
        self._pending_state = {}
        self._uncertain_requests = set()
        self._loading = False
        self._timeline_signature = None
        self._rows = []
        self._render_generation = 0
        self.setObjectName("smsPage")
        self.setStyleSheet(CHAT_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addWidget(plain_label("Phone"))
        self.phone = QComboBox()
        self.phone.setObjectName("smsPhoneSelector")
        self.phone.setMinimumWidth(220)
        self.phone.setAccessibleName("SMS phone")
        self.phone.currentIndexChanged.connect(self._phone_changed)
        toolbar.addWidget(self.phone)
        self.sync_status = plain_label("Choose a phone to read SMS history.")
        self.sync_status.setObjectName("smsMeta")
        toolbar.addWidget(self.sync_status, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button)
        self.new_button = QPushButton("+ New message")
        self.new_button.clicked.connect(self.start_new)
        toolbar.addWidget(self.new_button)
        layout.addLayout(toolbar)
        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setObjectName("smsThreadSearch")
        self.search.setPlaceholderText("Search conversations…")
        self.search.setAccessibleName("Search SMS conversations")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._populate_threads)
        left_layout.addWidget(self.search)
        self.threads = QListWidget()
        self.threads.setObjectName("smsThreads")
        self.threads.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.threads.setWordWrap(True)
        self.threads.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.threads.setAccessibleName("SMS conversations")
        self.threads.currentItemChanged.connect(self._thread_changed)
        left_layout.addWidget(self.threads)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        self.heading = plain_label("Your SMS conversations")
        self.heading.setObjectName("smsHeading")
        right_layout.addWidget(self.heading)
        self.context_label = plain_label("Select a conversation, or start a new message.")
        self.context_label.setObjectName("smsMeta")
        right_layout.addWidget(self.context_label)
        self.timeline = QScrollArea()
        self.timeline.setObjectName("smsTimelineScroll")
        self.timeline.setWidgetResizable(True)
        self.timeline.setAccessibleName("SMS conversation history")
        self.timeline_content = QWidget()
        self.timeline_content.setObjectName("smsTimeline")
        self.timeline_layout = QVBoxLayout(self.timeline_content)
        self.timeline_layout.setContentsMargins(16, 8, 16, 16)
        self.timeline_layout.setSpacing(8)
        self.timeline_layout.addStretch()
        self.timeline.setWidget(self.timeline_content)
        right_layout.addWidget(self.timeline, 1)
        self.recipient = QLineEdit()
        self.recipient.setObjectName("smsRecipient")
        self.recipient.setPlaceholderText("+639171234567 — explicit E.164 recipient")
        self.recipient.setAccessibleName("New SMS recipient")
        self.recipient.setMaxLength(32)
        self.recipient.textChanged.connect(self._recipient_changed)
        self.recipient.hide()
        right_layout.addWidget(self.recipient)
        self.composer = QTextEdit()
        self.composer.setObjectName("smsComposer")
        self.composer.setAcceptRichText(False)
        self.composer.setPlaceholderText("Write a message…")
        self.composer.setAccessibleName("SMS message draft")
        self.composer.setFixedHeight(86)
        self.composer.textChanged.connect(self._draft_changed)
        right_layout.addWidget(self.composer)
        self.feedback = plain_label()
        self.feedback.setObjectName("smsMeta")
        right_layout.addWidget(self.feedback)
        row = QHBoxLayout()
        hint = plain_label("Ctrl+Enter to review · Enter for a new line")
        hint.setObjectName("smsMeta")
        row.addWidget(hint, 1)
        self.send_button = primary(QPushButton("Review & send"))
        self.send_button.setObjectName("smsSendButton")
        self.send_button.clicked.connect(self.submit)
        row.addWidget(self.send_button)
        right_layout.addLayout(row)
        notice = plain_label(PLAINTEXT_NOTICE)
        notice.setObjectName("smsMeta")
        right_layout.addWidget(notice)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 700])
        layout.addWidget(splitter, 1)
        self.send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.composer)
        self.send_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.send_shortcut.activated.connect(self.submit)
        self.enter_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), self.composer)
        self.enter_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.enter_shortcut.activated.connect(self.submit)
        worker.result.connect(self.handle_result)
        self._update_controls()

    def _key(self):
        if self.current_thread is None or not self.selected_device_id:
            return None
        return (self.account_id, self.selected_device_id, self.current_thread)

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def _save_draft(self):
        key = self._key()
        if key is not None:
            previous = self._drafts.get(key, ("", 0))
            self._drafts[key] = (self.composer.toPlainText(), previous[1])
            self._contexts[(self.account_id, self.selected_device_id)] = self.current_thread

    def _load_draft(self):
        self._loading = True
        self.composer.setPlainText(self._drafts.get(self._key(), ("", 0))[0])
        self._loading = False
        self._update_controls()

    def _draft_changed(self):
        if self._loading:
            return
        key = self._key()
        if key is not None:
            version = self._drafts.get(key, ("", 0))[1] + 1
            self._drafts[key] = (self.composer.toPlainText(), version)
        self._update_controls()

    def _recipient_changed(self, text):
        if self._loading or not self.current_thread or self.current_thread[0] != "new":
            return
        self._save_draft()
        self.current_thread = ("new", text.strip())
        self._new_recipients[(self.account_id, self.selected_device_id)] = text
        self._load_draft()

    def apply_snapshot(self, data):
        """Consume each worker snapshot once; never connect worker.snapshot here."""
        capabilities = data.get("capabilities")
        if capabilities is not None and "account_id" in capabilities:
            account = capabilities["account_id"]
            if not self.account_known or account != self.account_id:
                self._reset_account(account)
        if capabilities is not None:
            self.capabilities = dict(capabilities)
        devices = data.get("devices")
        if isinstance(devices, dict) and self._part_current(devices):
            self.devices = [dict(item) for item in devices.get("devices", [])]
        if "actions" in data:
            actions = data["actions"]
            if isinstance(actions, dict):
                actions = actions.get("actions", []) if self._part_current(actions) else None
            # The worker guarantees account-consistent arrays in full snapshots.
            if actions is not None:
                self.actions = [dict(item) for item in actions if self._part_current(item)]
        sms = data.get("sms")
        device_id = data.get("sms_device_id")
        if isinstance(sms, dict) and device_id and self._part_current(sms):
            request_id = data.get("sms_request_id")
            if device_id != self.selected_device_id or not request_id or request_id == self.selection_request_id:
                previous = self.sms_cache.get(device_id, {})
                messages = [dict(item) for item in sms.get("messages", [])
                    if self._part_current(item) and item.get("device_id", device_id) == device_id]
                sync = dict(sms.get("sync", {}))
                if not messages and sync.get("error") and previous.get("messages"):
                    messages = previous["messages"]
                    sync["last_success_at"] = previous.get("sync", {}).get("last_success_at")
                self.sms_cache[device_id] = dict(sms, messages=messages, sync=sync)
        self._populate_phones()
        self._rebuild_threads()
        self._update_sync()
        self._update_controls()

    def _part_current(self, part):
        return "account_id" not in part or (self.account_known and part["account_id"] == self.account_id)

    def _reset_account(self, account):
        self.account_id, self.account_known = account, True
        self.devices, self.actions, self.sms_cache = [], [], {}
        self._drafts.clear()
        self._new_recipients.clear()
        self._contexts.clear()
        self._pending.clear()
        self._pending_state.clear()
        self._uncertain_requests.clear()
        self.selected_device_id = None
        self.selection_request_id = None
        self.current_thread = None
        self._threads = {}
        self._loading = True
        self.composer.clear()
        self.recipient.clear()
        self._loading = False
        self._timeline_signature = None

    def _populate_phones(self):
        selected = self.selected_device_id
        known = {item["id"]: item for item in self.devices}
        history_ids = (set(self.sms_cache) |
            {item["id"] for item in self.devices if item.get("history", {}).get("count")} |
            {item.get("device_id") for item in self.actions if item.get("kind") == "sms"})
        selectable = [item for item in self.devices if item.get("can_sms") or item["id"] in history_ids]
        for device_id in history_ids - set(known):
            if device_id:
                selectable.append({"id": device_id, "nickname": device_id, "active": False})
        # Preserve an existing destination even when its metadata disappears.
        if selected and selected not in {item["id"] for item in selectable}:
            selectable.append({"id": selected, "nickname": selected, "active": False})
        self.phone.blockSignals(True)
        self.phone.clear()
        self.phone.addItem("Select a phone…", None)
        for device in selectable:
            ready = device.get("active") and device.get("can_sms")
            label = str(device.get("nickname") or device["id"])
            if not ready:
                label += " · history only"
            self.phone.addItem(label, device["id"])
        if selected is None:
            selected = next((item["id"] for item in selectable if item.get("active") and item.get("can_sms")), None)
            if selected is None and selectable:
                selected = selectable[0]["id"]
        self.phone.setCurrentIndex(max(0, self.phone.findData(selected)))
        self.phone.blockSignals(False)
        if selected != self.selected_device_id:
            self._select_phone(selected)

    def _phone_changed(self):
        self._select_phone(self.phone.currentData())

    def _select_phone(self, device_id):
        if device_id == self.selected_device_id:
            return
        self._save_draft()
        self.selected_device_id = device_id
        self.current_thread = self._contexts.get((self.account_id, device_id))
        if self.current_thread and self.current_thread[0] == "new":
            self._loading = True
            self.recipient.setText(self._new_recipients.get((self.account_id, device_id), ""))
            self._loading = False
        self._timeline_signature = None
        self._rebuild_threads(new_context=True)
        self._load_draft()
        self._update_sync()
        self.selection_request_id = str(uuid.uuid4())
        self.worker.request("select_device", {"device_id": device_id, "request_id": self.selection_request_id})
        if device_id:
            self.refresh()

    def refresh(self):
        if self.selected_device_id and self.account_known and self.capabilities.get("features", {}).get("sms_history"):
            self.worker.request("refresh_sms", {"device_id": self.selected_device_id,
                "request_id": str(uuid.uuid4()), "account_id": self.account_id})

    def _rebuild_threads(self, new_context=False):
        threads = {}
        messages = self.sms_cache.get(self.selected_device_id, {}).get("messages", [])
        for message in messages:
            conversation = message.get("conversation_id")
            if conversation is None:
                continue
            key = ("history", str(conversation))
            threads.setdefault(key, {"messages": [], "actions": []})["messages"].append(message)
        for thread in threads.values():
            thread["messages"].sort(key=lambda item: (_order(item.get("timestamp")), str(item.get("id", ""))))
            thread["recipient"] = _recipient(thread["messages"])
        for action in self.actions:
            # Accepted requests remain in Message activity. They are not phone
            # messages, and keeping them here duplicates the synchronized SMS.
            # In particular, do not infer delivery by matching body/time text.
            if (action.get("kind") != "sms" or action.get("device_id") != self.selected_device_id
                    or action.get("state") == "upstream_accepted"):
                continue
            recipient = action.get("recipient")
            matches = [key for key, thread in threads.items() if key[0] == "history" and thread["recipient"] == recipient and recipient]
            key = matches[0] if len(matches) == 1 else ("local", str(recipient or "Unknown recipient"))
            threads.setdefault(key, {"messages": [], "actions": [], "recipient": None})["actions"].append(action)
        self._threads = threads
        if self.current_thread and self.current_thread[0] == "local" and self.current_thread not in threads:
            # The last local request can finish before phone history arrives.
            # Keep focus on the same recipient, rather than jumping to another
            # person's conversation or leaving an empty local-request thread.
            recipient = self.current_thread[1]
            matches = [key for key, thread in threads.items()
                       if key[0] == "history" and recipient and thread.get("recipient") == recipient]
            self.current_thread = matches[0] if len(matches) == 1 else ("new", recipient if E164.fullmatch(recipient) else "")
            if self.current_thread[0] == "new":
                self._new_recipients[(self.account_id, self.selected_device_id)] = self.current_thread[1]
                self._loading = True
                self.recipient.setText(self.current_thread[1])
                self._loading = False
            self._contexts[(self.account_id, self.selected_device_id)] = self.current_thread
            self._load_draft()
            new_context = True
        self._populate_threads()
        if self.current_thread is None and threads:
            self._activate_thread(self.threads.item(0).data(Qt.ItemDataRole.UserRole) if self.threads.count() else next(iter(threads)), True)
        else:
            self._render_timeline(new_context)
        self._update_controls()

    def _thread_title(self, key, thread):
        if key[0] == "local":
            return "Local requests · " + key[1]
        messages = thread["messages"]
        name = next((str(item["name"]) for item in reversed(messages) if item.get("name")), None)
        addresses = sorted({str(address) for item in messages for address in item.get("addresses", [])})
        return name or thread.get("recipient") or (", ".join(addresses) if addresses else "Unknown sender")

    def _populate_threads(self):
        self.threads.blockSignals(True)
        self.threads.clear()
        query = self.search.text().casefold()
        def recent(entry):
            return max([_order(item.get("timestamp")) for item in entry[1]["messages"]] +
                       [_order(item.get("created_at")) for item in entry[1]["actions"]] + [0])
        for key, thread in sorted(self._threads.items(), key=recent, reverse=True):
            title = self._thread_title(key, thread)
            records = thread["messages"]
            preview = str(records[-1].get("body", "")) if records else "Local request · review in Activity"
            searchable = title + " " + " ".join(str(item.get("body", "")) for item in records)
            if query and query not in searchable.casefold():
                continue
            item = QListWidgetItem(title + "\n" + preview[:90].replace("\n", " "))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.threads.addItem(item)
            if key == self.current_thread:
                self.threads.setCurrentItem(item)
        self.threads.blockSignals(False)

    def _thread_changed(self, item, previous=None):
        if item is not None:
            self._activate_thread(item.data(Qt.ItemDataRole.UserRole), True)

    def _activate_thread(self, key, new_context=False):
        if key == self.current_thread:
            return
        self._save_draft()
        self.current_thread = key
        self.recipient.hide()
        self._load_draft()
        self._populate_threads()
        self._render_timeline(new_context=True)

    def start_new(self):
        self._save_draft()
        recipient = self._new_recipients.get((self.account_id, self.selected_device_id), "")
        self.current_thread = ("new", recipient.strip())
        self._loading = True
        self.recipient.setText(recipient)
        self._loading = False
        self.recipient.show()
        self.threads.clearSelection()
        self._load_draft()
        self._render_timeline(new_context=True)
        self.recipient.setFocus()

    def open_action(self, action):
        if action.get("kind") != "sms" or not self._part_current(action):
            return
        if not any(item.get("id") == action.get("id") for item in self.actions):
            self.actions.append(dict(action))
        self._populate_phones()
        index = self.phone.findData(action.get("device_id"))
        if index >= 0:
            self.phone.setCurrentIndex(index)
        self._rebuild_threads()
        key = next((key for key, thread in self._threads.items() if any(item.get("id") == action.get("id") for item in thread["actions"])), None)
        if key is not None:
            self._activate_thread(key, True)

    def _current_pending(self):
        key = self._key()
        return next((record for record in self._pending.values() if record.context == key), None)

    def _route(self):
        if self.current_thread and self.current_thread[0] == "new":
            return self.recipient.text().strip()
        return self._threads.get(self.current_thread, {}).get("recipient")

    def validation_message(self):
        if not self.account_known or not self.account_id:
            return "Connect a provider account to prepare an SMS."
        device = next((item for item in self.devices if item["id"] == self.selected_device_id), {})
        if not device.get("active") or not device.get("can_sms"):
            return "This phone is history only. Choose an active SMS-capable phone for a new message."
        if self.current_thread is None:
            return "Select a conversation, or choose New message."
        route = self._route()
        if not route or not E164.fullmatch(route):
            return "Choose New message and enter an explicit E.164 number to send. Group or unknown senders are read-only."
        if not self.capabilities.get("features", {}).get("send_sms") or not self.capabilities.get("send_ready"):
            return self.capabilities.get("send_unavailable_reason") or "SMS sending is currently unavailable."
        if not self.composer.toPlainText().strip():
            return "Enter a message to review before sending."
        if len(self.composer.toPlainText()) > 65536:
            return "The message body is limited to 65,536 characters."
        return ""

    def _update_controls(self):
        pending = self._current_pending()
        state = self._pending_state.get(pending.request_id) if pending else None
        self.composer.setEnabled(self.current_thread is not None and pending is None)
        self.recipient.setEnabled(pending is None)
        self.send_button.setText("Reconcile same request" if state == "ambiguous" else "Saving for review…" if pending else "Review & send")
        self.send_button.setEnabled((state == "ambiguous" and pending.account_id == self.account_id) if pending else not self.validation_message())
        self.feedback.setText("The result is uncertain. Reconcile the exact original request; no SMS is sent by this button." if state == "ambiguous" else
            "Saving a pending request. Nothing is sent until you review and approve it." if pending else self.validation_message())
        self.refresh_button.setEnabled(bool(self.selected_device_id) and bool(self.capabilities.get("features", {}).get("sms_history")))

    def submit(self):
        record = self._current_pending()
        if record:
            if self._pending_state.get(record.request_id) != "ambiguous" or record.account_id != self.account_id:
                return
        else:
            if self.validation_message():
                return
            self._save_draft()
            key = self._key()
            record = PendingCreate(self.account_id, self.selected_device_id, key, self._route(),
                                   self.composer.toPlainText(), self._drafts[key][1], str(uuid.uuid4()))
            self._pending[record.request_id] = record
        self._pending_state[record.request_id] = "busy"
        self._update_controls()
        self.worker.request("create_action", {"request_id": record.request_id,
            "account_id": record.account_id, "payload": record.payload()})

    def handle_result(self, result):
        if result.get("command") != "create_action":
            return
        record = self._pending.get(result.get("request_id"))
        if record is None or record.account_id != self.account_id or not self._part_current(result):
            return
        if "data" in result:
            action = result["data"]
            if not isinstance(action, dict) or not self._part_current(action):
                return
            # A mismatching response must never become approval for another payload.
            if any(action.get(key) != value for key, value in record.payload().items() if key in {"kind", "device_id", "recipient", "body", "transport", "title", "url"}):
                self._pending_state[record.request_id] = "ambiguous"
                self._uncertain_requests.add(record.request_id)
                self._update_controls()
                return
            del self._pending[record.request_id]
            self._pending_state.pop(record.request_id, None)
            self._uncertain_requests.discard(record.request_id)
            if self._drafts.get(record.context, (None, None))[1] == record.version:
                self._drafts[record.context] = ("", record.version + 1)
                if self._key() == record.context:
                    self._load_draft()
            self.actions = [item for item in self.actions if item.get("id") != action.get("id")] + [dict(action)]
            self._rebuild_threads()
            self.action_created.emit(dict(action))
            if self._key() == record.context:
                self.review_requested.emit(dict(action))
        elif result.get("ambiguous") or record.request_id in self._uncertain_requests:
            self._pending_state[record.request_id] = "ambiguous"
            self._uncertain_requests.add(record.request_id)
        else:
            del self._pending[record.request_id]
            self._pending_state.pop(record.request_id, None)
        self._update_controls()
        if "data" not in result and record.request_id not in self._uncertain_requests and self._key() == record.context:
            self.feedback.setText("The request was rejected. Your draft is kept; check the phone and recipient before trying again.")

    def _update_sync(self):
        sync = self.sms_cache.get(self.selected_device_id, {}).get("sync", {})
        state = sync.get("state")
        last = _time(sync.get("last_success_at"))
        text = "Stored phone history" + (" · " + last.strftime("%d %b, %H:%M") if last else " · not yet synced")
        if state and state not in {"ready", "ok", "synced"}:
            text += " · " + str(state).replace("_", " ")
        if sync.get("error"):
            text += (" · available conversations synced; some history unavailable" if state == "partial" else
                     " · refresh unavailable; cached history kept")
        if sync.get("unavailable_count"):
            text += " · " + str(sync["unavailable_count"]) + " conversations unavailable"
        self.sync_status.setText(text if self.selected_device_id else "Choose a phone to read SMS history.")

    def _anchor(self):
        value = self.timeline.verticalScrollBar().value()
        for key, row in self._rows:
            if row.y() + row.height() > value:
                return key, row.y() - value
        return None

    def _render_timeline(self, new_context=False):
        thread = self._threads.get(self.current_thread, {"messages": [], "actions": []})
        is_new = bool(self.current_thread and self.current_thread[0] == "new")
        self.recipient.setVisible(is_new)
        self.heading.setText("New message" if is_new else self._thread_title(self.current_thread, thread) if self.current_thread in self._threads else "Your SMS conversations")
        route = thread.get("recipient")
        self.context_label.setText("Enter the complete phone number, including + and country code." if is_new else
            ("Phone history · " + route if route else "Phone history is read-only here. Use New message with an explicit number.") if thread["messages"] else
            "Local requests are separate from phone history. Review their exact payload in Activity.")
        signature = (self.account_id, self.selected_device_id, self.current_thread, repr(thread))
        if signature == self._timeline_signature:
            return
        scroll = self.timeline.verticalScrollBar()
        near_bottom = scroll.maximum() - scroll.value() < 48
        anchor = self._anchor()
        old_value = scroll.value()
        self._timeline_signature = signature
        self._render_generation += 1
        generation = self._render_generation
        while self.timeline_layout.count():
            item = self.timeline_layout.takeAt(0)
            if item.widget():
                old_widget = item.widget()
                old_widget.hide()
                old_widget.setParent(None)
                old_widget.deleteLater()
        self._rows = []
        day = None
        for message in thread["messages"]:
            stamp = _time(message.get("timestamp"))
            date = stamp.strftime("%d %B %Y") if stamp else "Unknown date"
            if day != date:
                label = plain_label(date)
                label.setObjectName("smsDate")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.timeline_layout.addWidget(label)
                day = date
            outgoing = message.get("direction") == "outgoing"
            row, bubble, bubble_layout = self._card("smsOutgoing" if outgoing else "smsIncoming", outgoing)
            sender = "You · phone SMS" if outgoing else str(message.get("name") or ", ".join(message.get("addresses", [])) or "Unknown sender")
            meta = plain_label(sender + " · " + (stamp.strftime("%H:%M") if stamp else "Unknown time"))
            meta.setObjectName("smsMeta")
            bubble_layout.addWidget(meta)
            body = MessageText(str(message.get("body") or ""))
            body.setAccessibleName("Outgoing SMS" if outgoing else "Incoming SMS")
            bubble_layout.addWidget(body)
            key = ("sms", str(message.get("id") or message.get("message_id")))
            row.setProperty("messageId", key[1])
            bubble.setProperty("direction", "outgoing" if outgoing else "incoming")
            self.timeline_layout.addWidget(row)
            self._rows.append((key, row))
        for action in sorted(thread["actions"], key=lambda item: (_order(item.get("created_at")), str(item.get("id")))):
            row, bubble, bubble_layout = self._card("smsLocal", True)
            label = plain_label("LOCAL REQUEST · " + STATE_LABELS.get(action.get("state"), str(action.get("state", "Unknown"))))
            label.setObjectName("smsMeta")
            bubble_layout.addWidget(label)
            bubble_layout.addWidget(MessageText(str(action.get("body") or "")))
            date = _time(action.get("created_at"))
            details = "To " + str(action.get("recipient") or "Unknown recipient")
            if date:
                details += " · " + date.strftime("%d %b, %H:%M")
            bubble_layout.addWidget(plain_label(details))
            button = QPushButton("Review / edit in Activity")
            button.clicked.connect(self.activity_requested.emit)
            bubble_layout.addWidget(button)
            if action.get("state") == "uncertain":
                bubble_layout.addWidget(plain_label("Pushbullet may already have accepted this message. No automatic retry."))
            self.timeline_layout.addWidget(row)
            self._rows.append((("action", str(action.get("id"))), row))
        if not self._rows:
            empty = plain_label("Write a new message below." if is_new else "No SMS history for this conversation yet.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.timeline_layout.addWidget(empty)
        self.timeline_layout.addStretch()
        self.timeline_content.adjustSize()

        def restore():
            if sip.isdeleted(self) or generation != self._render_generation:
                return
            self.timeline_layout.activate()
            if new_context or near_bottom:
                scroll.setValue(scroll.maximum())
            elif anchor:
                row = next((row for key, row in self._rows if key == anchor[0]), None)
                scroll.setValue(row.y() - anchor[1] if row else old_value)
            else:
                scroll.setValue(old_value)
        # Native wrapping settles after the viewport's layout pass.
        QTimer.singleShot(0, restore)
        QTimer.singleShot(30, restore)

    def _card(self, name, outgoing):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        bubble = QFrame()
        bubble.setObjectName(name)
        bubble.setMaximumWidth(560)
        bubble.setMinimumWidth(130)
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        contents = QVBoxLayout(bubble)
        contents.setContentsMargins(12, 9, 12, 9)
        contents.setSpacing(5)
        if outgoing:
            row_layout.addStretch(1)
        row_layout.addWidget(bubble, 4)
        if not outgoing:
            row_layout.addStretch(1)
        return row, bubble, contents
