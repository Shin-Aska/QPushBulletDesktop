"""Native action composition and exact-payload approval. The core owns sending."""
from html import escape
import re
from urllib.parse import urlsplit
import uuid

from PyQt6.QtCore import Qt, pyqtSignal
from .theme import primary
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QTextEdit, QVBoxLayout)

PLAINTEXT_NOTICE = "Pushbullet can process this content. SMS carrier delivery is not end-to-end encrypted."
STATE_LABELS = {
    "pending_approval": "Pending approval", "ready": "Approved · queued", "submitting": "Submitting to Pushbullet",
    "upstream_accepted": "Accepted by Pushbullet · delivery not confirmed", "failed": "Failed", "uncertain": "Uncertain · do not retry automatically",
    "expired": "Expired", "cancelled": "Cancelled",
}


def plain_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def eligible_devices(devices, kind):
    flag = "can_sms" if kind == "sms" else "can_push"
    return [device for device in devices if device.get("active") and device.get(flag)]


def action_preview(action, devices):
    device = next((item for item in devices if item["id"] == action["device_id"]), {})
    lines = [STATE_LABELS.get(action["state"], action["state"]),
             f"Kind: {action['kind'].title()}",
             f"Device: {device.get('nickname') or '(unnamed)'} · {action['device_id']}",
             f"Recipient: {action.get('recipient') or '(selected device)'}",
             "Transport: Not end-to-end encrypted; sent over HTTPS to Pushbullet", ""]
    if action.get('title'):
        lines.extend(['Title:', action['title'], ''])
    lines.extend(['Body:', action.get('body') or ''])
    if action.get('url'):
        lines.extend(['', 'URL:', action['url']])
    lines.extend(['', 'Advanced details', f"Action #{action['id']} · revision {action['revision']}",
                  f"Requested by: {action.get('requested_by', 'unknown')}",
                  f"Expires: {action['expires_at']}", f"Fingerprint: {action['fingerprint']}"])
    if action.get("upstream_id"):
        lines.extend(["", f"Pushbullet acceptance ID: {action['upstream_id']} (not a delivery receipt)"])
    if action.get("error_code") or action.get("error_message"):
        lines.extend(["", f"Error: {action.get('error_code') or ''}", action.get("error_message") or ""])
    if action["state"] == "uncertain":
        lines.extend(["", "Pushbullet may already have accepted this message. Check the destination before creating another action. No automatic retry is offered."])
    return "\n".join(lines)


class ComposeDialog(QDialog):
    """Save a pending action or edit one revision; never approve from this dialog."""
    saved = pyqtSignal(dict)

    def __init__(self, worker, devices, parent=None, action=None, account_id=None, kinds=None):
        super().__init__(parent)
        self.worker, self.devices = worker, list(devices)
        self.account_id = account_id
        self.action = dict(action) if action else None
        self.request_id = str(uuid.uuid4())
        self.pending_payload = None
        self.busy = False
        self.setWindowTitle("Edit action" if action else "Compose · save for review")
        self.resize(600, 650)
        layout = QVBoxLayout(self)
        heading = plain_label("Edit saved action" if action else "Compose a message")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        layout.addWidget(plain_label("Choose an explicit destination. Saving creates a pending action; approval happens separately in Message activity."))
        layout.addWidget(plain_label("Not end-to-end encrypted; sent over HTTPS to Pushbullet"))
        layout.addWidget(plain_label(PLAINTEXT_NOTICE))
        form = QFormLayout()
        self.kind = QComboBox()
        for value in (kinds or ("note", "link", "sms")):
            self.kind.addItem(value.title(), value)
        self.device = QComboBox()
        self.recipient = QLineEdit()
        self.recipient.setMaxLength(32)
        self.recipient.setPlaceholderText("+639171234567 — explicit E.164 phone number")
        self.title = QLineEdit()
        self.title.setMaxLength(4096)
        self.body = QTextEdit()
        self.body.setAcceptRichText(False)
        self.body.setPlaceholderText("Message content")
        self.url = QLineEdit()
        self.url.setMaxLength(8192)
        self.url.setPlaceholderText("https://example.com")
        self.recipient_label, self.title_label, self.url_label = QLabel("Phone number"), QLabel("Title"), QLabel("URL")
        form.addRow("Kind", self.kind)
        form.addRow("Destination device", self.device)
        form.addRow(self.recipient_label, self.recipient)
        form.addRow(self.title_label, self.title)
        form.addRow("Body", self.body)
        form.addRow(self.url_label, self.url)
        layout.addLayout(form)
        self.validation = plain_label()
        layout.addWidget(self.validation)
        self.request_status = plain_label()
        layout.addWidget(self.request_status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.save_button = primary(buttons.addButton("Save for review", QDialogButtonBox.ButtonRole.ActionRole))
        self.save_button.clicked.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self.update_devices)
        for widget in (self.device,):
            widget.currentIndexChanged.connect(self.validate)
        for widget in (self.recipient, self.title, self.url):
            widget.textChanged.connect(self.validate)
        self.body.textChanged.connect(self.validate)
        self.update_devices()
        if action:
            self.kind.setCurrentIndex(self.kind.findData(action["kind"]))
            if self.device.findData(action["device_id"]) < 0:
                self.device.addItem(action["device_id"] + " · unavailable", action["device_id"])
            self.device.setCurrentIndex(self.device.findData(action["device_id"]))
            self.recipient.setText(action.get("recipient") or "")
            self.title.setText(action.get("title") or "")
            self.body.setPlainText(action.get("body") or "")
            self.url.setText(action.get("url") or "")
            self.request_status.setText(f"Editing action #{action['id']} revision {action['revision']}. Saving invalidates its previous approval; expiry is preserved.")
        worker.result.connect(self.handle_result)
        self.finished.connect(lambda: worker.result.disconnect(self.handle_result))
        self.validate()

    def update_devices(self):
        selected = self.device.currentData()
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("Select a destination…", None)
        for device in eligible_devices(self.devices, self.kind.currentData()):
            self.device.addItem(f"{device.get('nickname') or '(unnamed)'} · {device['id']}", device["id"])
        index = self.device.findData(selected)
        self.device.setCurrentIndex(index if index >= 0 else 0)
        self.device.blockSignals(False)
        sms, link = self.kind.currentData() == "sms", self.kind.currentData() == "link"
        for widget in (self.recipient_label, self.recipient):
            widget.setVisible(sms)
        for widget in (self.title_label, self.title):
            widget.setVisible(not sms)
        for widget in (self.url_label, self.url):
            widget.setVisible(link)
        self.validate()

    def validation_message(self):
        kind = self.kind.currentData()
        if self.device.currentData() not in {device["id"] for device in eligible_devices(self.devices, kind)}:
            return "Select an active device supporting this message kind. Device registration does not imply it is online."
        if kind == "sms" and not re.fullmatch(r"\+[1-9][0-9]{1,14}", self.recipient.text().strip()):
            return "Enter an explicit E.164 phone number, including + and country code. Numbers are never inferred from notifications."
        if len(self.body.toPlainText()) > 65536:
            return "The message body is limited to 65,536 characters."
        if kind == "sms" and not self.body.toPlainText().strip():
            return "Enter a message body."
        if kind == "note" and not (self.title.text().strip() or self.body.toPlainText().strip()):
            return "Enter a note title or message body."
        if kind == "link":
            try:
                url = urlsplit(self.url.text().strip())
                valid = url.scheme in {"https", "http"} and bool(url.hostname) and not url.username and not url.password
            except ValueError:
                valid = False
            if not valid:
                return "Enter an http or https URL without embedded credentials."
        return ""

    def validate(self):
        message = self.validation_message()
        self.validation.setText(message)
        self.save_button.setEnabled(not self.busy and (self.pending_payload is not None or not message))

    def payload(self):
        kind = self.kind.currentData()
        return {"kind": kind, "transport": "provider_plaintext", "device_id": self.device.currentData(),
                "recipient": self.recipient.text().strip() if kind == "sms" else None,
                "title": self.title.text() if kind != "sms" else "", "body": self.body.toPlainText(),
                "url": self.url.text().strip() if kind == "link" else None}

    def set_fields_enabled(self, enabled):
        for widget in (self.kind, self.device, self.recipient, self.title, self.body, self.url):
            widget.setEnabled(enabled)

    def submit(self):
        if self.busy or (self.pending_payload is None and self.validation_message()):
            return
        if self.pending_payload is None:
            self.pending_payload = self.payload()
            if self.action:
                self.pending_payload["expected_revision"] = self.action["revision"]
            else:
                self.pending_payload.update(client_request_id=self.request_id, expires_in_seconds=3600)
        self.busy = True
        self.set_fields_enabled(False)
        self.request_status.setText(f"Saving request {self.request_id}… No message is sent at this stage.")
        self.validate()
        command = "edit_action" if self.action else "create_action"
        value = {"request_id": self.request_id, "payload": dict(self.pending_payload)}
        if self.account_id is not None:
            value["account_id"] = self.account_id
        if self.action:
            value["action_id"] = self.action["id"]
        self.worker.request(command, value)

    def handle_result(self, result):
        if result.get("request_id") != self.request_id:
            return
        self.busy = False
        if self.account_id is not None and result.get("account_id") != self.account_id:
            self.request_status.setText("The account context changed. Close this dialog and review message activity.")
            self.busy = True
            self.validate()
            return
        if "data" in result:
            self.saved.emit(result["data"])
            self.accept()
            return
        self.request_status.setText(result["error"] + f"\nRequest ID: {self.request_id}")
        if result.get("ambiguous"):
            if self.action:
                self.save_button.setText("Check message activity before editing again")
                self.busy = True  # Never repeat an ambiguous PATCH against an old revision.
            else:
                self.save_button.setText("Reconcile same request")
                # Retain exact original fields and UUID, including expiry duration.
        elif result.get("status") == 409:
            self.busy = True
            self.save_button.setText("Close and review refreshed action")
        else:
            self.pending_payload = None
            self.set_fields_enabled(True)
        self.validate()


class ReviewDialog(QDialog):
    """Approve the exact revision and fingerprint displayed, with explicit transport consent."""
    edit_requested = pyqtSignal(dict)

    def __init__(self, worker, action, devices, capabilities, parent=None):
        super().__init__(parent)
        self.worker, self.action, self.devices = worker, dict(action), list(devices)
        self.capabilities = capabilities
        self.request_id = None
        self.busy = False
        self.awaiting_refresh = False
        self.setWindowTitle(f"Review action #{action['id']}")
        self.resize(680, 730)
        layout = QVBoxLayout(self)
        heading = plain_label("Review exact message")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        layout.addWidget(plain_label("Approval applies only to the destination and content shown below."))
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setAccessibleName("Exact action payload")
        layout.addWidget(self.preview, 1)
        self.advanced_toggle = QPushButton('▸  Advanced details')
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced = QTextBrowser()
        self.advanced.setAccessibleName('Action revision, fingerprint and expiry')
        self.advanced.setMaximumHeight(145)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)
        disclosure = plain_label('Sent over HTTPS to Pushbullet · Not end-to-end encrypted.\n' + PLAINTEXT_NOTICE)
        disclosure.setObjectName('errorBanner')
        layout.addWidget(disclosure)
        self.ack = QCheckBox("I understand and allow Pushbullet to process this message.")
        self.ack.setChecked(False)
        layout.addWidget(self.ack)
        self.readiness = plain_label()
        layout.addWidget(self.readiness)
        self.feedback = plain_label()
        layout.addWidget(self.feedback)
        row = QHBoxLayout()
        self.edit_button = QPushButton("Edit…")
        self.cancel_button = QPushButton("Cancel action")
        self.approve_button = primary(QPushButton("Approve and send"))
        close_button = QPushButton("Close")
        for button in (self.edit_button, self.cancel_button, self.approve_button, close_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.edit_button.clicked.connect(self.edit)
        self.cancel_button.clicked.connect(lambda: self.mutate("cancel_action"))
        self.approve_button.clicked.connect(lambda: self.mutate("approve_action"))
        close_button.clicked.connect(self.reject)
        self.ack.toggled.connect(self.update_controls)
        worker.result.connect(self.handle_result)
        worker.snapshot.connect(self.handle_snapshot)
        self.finished.connect(self.disconnect_worker)
        self.render()

    def disconnect_worker(self):
        self.worker.result.disconnect(self.handle_result)
        self.worker.snapshot.disconnect(self.handle_snapshot)

    def update_controls(self):
        editable = self.action["state"] in {"pending_approval", "ready"} and not self.busy and not self.awaiting_refresh
        self.edit_button.setEnabled(editable)
        self.cancel_button.setEnabled(editable)
        pending = editable and self.action["state"] == "pending_approval"
        self.ack.setEnabled(pending)
        destination_ready = self.action["device_id"] in {device["id"] for device in eligible_devices(self.devices, self.action["kind"])}
        self.approve_button.setEnabled(pending and self.ack.isChecked() and bool(self.capabilities.get("send_ready")) and destination_ready)
        self.readiness.setText("The original destination is unavailable or no longer supports this action. Choose Edit to explicitly select a destination." if not destination_ready else "Ready to submit after approval." if self.capabilities.get("send_ready") else (self.capabilities.get("send_unavailable_reason") or "Sending is currently unavailable. Check provider connection and credentials."))

    def render(self):
        action = self.action
        device = next((item for item in self.devices if item['id'] == action['device_id']), {})
        document = '<p style="color:#68766e">' + escape(STATE_LABELS.get(action['state'], action['state'])) + '</p><h2>' + escape('SMS' if action['kind'] == 'sms' else action['kind'].title()) + ' to ' + escape(action.get('recipient') or device.get('nickname') or action['device_id']) + '</h2><p>Via ' + escape(device.get('nickname') or '(unnamed)') + ' · ' + escape(action['device_id']) + '</p><hr>'
        if action.get('title'):
            document += '<h3>' + escape(action['title']) + '</h3>'
        document += '<p>' + escape(action.get('body') or '').replace('\n', '<br>') + '</p>'
        if action.get('url'):
            document += '<p>' + escape(action['url']) + '</p>'
        document += '<hr><p>Expires: ' + escape(action['expires_at']) + '</p><p style="color:#68766e; font-size:12px">Not end-to-end encrypted; sent over HTTPS to Pushbullet</p>'
        if action['state'] == 'uncertain':
            document += '<p style="background-color:#fff0d5">Pushbullet may already have accepted this message. Check the destination before creating another action. No automatic retry is offered.</p>'
        if action.get('error_message'):
            document += '<p>' + escape(action['error_message']) + '</p>'
        self.preview.setHtml(document)
        self.advanced.setPlainText(action_preview(action, self.devices))
        self.update_controls()

    def handle_snapshot(self, data):
        self.capabilities = data.get("capabilities", self.capabilities)
        self.devices = data.get("devices", {}).get("devices", self.devices)
        latest = next((action for action in data.get("actions", []) if action["id"] == self.action["id"]), None)
        if latest:
            if latest != self.action:
                self.ack.setChecked(False)
                self.feedback.setText("The action changed. Review the current payload and state before continuing.")
            self.action = dict(latest)
            self.awaiting_refresh = False
        self.render()

    def edit(self):
        if not self.edit_button.isEnabled():
            return
        self.edit_requested.emit(dict(self.action))
        self.accept()

    def mutate(self, command):
        button = self.approve_button if command == "approve_action" else self.cancel_button
        if not button.isEnabled():
            return
        payload = {"revision": self.action["revision"]}
        if command == "approve_action":
            payload.update(fingerprint=self.action["fingerprint"], allow_provider_plaintext=True)
        self.request_id = str(uuid.uuid4())
        self.busy = True
        self.feedback.setText("Submitting approval…" if command == "approve_action" else "Cancelling pending action…")
        value = {"request_id": self.request_id, "action_id": self.action["id"], "payload": payload}
        if "account_id" in self.capabilities:
            value["account_id"] = self.capabilities["account_id"]
        self.worker.request(command, value)
        self.update_controls()

    def handle_result(self, result):
        if result.get("request_id") != self.request_id:
            return
        self.busy = False
        self.ack.setChecked(False)
        if "data" in result:
            self.action = dict(result["data"])
            self.feedback.setText("Core state updated. Pushbullet acceptance is not a delivery receipt.")
        else:
            self.awaiting_refresh = True
            self.feedback.setText(result["error"])
        self.render()
