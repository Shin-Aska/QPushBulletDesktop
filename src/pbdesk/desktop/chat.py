"""Native SMS conversations. Phone history and local send requests stay distinct."""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from html import escape
import re
import uuid
import weakref

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6 import sip
from PyQt6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut, QTextOption
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from .media import attachment_revision, contact_icon, inline_image, RemoteImage
from .actions import PLAINTEXT_NOTICE, STATE_LABELS, plain_label
from .theme import colors, primary, theme_signals
from .widgets import ConversationDelegate, card_frame, divider_line, make_icon

E164 = re.compile(r"\+[1-9][0-9]{1,14}\Z")



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
    ai_context_changed = pyqtSignal()

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
        self.filter_device_id = None
        self._filter_contexts = {}
        self.selection_request_id = None
        self.current_thread = None
        self._threads = {}
        self._contact_icons = OrderedDict()
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        self.sync_status = plain_label("Stored SMS history across your phones.")
        self.sync_status.setWordWrap(True)
        self.sync_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.sync_status.setObjectName("smsMeta")
        toolbar.addWidget(self.sync_status)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("inlineLink")
        self.refresh_button.setIcon(make_icon("refresh", colors()["text"], 14))
        self.refresh_button.setIconSize(QSize(14, 14))
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button)
        self.new_button = primary(QPushButton("+ New message"))
        self.new_button.clicked.connect(self.start_new)
        toolbar.addWidget(self.new_button)
        layout.addLayout(toolbar)

        splitter = QSplitter()
        left = card_frame()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 10)
        left_layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("smsThreadSearch")
        self.search.setPlaceholderText("Search conversations…")
        self.search.setAccessibleName("Search SMS conversations")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._populate_threads)
        left_layout.addWidget(self.search)
        self.phone = QComboBox()
        self.phone.setObjectName("smsPhoneSelector")
        self.phone.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.phone.setMinimumContentsLength(12)
        self.phone.setAccessibleName("Filter SMS by phone")
        self.phone.currentIndexChanged.connect(self._phone_changed)
        left_layout.addWidget(self.phone)
        left_layout.addWidget(divider_line())
        self.threads = QListWidget()
        self.threads.setObjectName("smsThreads")
        self.threads.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.threads.setWordWrap(True)
        self.threads.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.threads.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.threads.setItemDelegate(ConversationDelegate(_time, self.threads))
        self.threads.setAccessibleName("SMS conversations")
        self.threads.currentItemChanged.connect(self._thread_changed)
        left_layout.addWidget(self.threads, 1)
        splitter.addWidget(left)

        right_outer = QWidget()
        right_outer_layout = QVBoxLayout(right_outer)
        right_outer_layout.setContentsMargins(0, 0, 0, 0)
        right_outer_layout.setSpacing(6)

        right = card_frame()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        chat_header = QWidget()
        chat_header_layout = QHBoxLayout(chat_header)
        chat_header_layout.setContentsMargins(18, 12, 18, 12)
        self.avatar = QLabel()
        self.avatar.setFixedSize(36, 36)
        self.avatar.setAccessibleName("Conversation avatar")
        chat_header_layout.addWidget(self.avatar)
        header_titles = QVBoxLayout()
        header_titles.setSpacing(2)
        self.heading = plain_label("Your SMS conversations")
        self.heading.setObjectName("smsHeading")
        self.heading.setWordWrap(True)
        header_titles.addWidget(self.heading)
        self.context_label = plain_label("Select a conversation, or start a new message.")
        self.context_label.setObjectName("smsMeta")
        header_titles.addWidget(self.context_label)
        chat_header_layout.addLayout(header_titles, 1)
        self._avatar_key = None
        self.avatar_remote = RemoteImage(worker, {}, valid=self._media_validator())
        self.avatar_remote.setMaximumWidth(180)
        self.avatar_remote.set_preview_bounds(72, 72)
        self.avatar_remote.image_loaded.connect(self._remote_avatar_loaded)
        chat_header_layout.addWidget(self.avatar_remote)
        right_layout.addWidget(chat_header)
        right_layout.addWidget(divider_line())

        self.timeline = QScrollArea()
        self.timeline.setObjectName("smsTimelineScroll")
        self.timeline.setWidgetResizable(True)
        self.timeline.setAccessibleName("SMS conversation history")
        self.timeline_content = QWidget()
        self.timeline_content.setObjectName("smsTimeline")
        self.timeline_layout = QVBoxLayout(self.timeline_content)
        self.timeline_layout.setContentsMargins(18, 12, 18, 16)
        self.timeline_layout.setSpacing(10)
        self.timeline_layout.addStretch()
        self.timeline.setWidget(self.timeline_content)
        right_layout.addWidget(self.timeline, 1)
        right_layout.addWidget(divider_line())

        composer_wrap = QWidget()
        composer_layout = QVBoxLayout(composer_wrap)
        composer_layout.setContentsMargins(16, 12, 16, 12)
        composer_layout.setSpacing(8)
        self.recipient = QLineEdit()
        self.recipient.setObjectName("smsRecipient")
        self.recipient.setPlaceholderText("+639171234567 — explicit E.164 recipient")
        self.recipient.setAccessibleName("New SMS recipient")
        self.recipient.setMaxLength(32)
        self.recipient.textChanged.connect(self._recipient_changed)
        self.recipient.hide()
        composer_layout.addWidget(self.recipient)
        self.send_from = QComboBox()
        self.send_from.setAccessibleName("Send SMS from phone")
        self.send_from.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.send_from.setMinimumContentsLength(12)
        self.send_from.currentIndexChanged.connect(self._sender_changed)
        self.send_from.hide()
        composer_layout.addWidget(self.send_from)
        self.composer = QTextEdit()
        self.composer.setObjectName("smsComposer")
        self.composer.setAcceptRichText(False)
        self.composer.setPlaceholderText("Write a message…")
        self.composer.setAccessibleName("SMS message draft")
        self.composer.setFixedHeight(86)
        self.composer.textChanged.connect(self._draft_changed)
        self.assistant_layout = QVBoxLayout()
        self.assistant_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.addLayout(self.assistant_layout)
        composer_layout.addWidget(self.composer)
        self.feedback = plain_label()
        self.feedback.setObjectName("smsMeta")
        composer_layout.addWidget(self.feedback)
        row = QHBoxLayout()
        hint = plain_label("Ctrl+Enter to review · Enter for a new line")
        hint.setObjectName("smsMeta")
        row.addWidget(hint, 1)
        self.send_button = primary(QPushButton("Review & send"))
        self.send_button.setObjectName("smsSendButton")
        self.send_button.clicked.connect(self.submit)
        row.addWidget(self.send_button)
        composer_layout.addLayout(row)
        right_layout.addWidget(composer_wrap)

        right_outer_layout.addWidget(right, 1)
        notice = plain_label(PLAINTEXT_NOTICE)
        notice.setObjectName("smsMeta")
        right_outer_layout.addWidget(notice)
        splitter.addWidget(right_outer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 660])
        layout.addWidget(splitter, 1)
        self.send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.composer)
        self.send_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.send_shortcut.activated.connect(self.submit)
        self.enter_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), self.composer)
        self.enter_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.enter_shortcut.activated.connect(self.submit)
        worker.result.connect(self.handle_result)
        self._update_controls()
        theme_signals(QApplication.instance()).changed.connect(self._refresh_theme)

    def ai_context(self):
        """Capture an insertion token without exposing thumbnail/attachment metadata."""
        if (not self.account_known or not self.account_id or not self.selected_device_id or
                not self.current_thread or self.current_thread[0] != "history" or self._current_pending()):
            return None
        thread = self._threads.get(self.current_thread)
        if not thread:
            return None
        token = {"account_id": self.account_id, "device_id": self.selected_device_id,
            "thread": list(self.current_thread), "recipient": self._route(),
            "draft": self.composer.toPlainText(),
            "composer_version": self._drafts.get(self._key(), ("", 0))[1],
            "context_version": self._render_generation,
            "message_ids": [item.get("id") for item in thread["messages"][-100:]]}
        device = next((item for item in self.devices if item["id"] == self.selected_device_id), {})
        reason = ''
        if not self._route() or not E164.fullmatch(self._route()):
            reason = 'Group or unknown senders are read-only. You can copy this draft; choose New message with an explicit number to send.'
        elif not device.get('active') or not device.get('can_sms'):
            reason = 'This phone is history only. You can copy this draft; choose an active SMS-capable phone to send.'
        elif not self.capabilities.get('features', {}).get('send_sms') or not self.capabilities.get('send_ready'):
            reason = self.capabilities.get('send_unavailable_reason') or 'SMS sending is currently unavailable. You can copy this draft.'
        return {"account_id": self.account_id, "device_id": self.selected_device_id,
            "conversation_id": self.current_thread[1], "token": token,
            "can_reply": not reason, "reply_unavailable_reason": reason}

    def insert_ai_draft(self, text, token):
        """Only a still-current explicit insertion can replace the captured draft."""
        context = self.ai_context()
        if (not context or context["token"] != token or not isinstance(text, str) or
                not text.strip() or len(text) > 65536 or self._current_pending()):
            return False
        self.composer.setPlainText(text)
        self.composer.setFocus()
        return True

    @staticmethod
    def _thread_recipients(thread):
        messages = thread.get("messages", [])
        recipients = next((item.get("recipients") for item in reversed(messages) if item.get("recipients")), [])
        if recipients:
            return recipients
        return [{"address": address} for address in sorted({str(address) for item in messages for address in item.get("addresses", [])})]

    def _media_validator(self, generation=None):
        page_ref = weakref.ref(self)
        def valid():
            page = page_ref()
            return (page is not None and not sip.isdeleted(page) and
                (page._avatar_key == page._key() if generation is None else page._render_generation == generation))
        return valid

    def _remote_avatar_loaded(self, image):
        if self._avatar_key != self._key():
            return
        reference = self.avatar_remote.reference
        if reference.get('kind') != 'contact':
            return
        # Retain only small native avatars independently of the full-image cache.
        # Account + URL revision also survives a new message with the same photo.
        key = (reference['account_id'], reference['revision'])
        pixmap = QPixmap.fromImage(image).scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        icon = QIcon(pixmap)
        self._contact_icons[key] = icon
        self._contact_icons.move_to_end(key)
        while len(self._contact_icons) > 256:
            self._contact_icons.popitem(last=False)
        self.avatar.setPixmap(icon.pixmap(36, 36))
        for index in range(self.threads.count()):
            item = self.threads.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == self.current_thread:
                item.setIcon(icon)
        self.avatar_remote.hide()

    def _contact_reference(self, thread):
        recipients = self._thread_recipients(thread)
        reference = None
        if len(recipients) == 1 and recipients[0].get('image_url') and inline_image(recipients[0].get('thumbnail')) is None:
            message = next((item for item in reversed(thread.get('messages', []))
                if len(item.get('recipients', [])) == 1 and item['recipients'][0].get('image_url') == recipients[0]['image_url']), None)
            if message and message.get('id') is not None:
                reference = {'account_id': self.account_id, 'kind': 'contact', 'message_id': message['id'],
                    'index': 0, 'revision': attachment_revision(recipients[0]['image_url'])}
        return reference

    def _cached_contact_icon(self, reference):
        if not reference:
            return None
        key = (reference['account_id'], reference['revision'])
        icon = self._contact_icons.get(key)
        if icon is not None:
            self._contact_icons.move_to_end(key)
        return icon

    def _thread_icon(self, thread, title):
        cached = self._cached_contact_icon(self._contact_reference(thread))
        return cached if cached is not None else contact_icon(self._thread_recipients(thread), title)

    def _sync_remote_avatar(self, thread):
        self._avatar_key = self._key()
        reference = self._contact_reference(thread)
        changed = reference != self.avatar_remote.reference
        self.avatar_remote.set_reference(reference)
        if reference:
            if self.avatar_remote.image is not None:
                self._remote_avatar_loaded(self.avatar_remote.image)
            elif self._cached_contact_icon(reference) is not None:
                self.avatar_remote.hide()
            elif changed:
                self.avatar_remote.status.setText('Contact image · load to display')
                self.avatar_remote.load_button.setText('Load contact image')

    def _refresh_theme(self):
        # Global semantic QSS repaints existing bubbles without rebuilding the
        # timeline, so drafts, selections, and scroll anchors remain intact.
        self.refresh_button.setIcon(make_icon("refresh", colors()["text"], 14))
        for index in range(self.threads.count()):
            row = self.threads.item(index)
            key = row.data(Qt.ItemDataRole.UserRole)
            thread = self._threads.get(key, {})
            row.setIcon(self._thread_icon(thread, self._thread_title(key, thread)))
        thread = self._threads.get(self.current_thread, {})
        self.avatar.setPixmap(self._thread_icon(thread, self.heading.text()).pixmap(36, 36))
        self._sync_remote_avatar(thread)
        self.threads.viewport().update()

    def _key(self):
        if self.current_thread is None:
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
            self._filter_contexts[self.filter_device_id] = self.current_thread

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
        self.current_thread = ("new", text.strip(), self.selected_device_id)
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
        request_id = data.get("sms_request_id")
        if not request_id or request_id == self.selection_request_id:
            snapshots = data.get("sms_by_device")
            if not isinstance(snapshots, dict):
                snapshots = {data.get("sms_device_id"): data.get("sms")}
            for device_id, sms in snapshots.items():
                if not device_id or not isinstance(sms, dict) or not self._part_current(sms):
                    continue
                previous = self.sms_cache.get(device_id, {})
                messages = [dict(item) for item in sms.get("messages", [])
                    if self._part_current(item) and item.get("device_id", device_id) == device_id]
                sync = dict(sms.get("sync", {}))
                if not messages and (sync.get("error") or sms.get("read_error")) and previous.get("messages"):
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
        if hasattr(self.worker, '_media_cache'):
            self.worker._media_cache.clear()
            self.worker._media_cache_account = account
        self.account_id, self.account_known = account, True
        self.devices, self.actions, self.sms_cache = [], [], {}
        self._drafts.clear()
        self._new_recipients.clear()
        self._contexts.clear()
        self._pending.clear()
        self._pending_state.clear()
        self._uncertain_requests.clear()
        self.selected_device_id = None
        self.filter_device_id = None
        self._filter_contexts.clear()
        # The worker starts in All devices and resets there with each account.
        # Opening the page/setup must not enqueue a redundant read command.
        self.selection_request_id = None
        if (getattr(self.worker, "device_id", None) is not None and
                getattr(self.worker, "account_id", None) == account):
            self.selection_request_id = str(uuid.uuid4())
            self.worker.request("select_device", {"device_id": None,
                "request_id": self.selection_request_id, "account_id": account})
        self.current_thread = None
        self._threads = {}
        self._contact_icons.clear()
        self._loading = True
        self.composer.clear()
        self.recipient.clear()
        self._loading = False
        self._timeline_signature = None

    def _eligible_devices(self):
        known = {item["id"]: dict(item) for item in self.devices}
        retained = set(self.sms_cache) | {item.get("device_id") for item in self.actions if item.get("kind") == "sms"}
        for device_id in retained - set(known):
            if device_id:
                known[device_id] = {"id": device_id, "nickname": device_id, "active": False}
        return [item for item in known.values() if item.get("can_sms") or
            item.get("history", {}).get("count") or item["id"] in retained]

    def _device_label(self, device_id):
        if not device_id:
            return "Choose a sending phone"
        devices = self._eligible_devices()
        device = next((item for item in devices if item["id"] == device_id), {"id": device_id})
        name = str(device.get("nickname") or device_id)
        duplicates = [item for item in devices if str(item.get("nickname") or item["id"]) == name]
        if len(duplicates) > 1:
            # Full IDs ensure even IDs with a shared suffix remain distinguishable.
            name += " · " + device_id
        if not (device.get("active") and device.get("can_sms")):
            name += " · history only"
        return name

    def _populate_phones(self):
        devices = self._eligible_devices()
        self.phone.blockSignals(True)
        self.phone.clear()
        self.phone.addItem("All devices", None)
        self.send_from.blockSignals(True)
        self.send_from.clear()
        self.send_from.addItem("Send from: choose a phone…", None)
        for device in devices:
            label = self._device_label(device["id"])
            self.phone.addItem(label, device["id"])
            self.phone.setItemData(self.phone.count() - 1, device["id"], Qt.ItemDataRole.ToolTipRole)
            if device.get("active") and device.get("can_sms"):
                self.send_from.addItem("Send from: " + label, device["id"])
                self.send_from.setItemData(self.send_from.count() - 1, device["id"], Qt.ItemDataRole.ToolTipRole)
        self.phone.setCurrentIndex(max(0, self.phone.findData(self.filter_device_id)))
        self.send_from.setCurrentIndex(max(0, self.send_from.findData(self.selected_device_id)))
        self.phone.blockSignals(False)
        self.send_from.blockSignals(False)

    def _phone_changed(self):
        self._select_phone(self.phone.currentData())

    def _select_phone(self, device_id):
        if device_id == self.filter_device_id:
            return
        self._save_draft()
        self.filter_device_id = device_id
        context = self._filter_contexts.get(device_id) or self._contexts.get((self.account_id, device_id))
        self.current_thread = context
        self.selected_device_id = context[2] if context else device_id
        if context and context[0] == "new":
            self._loading = True
            self.recipient.setText(context[1])
            self._loading = False
        self._timeline_signature = None
        self._rebuild_threads(new_context=True)
        self._load_draft()
        self._update_sync()
        self.selection_request_id = str(uuid.uuid4())
        self.worker.request("select_device", {"device_id": device_id,
            "request_id": self.selection_request_id, "account_id": self.account_id})

    def _sender_changed(self):
        if not self.current_thread or self.current_thread[0] != "new":
            return
        self._save_draft()
        old_key = self._key()
        self.selected_device_id = self.send_from.currentData()
        self.current_thread = ("new", self.recipient.text().strip(), self.selected_device_id)
        if old_key and old_key[1] is None and self._key() not in self._drafts:
            self._drafts[self._key()] = self._drafts.get(old_key, ("", 0))
        self._new_recipients[(self.account_id, self.selected_device_id)] = self.recipient.text()
        self._load_draft()
        self._render_timeline(new_context=True)

    def refresh(self):
        if not self.account_known or not self.capabilities.get("features", {}).get("sms_history"):
            return
        devices = [self.filter_device_id] if self.filter_device_id else [item["id"] for item in
            self._eligible_devices() if item.get("active") and item.get("can_sms")]
        for device_id in devices:
            self.worker.request("refresh_sms", {"device_id": device_id,
                "request_id": str(uuid.uuid4()), "account_id": self.account_id})

    def _rebuild_threads(self, new_context=False):
        threads = {}
        messages = [dict(message, device_id=device_id) for device_id, snapshot in self.sms_cache.items()
            if self.filter_device_id is None or device_id == self.filter_device_id
            for message in snapshot.get("messages", [])]
        for message in messages:
            conversation = message.get("conversation_id")
            if conversation is None:
                continue
            key = ("history", str(conversation), message["device_id"])
            threads.setdefault(key, {"messages": [], "actions": []})["messages"].append(message)
        for thread in threads.values():
            thread["messages"].sort(key=lambda item: (_order(item.get("timestamp")), str(item.get("id", ""))))
            thread["recipient"] = _recipient(thread["messages"])
        for action in self.actions:
            # Accepted requests remain in Message activity. They are not phone
            # messages, and keeping them here duplicates the synchronized SMS.
            # In particular, do not infer delivery by matching body/time text.
            if (action.get("kind") != "sms" or (self.filter_device_id is not None and action.get("device_id") != self.filter_device_id)
                    or action.get("state") == "upstream_accepted"):
                continue
            recipient = action.get("recipient")
            matches = [key for key, thread in threads.items() if key[0] == "history" and thread["recipient"] == recipient and recipient and key[2] == action.get("device_id")]
            key = matches[0] if len(matches) == 1 else ("local", str(recipient or "Unknown recipient"), action.get("device_id"))
            threads.setdefault(key, {"messages": [], "actions": [], "recipient": None})["actions"].append(action)
        self._threads = threads
        if self.current_thread and self.current_thread[0] == "local" and self.current_thread not in threads:
            # The last local request can finish before phone history arrives.
            # Keep focus on the same recipient, rather than jumping to another
            # person's conversation or leaving an empty local-request thread.
            recipient = self.current_thread[1]
            matches = [key for key, thread in threads.items()
                       if key[0] == "history" and recipient and thread.get("recipient") == recipient and key[2] == self.selected_device_id]
            self.current_thread = matches[0] if len(matches) == 1 else ("new", recipient if E164.fullmatch(recipient) else "", self.selected_device_id)
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
        scroll = self.threads.verticalScrollBar()
        old_scroll = scroll.value()
        anchor = self.threads.itemAt(0, 0)
        anchor_key = anchor.data(Qt.ItemDataRole.UserRole) if anchor else None
        anchor_top = self.threads.visualItemRect(anchor).top() if anchor else 0
        rows = {self.threads.item(index).data(Qt.ItemDataRole.UserRole): self.threads.item(index)
            for index in range(self.threads.count())}
        desired = []
        query = self.search.text().casefold()
        def recent(entry):
            return max([_order(item.get("timestamp")) for item in entry[1]["messages"]] +
                       [_order(item.get("created_at")) for item in entry[1]["actions"]] + [0])
        for key, thread in sorted(self._threads.items(), key=recent, reverse=True):
            title = self._thread_title(key, thread)
            records = thread["messages"]
            preview = (str(records[-1].get("body", "")) or ("Image attachment" if records[-1].get("image_urls") else "")) if records else "Local request · review in Activity"
            searchable = title + " " + self._device_label(key[2]) + " " + " ".join(str(item.get("body", "")) for item in records)
            if query and query not in searchable.casefold():
                continue
            last_ts = records[-1].get("timestamp") if records else (thread["actions"][-1].get("created_at") if thread["actions"] else None)
            desired.append((key, title + "\n" + self._device_label(key[2]) + "\n" + preview[:90].replace("\n", " "), self._thread_icon(thread, title), {
                "title": title,
                "device": self._device_label(key[2]),
                "preview": preview.replace("\n", " "),
                "last_timestamp": last_ts,
            }))
        blocked = self.threads.blockSignals(True)
        try:
            retained = {}
            for position, (key, text, icon, metadata) in enumerate(desired):
                item = rows.pop(key, None)
                if item is None:
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, key)
                    self.threads.insertItem(position, item)
                elif self.threads.row(item) != position:
                    self.threads.takeItem(self.threads.row(item))
                    self.threads.insertItem(position, item)
                if item.text() != text:
                    item.setText(text)
                item.setIcon(icon)
                item.setToolTip("<qt>" + escape(self._device_label(key[2]) + " · " + str(key[2])) + "</qt>")
                if item.data(Qt.ItemDataRole.UserRole + 1) != metadata:
                    item.setData(Qt.ItemDataRole.UserRole + 1, metadata)
                retained[key] = item
            for item in rows.values():
                self.threads.takeItem(self.threads.row(item))
            current = retained.get(self.current_thread)
            if self.threads.currentItem() is not current:
                self.threads.setCurrentItem(current)
            # Complete Qt's pending row layout before restoring the visible anchor.
            self.threads.doItemsLayout()
            if anchor_key in retained:
                new_top = self.threads.visualItemRect(retained[anchor_key]).top()
                scroll.setValue(scroll.value() + new_top - anchor_top)
            else:
                scroll.setValue(old_scroll)
        finally:
            self.threads.blockSignals(blocked)

    def _thread_changed(self, item, previous=None):
        if item is not None:
            self._activate_thread(item.data(Qt.ItemDataRole.UserRole), True)

    def _activate_thread(self, key, new_context=False):
        # Compatibility callers must already have an explicit selected route.
        if len(key) == 2:
            if not self.selected_device_id:
                return
            key = (*key, self.selected_device_id)
        if key == self.current_thread:
            return
        self._save_draft()
        self.current_thread = key
        self.selected_device_id = key[2]
        self.recipient.hide()
        self._load_draft()
        selected = next((self.threads.item(index) for index in range(self.threads.count())
            if self.threads.item(index).data(Qt.ItemDataRole.UserRole) == key), None)
        if self.threads.currentItem() is not selected:
            blocked = self.threads.blockSignals(True)
            try:
                self.threads.setCurrentItem(selected)
            finally:
                self.threads.blockSignals(blocked)
        self._render_timeline(new_context=True)

    def start_new(self):
        self._save_draft()
        self.selected_device_id = self.filter_device_id
        recipient = self._new_recipients.get((self.account_id, self.selected_device_id), "")
        self.current_thread = ("new", recipient.strip(), self.selected_device_id)
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
        if self.filter_device_id is not None and self.filter_device_id != action.get("device_id"):
            self.phone.setCurrentIndex(0)
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
        if not self.selected_device_id:
            return "Choose a Send from phone before reviewing this message."
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
        self.refresh_button.setText("Refresh all devices" if self.filter_device_id is None else "Refresh")
        self.refresh_button.setEnabled(bool(self._eligible_devices()) and bool(self.capabilities.get("features", {}).get("sms_history")))
        self.send_from.setEnabled(pending is None)
        self.ai_context_changed.emit()

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
        sync = dict(self.sms_cache.get(self.filter_device_id, {}).get("sync", {}))
        read_error = self.sms_cache.get(self.filter_device_id, {}).get("read_error")
        if read_error:
            sync["error"] = read_error
        state = sync.get("state")
        last = _time(sync.get("last_success_at"))
        unavailable = sync.get("unavailable_count", 0)
        unavailable = unavailable if isinstance(unavailable, int) and not isinstance(unavailable, bool) and unavailable > 0 else 0
        failed = bool(sync.get("error")) or state == "error"
        label = {"partial": "Partial", "refreshing": "Refreshing", "queued": "Refresh queued",
            "locked": "History locked", "rate_limited": "Rate limited"}.get(state,
                "Refresh unavailable" if failed else "Stored history")
        text = (last.strftime("%d %b, %H:%M") if last else "Not yet synced") + " · " + label
        details = ["Stored phone history.", "Last successful sync: " + last.strftime("%d %b %Y, %H:%M") + "."
            if last else "No successful history sync has been recorded."]
        if state == "partial":
            details.append("Available conversations synced; some history unavailable. Cached messages are retained.")
        elif failed or state in {"locked", "rate_limited"}:
            details.append("Refresh unavailable; cached history kept.")
        elif state in {"refreshing", "queued"}:
            details.append("A history refresh is " + ("in progress" if state == "refreshing" else "queued") + "; cached messages remain available.")
        if unavailable:
            text += " · " + str(unavailable) + " unavailable"
            details.append(str(unavailable) + (" conversation unavailable." if unavailable == 1 else " conversations unavailable."))
        if state:
            details.append("Sync state: " + str(state).replace("_", " ") + ".")
        if sync.get("error"):
            details.append("Details: " + str(sync["error"]))
        if self.filter_device_id is None:
            entries = self._eligible_devices()
            problems = []
            for device in entries:
                cached = self.sms_cache.get(device["id"], {})
                status = cached.get("sync", {})
                error = cached.get("read_error") or status.get("error")
                if error or status.get("state") in {"error", "locked", "rate_limited", "partial"}:
                    problems.append(self._device_label(device["id"]) + ": " + str(error or status.get("state")))
            text = "All devices · " + str(len(entries)) + " phones"
            if problems:
                text += " · " + str(len(problems)) + " need attention"
            details = ["Stored history across all phones. Conversations stay separate for each device."] + problems
            if problems:
                details.append("Cached messages are retained; other phones remain available.")
        description = "\n".join(details)
        self.sync_status.setText(text)
        self.sync_status.setAccessibleDescription(description)
        # A controlled wrapper prevents Qt from interpreting provider detail as
        # tooltip markup; accessibility keeps the complete plain-text version.
        self.sync_status.setToolTip("<qt>" + escape(description).replace("\n", "<br>") + "</qt>")

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
        self.send_from.setVisible(is_new)
        self.send_from.blockSignals(True)
        self.send_from.setCurrentIndex(max(0, self.send_from.findData(self.selected_device_id)))
        self.send_from.blockSignals(False)
        self.heading.setText("New message" if is_new else self._thread_title(self.current_thread, thread) if self.current_thread in self._threads else "Your SMS conversations")
        self.avatar.setPixmap(self._thread_icon(thread, self.heading.text()).pixmap(36, 36))
        self._sync_remote_avatar(thread)
        route = thread.get("recipient")
        self.context_label.setText("Enter the complete phone number, including + and country code." if is_new else
            ("Phone history · " + route if route else "Phone history is read-only here. Use New message with an explicit number.") if thread["messages"] else
            "Local requests are separate from phone history. Review their exact payload in Activity.")
        if self.selected_device_id:
            self.context_label.setText(self._device_label(self.selected_device_id) + " · " + self.context_label.text())
        self.context_label.setWordWrap(True)
        self.context_label.setToolTip("<qt>" + escape(str(self.selected_device_id or "Choose a sending phone")) + "</qt>")
        signature = (self.account_id, self.selected_device_id, self.current_thread,
            self._device_label(self.selected_device_id) if self.selected_device_id else None, repr(thread))
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
            meta = plain_label(sender + " · " + self._device_label(self.selected_device_id) + " · " + (stamp.strftime("%H:%M") if stamp else "Unknown time"))
            meta.setWordWrap(True)
            meta.setObjectName("smsMeta")
            bubble_layout.addWidget(meta)
            body = MessageText(str(message.get("body") or ""))
            body.setAccessibleName("Outgoing SMS" if outgoing else "Incoming SMS")
            bubble_layout.addWidget(body)
            for index, unused in enumerate(message.get("image_urls", [])[:16]):
                media = RemoteImage(self.worker, {"account_id": self.account_id,
                    "kind": "sms", "message_id": message["id"], "index": index,
                    "revision": attachment_revision(unused)},
                    valid=self._media_validator(generation))
                media.geometry_changing.connect(self._media_anchor)
                media.geometry_changed.connect(self._restore_media_anchor)
                bubble_layout.addWidget(media)
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
            details = self._device_label(action.get("device_id")) + " · To " + str(action.get("recipient") or "Unknown recipient")
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

    def _media_anchor(self):
        scroll = self.timeline.verticalScrollBar()
        self._image_anchor = (self._anchor(), scroll.value(), scroll.maximum() - scroll.value() < 48)

    def _restore_media_anchor(self):
        state = getattr(self, '_image_anchor', None)
        generation = self._render_generation
        def restore():
            if sip.isdeleted(self) or generation != self._render_generation or state is None:
                return
            self.timeline_layout.activate()
            anchor, old_value, near_bottom = state
            scroll = self.timeline.verticalScrollBar()
            row = next((row for key, row in self._rows if anchor and key == anchor[0]), None)
            scroll.setValue(scroll.maximum() if near_bottom else row.y() - anchor[1] if row else old_value)
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
