"""Automatic account digests and explicitly reviewed SMS reply drafts."""
import copy
import time
import uuid
from PyQt6.QtCore import QObject, QSizeF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QTextDocument, QTextFormat, QTextObjectInterface
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QComboBox, QLineEdit, QFormLayout, QTabWidget, QDialog, QMessageBox, QDialogButtonBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView

from .notification_content import NotificationBrowser, valid_http_url


def label(text=''):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


class AiPanel(QWidget):
    def __init__(self, ai_client, parent=None):
        super().__init__(parent)
        self.client = ai_client
        self.account_id = None
        self._known_account = False
        self._requests = {}
        self.context_id = None
        self.generated_text = None
        self.layout_box = QVBoxLayout(self)
        self.scope = label('Choose local text, then prepare a preview.')
        self.layout_box.addWidget(self.scope)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName('Exact text selected for OpenAI')
        self.preview.setPlaceholderText('The selected text appears here before you generate.')
        self.layout_box.addWidget(self.preview)
        self.disclosure = label('Selected text will be sent to OpenAI when you click Generate. OpenAI API usage may incur charges.')
        self.layout_box.addWidget(self.disclosure)
        controls = QHBoxLayout()
        self.prepare_button = QPushButton('Prepare preview')
        self.generate_button = QPushButton('Generate')
        self.generate_button.setEnabled(False)
        self.cancel_button = QPushButton('Cancel')
        controls.addWidget(self.prepare_button)
        controls.addWidget(self.generate_button)
        controls.addWidget(self.cancel_button)
        self.layout_box.addLayout(controls)
        self.feedback = label('' if ai_client else 'AI service unavailable. Reopen pbdesk with its private core connected.')
        self.layout_box.addWidget(self.feedback)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setAccessibleName('Generated text for review')
        self.layout_box.addWidget(self.output)
        self.generate_button.clicked.connect(self.generate)
        self.cancel_button.clicked.connect(self.cancel)
        self.prepare_button.setEnabled(ai_client is not None)
        if ai_client:
            ai_client.result.connect(self.result)

    def send(self, command, payload):
        if self.client is None:
            return None
        request_id = str(uuid.uuid4())
        self._requests[request_id] = command
        self.client.request(command, dict(payload, request_id=request_id))
        return request_id

    def clear(self):
        self.cancel()
        self.preview.clear()
        self.output.clear()
        self.generated_text = None
        self.scope.setText('Choose local text, then prepare a preview.')

    def cancel(self):
        if self.client:
            for request_id in list(self._requests):
                self.client.request('ai_cancel', {'target_id': request_id})
        self._requests.clear()
        self.context_id = None
        self.generate_button.setEnabled(False)
        self.prepare_button.setEnabled(self.client is not None)

    def apply_snapshot(self, data):
        caps = data.get('capabilities', data)
        account = caps.get('account_id')
        if self._known_account and account != self.account_id:
            self.clear()
        self.account_id = account
        self._known_account = True
        supported = caps.get('features', {}).get('ai', True)
        self.prepare_button.setEnabled(bool(self.client and supported))
        if not supported:
            self.cancel()
            self.prepare_button.setEnabled(False)
            self.feedback.setText('AI is unavailable in this core version. Restart the updated core and desktop.')

    def generate(self):
        if not self.context_id:
            return
        self.generate_button.setEnabled(False)
        self.prepare_button.setEnabled(False)
        self.generated_text = None
        self.output.clear()
        self.feedback.setText('Generating from the previewed text…')
        self.send('ai_generate', {'context_id': self.context_id})
        self.context_id = None

    def result(self, result):
        command = self._requests.pop(result.get('request_id'), None)
        if command is None:
            return
        self.prepare_button.setEnabled(self.client is not None)
        if not result.get('ok'):
            self.feedback.setText(result.get('error', 'The AI request could not finish.'))
            self.generate_button.setEnabled(False)
            return
        data = result.get('data', {})
        if data.get('account_id') != self.account_id:
            self.clear()
            self.feedback.setText('The account changed. Prepare the selected text again.')
            return
        suffix = ' · truncated to limits' if data.get('truncated') else ''
        self.scope.setText(f"{data.get('scope', 'Selected local text')} · {data.get('count', 0)} records{suffix}")
        if command == 'ai_prepare':
            self.context_id = data['context_id']
            self.preview.setPlainText(data['text'])
            self.generate_button.setEnabled(True)
            self.feedback.setText('Review this exact text, then click Generate. Preview expires in 5 minutes.')
        elif command == 'ai_generate':
            self.generated_text = data['text']
            self.output.setPlainText(data['text'])
            self.feedback.setText('Generated ' + time.strftime('%H:%M:%S', time.localtime(data.get('generated_at', time.time()))) + ' · Review before using.')


class _SummaryImageBlocker(QObject, QTextObjectInterface):
    def intrinsicSize(self, document, position, text_format):
        return QSizeF(0, 0)

    def drawObject(self, painter, rectangle, document, position, text_format):
        pass


class _SummaryMarkdownDocument(QTextDocument):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt's default image handler can fall back to reading files even when
        # loadResource returns nothing. Replace it before importing Markdown.
        self._image_blocker = _SummaryImageBlocker(self)
        self.documentLayout().registerHandler(QTextFormat.ObjectTypes.ImageObject.value,
                                               self._image_blocker)
        self.setResourceProvider(lambda url: None)

    def loadResource(self, resource_type, name):
        # Markdown images can resolve local files through QTextDocument rather
        # than QTextBrowser. Block both routes to embedded resource loading.
        return None


class SummaryMarkdownBrowser(NotificationBrowser):
    """Scrollable native Markdown with no embedded resource loading."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDocument(_SummaryMarkdownDocument(self))
        self._markdown = None
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.anchorClicked.connect(self.open_link)

    def set_markdown(self, text):
        # Rendered plain text differs from its Markdown source. Compare the
        # source so unchanged local polls preserve selection and scroll state.
        if text == self._markdown:
            return
        scrollbars = (self.verticalScrollBar(), self.horizontalScrollBar())
        positions = [bar.value() for bar in scrollbars]
        features = (QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
                    QTextDocument.MarkdownFeature.MarkdownNoHTML)
        self.document().setMarkdown(text, features)
        self._markdown = text
        for bar, position in zip(scrollbars, positions):
            bar.setValue(position)

    def clear(self):
        self._markdown = None
        super().clear()

    def open_link(self, url):
        if url.isValid() and valid_http_url(url.toString()):
            QDesktopServices.openUrl(url)


class SummaryPage(QWidget):
    """Poll local digest state; the core owns paid cadence and single flight."""
    settings_requested = pyqtSignal()

    def __init__(self, ai_client, parent=None):
        super().__init__(parent)
        self.client = ai_client
        self.account_id = None
        self._known_account = False
        self._supported = True
        self._configured = None
        self._requests = {}
        self._context = None
        self.last_good = None
        self.generated_text = None
        from .summary_widgets import PresetControls, HistoryPage
        outer = QVBoxLayout(self)
        self.presets = PresetControls(ai_client, self)
        outer.addWidget(self.presets)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        latest = QWidget()
        self.tabs.addTab(latest, 'Latest')
        layout = QVBoxLayout(latest)
        self.history = HistoryPage(ai_client, SummaryMarkdownBrowser, self)
        self.tabs.addTab(self.history, 'History')
        self.presets.changed.connect(lambda: self.history.set_presets(self.presets.presets))
        self.presets.active_changed.connect(self.active_changed)
        heading = label('Your recent activity')
        heading.setObjectName('pageTitle')
        layout.addWidget(heading)
        self.scope = label('Recent cached notifications and SMS across your conversations')
        layout.addWidget(self.scope)
        self.output = SummaryMarkdownBrowser()
        self.output.setReadOnly(True)
        self.output.setAccessibleName('Automatic recent account digest')
        self.output.document().setDocumentMargin(16)
        font = self.output.font()
        font.setPointSize(11)
        self.output.setFont(font)
        self.output.setPlaceholderText('Your automatic digest will appear here.')
        layout.addWidget(self.output, 1)
        self.feedback = label('Checking AI configuration…' if ai_client else 'The private AI service is unavailable.')
        layout.addWidget(self.feedback)
        self.details_button = QPushButton('Details')
        self.details_button.setCheckable(True)
        layout.addWidget(self.details_button)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('Summary generation details')
        self.details.setMaximumHeight(170)
        self.details.hide()
        layout.addWidget(self.details)
        self.details_button.toggled.connect(self.details.setVisible)
        row = QHBoxLayout()
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(lambda: self.refresh(force=True))
        self.settings_button = QPushButton('AI Settings…')
        self.settings_button.clicked.connect(self.settings_requested)
        row.addWidget(self.refresh_button)
        row.addWidget(self.settings_button)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label('Updates use recent cached text with OpenAI. API usage may incur charges. Draft replies always require review.'))
        self.poll = QTimer(self)
        self.poll.setInterval(5000)
        self.poll.timeout.connect(self.refresh)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(2000)
        self.debounce.timeout.connect(self.refresh)
        self.refresh_button.setEnabled(False)
        if ai_client:
            ai_client.result.connect(self.result)

    def send(self, command, payload):
        if not self.client or command in self._requests.values():
            return
        request_id = str(uuid.uuid4())
        self._requests[request_id] = command
        self.client.request(command, dict(payload, request_id=request_id))

    def clear(self):
        for request_id in list(self._requests):
            if self.client:
                self.client.request('ai_cancel', {'target_id': request_id})
        self._requests.clear()
        self.debounce.stop()
        self.poll.stop()
        self.last_good = None
        self.generated_text = None
        self.output.clear()
        self.scope.setText('Recent cached notifications and SMS across your conversations')
        self.details.clear()
        self._configured = None

    def stop(self):
        self.clear()
        self.presets.set_account(None)
        self.history.set_account(None)

    def set_context(self, account_id, items, sms_context=None):
        if self._known_account and account_id != self.account_id:
            self.clear()
        self.account_id, self._known_account = account_id, True
        if self._supported:
            self.presets.set_account(account_id)
            self.history.set_account(account_id)
        # Composer edits and navigation do not change an account-wide digest.
        messages = (sms_context or {}).get('messages', [])
        context = (account_id, tuple((r.get('id'), r.get('title'), r.get('body'), r.get('created_at')) for r in items),
                   tuple((r.get('id'), r.get('body'), r.get('timestamp')) for r in messages))
        changed = self._context != context
        self._context = context
        if account_id and self.client and self._supported:
            if self._configured is None:
                self.send('ai_settings', {})
            elif self._configured:
                if not self.poll.isActive():
                    self.poll.start()
                if changed:
                    self.debounce.start()
        elif not account_id:
            self.feedback.setText('Connect a Pushbullet account to see recent activity.')
            self.refresh_button.setEnabled(False)

    def apply_snapshot(self, data):
        caps = data.get('capabilities', data)
        self._supported = caps.get('features', {}).get('ai', True)
        account = caps.get('account_id')
        if self._known_account and account != self.account_id:
            self.clear()
        self.account_id, self._known_account = account, True
        self.presets.set_account(account if self._supported else None)
        self.history.set_account(account if self._supported else None)
        if not self._supported:
            self.poll.stop()
            self.debounce.stop()
            self.refresh_button.setEnabled(False)
            self.feedback.setText('AI is unavailable in this core version. Restart the updated core and desktop.')
        elif account and self._configured is None:
            self.send('ai_settings', {})

    def configuration_changed(self, data):
        # A newer settings result supersedes startup metadata still in flight.
        for request_id, command in list(self._requests.items()):
            if command == 'ai_settings':
                self.client.request('ai_cancel', {'target_id': request_id})
                self._requests.pop(request_id, None)
        self._configured = bool(data.get('configured'))
        self.refresh_button.setEnabled(bool(self.client and self._supported and self.account_id and self._configured))
        if self._configured and self.account_id and self._supported:
            self.poll.start()
            self.refresh()
        else:
            self.poll.stop()
            self.debounce.stop()
            self.feedback.setText('Add or unlock your OpenAI key in AI Settings to enable automatic summaries.')

    def active_changed(self):
        for identity, command in list(self._requests.items()):
            if command == 'ai_digest':
                self.client.request('ai_cancel', {'target_id': identity})
                self._requests.pop(identity)
        self.last_good = self.generated_text = None
        self.output.clear()
        self.details.clear()
        self.feedback.setText('Active preset changed. The next automatic update will use this scope.')

    def refresh(self, force=False):
        if self.client and self.account_id and self._supported and self._configured:
            self.presets.load()
            self.send('ai_digest', {'account_id': self.account_id, 'scope': 'recent', 'force': bool(force)})

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    @staticmethod
    def stamp(value):
        if isinstance(value, (int, float)):
            return time.strftime('%d %b %H:%M', time.localtime(value))
        return str(value or 'recent')

    def result(self, result):
        command = self._requests.pop(result.get('request_id'), None)
        if not command:
            return
        if not result.get('ok'):
            self.feedback.setText(result.get('error', 'The automatic digest could not update. Last successful digest is kept.'))
            return
        data = result.get('data', {})
        if command == 'ai_settings':
            self.configuration_changed(data)
            return
        if data.get('account_id') != self.account_id:
            return
        if command != 'ai_digest':
            return
        remote_revision = data.get('active_revision', self.presets.active_revision)
        remote_preset_revision = data.get('active_preset_revision')
        if remote_revision < self.presets.active_revision:
            return
        if (remote_revision == self.presets.active_revision and remote_preset_revision is not None and
                self.presets.active_preset_revision is not None and remote_preset_revision < self.presets.active_preset_revision):
            return
        remote_id = data.get('active_preset_id')
        if remote_id and (remote_id != self.presets.active_id or
                (remote_preset_revision is not None and remote_preset_revision != self.presets.active_preset_revision)):
            # Digest metadata is authoritative when another desktop changes the scope.
            self.presets.active_id = remote_id
            self.presets.active_revision = remote_revision
            self.presets.active_preset_revision = remote_preset_revision
            self.presets.rebuild()
            self.presets.load()
            self.last_good = self.generated_text = None
            self.output.clear(); self.details.clear()
            self.scope.setText('Active preset changed · Updating cached activity')
        good = data.get('last_good')
        if good:
            self.last_good = copy.deepcopy(good)
            self.generated_text = good.get('text', '')
            self.output.set_markdown(self.generated_text)
            from .summary_widgets import metrics_text, scope_text
            self.details.setPlainText(metrics_text(good) + '\n' + scope_text(good.get('preset_snapshot')))
            counts = good.get('counts', {})
            preset_name = (good.get('preset_snapshot') or {}).get('name') or 'Recent account activity'
            self.scope.setText(f"{preset_name} · {counts.get('notifications', 0)} notifications · {counts.get('sms', 0)} SMS · "
                               f"{self.stamp(good.get('window_start'))} – {self.stamp(good.get('window_end'))}"
                               + (' · Bounded text; some records were truncated' if good.get('truncated') else ' · Cached text only'))
        status = data.get('status')
        if status == 'unconfigured':
            self.configuration_changed({'configured': False})
        elif status in ('queued', 'updating'):
            scheduled = data.get('next_refresh_at')
            message = ('Next update scheduled for ' + self.stamp(scheduled) + '.'
                       if status == 'queued' and isinstance(scheduled, (int, float)) and scheduled > time.time()
                       else 'Updating your digest…')
            self.feedback.setText(message + (' Showing the last successful update.' if self.last_good else ''))
        elif status == 'error':
            self.feedback.setText(data.get('error') or 'The digest could not update. Refresh to try again; the last successful digest is kept.')
        elif self.last_good:
            self.feedback.setText('Updated ' + self.stamp(self.last_good.get('generated_at')) + ' · Updates automatically')
        else:
            self.feedback.setText('No recent cached text is available yet. New activity will appear automatically.')

    def focus_search(self):
        self.output.setFocus()


class ReplyToneDialog(QDialog):
    """A writing preference only; accepting this dialog never sends an SMS."""
    def __init__(self, tone='Natural', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Draft again')
        self.resize(390, 190)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout(self)
        layout.addWidget(label('How should this reply sound?'))
        self.tone = QComboBox()
        self.tone.setEditable(True)
        self.tone.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.tone.addItems(['Natural', 'Friendly', 'Brief', 'Professional', 'Warm'])
        self.tone.lineEdit().setMaxLength(200)
        self.tone.setCurrentText(tone)
        self.tone.setAccessibleName('Reply tone or custom writing preference')
        layout.addWidget(self.tone)
        layout.addWidget(label('Choose a tone or write a short preference. The current draft stays until a new reply is ready.'))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Draft again')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.tone.currentTextChanged.connect(lambda value: self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(value.strip())))
        layout.addWidget(self.buttons)


class SmsAiAssistant(QWidget):
    """Select cached context, explicitly draft, then guard current-text insertion."""
    used = pyqtSignal()

    def __init__(self, ai_client, sms_page, parent=None):
        super().__init__(parent)
        self.client, self.sms_page = ai_client, sms_page
        self.account_id, self.token, self.context_id, self._identity = None, None, None, None
        self._requests, self._supported, self._busy, self._closed = {}, True, False, False
        self._editor_revision, self._generation_revision = 0, 0
        self._candidates, self._selected_ids, self._candidates_loaded = [], set(), False
        self._selection_revision, self._generation_selection_revision = 0, 0
        self.tone, self.tone_dialog = 'Natural', None
        self.generated_text = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.scope = label('Draft a reply')
        self.scope.setObjectName('pageTitle'); layout.addWidget(self.scope)
        layout.addWidget(label('Checked conversation text goes to OpenAI when you choose Draft reply. API usage may incur charges.'))
        self.context_toggle = QPushButton('Conversation context')
        self.context_toggle.setCheckable(True); layout.addWidget(self.context_toggle)
        self.context_box = QWidget()
        context_layout = QVBoxLayout(self.context_box)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_row = QHBoxLayout(); context_layout.addLayout(context_row)
        self.context_limit = label('Only the 20 most recent cached messages appear here.')
        context_row.addWidget(self.context_limit, 1)
        self.refresh_context_button = QPushButton('Refresh local context')
        self.refresh_context_button.clicked.connect(self.refresh_context)
        context_row.addWidget(self.refresh_context_button)
        self.preview = QTableWidget(0, 4)
        self.preview.setHorizontalHeaderLabels(['Use', 'Time', 'Sender', 'Message'])
        self.preview.setAccessibleName('Check messages to include in the reply context')
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.preview.setWordWrap(True)
        self.preview.verticalHeader().hide()
        self.preview.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.preview.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.preview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.preview.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.context_reflow = QTimer(self.preview)
        self.context_reflow.setSingleShot(True)
        self.context_reflow.timeout.connect(self.preview.resizeRowsToContents)
        self.preview.horizontalHeader().sectionResized.connect(
            lambda column, *_: self.context_reflow.start(0) if column == 3 else None)
        self.preview.setMinimumHeight(215)
        self.preview.setMaximumHeight(310)
        self.preview.itemChanged.connect(self.selection_changed)
        context_layout.addWidget(self.preview)
        layout.addWidget(self.context_box)
        self.context_box.hide()
        self.context_toggle.toggled.connect(self.toggle_context)
        self.output = QPlainTextEdit(); self.output.setAccessibleName('Editable SMS reply draft')
        self.output.setPlaceholderText('Your editable reply will appear here. You can also write your own draft.')
        self.output.setMinimumHeight(150); layout.addWidget(self.output, 1)
        self.output.textChanged.connect(self.editor_changed)
        self.feedback = label(); layout.addWidget(self.feedback)
        row = QHBoxLayout(); layout.addLayout(row)
        self.generate_button = QPushButton('Draft reply'); self.generate_button.clicked.connect(self.generate); row.addWidget(self.generate_button)
        self.insert_button = QPushButton('Use draft'); self.insert_button.setEnabled(False); self.insert_button.clicked.connect(self.insert); row.addWidget(self.insert_button)
        self.cancel_button = QPushButton('Close'); self.cancel_button.clicked.connect(self.close_dialog); row.addWidget(self.cancel_button)
        if ai_client: ai_client.result.connect(self.result)
        self.cancel_button.setAutoDefault(False)
        self.update_actions()
        if hasattr(sms_page, 'ai_context_changed'):
            sms_page.ai_context_changed.connect(self.context_changed)

    def toggle_context(self, expanded):
        self.context_box.setVisible(expanded)
        dialog = self.window()
        if isinstance(dialog, QDialog) and dialog.isVisible():
            self.layout().activate()
            if dialog.layout(): dialog.layout().activate()
            available = dialog.screen().availableGeometry().height() if dialog.screen() else 900
            dialog.resize(dialog.width(), min(760 if expanded else 500, available - 40))
        self.preview.resizeRowsToContents()

    def context_changed(self):
        account = getattr(self.sms_page, 'account_id', self.account_id)
        context = self.sms_page.ai_context()
        identity = tuple((context or {}).get(key) for key in ('account_id', 'device_id', 'conversation_id'))
        if account != self.account_id or (context and self._identity and identity != self._identity):
            self.clear(); self.account_id = account
            self.feedback.setText('The conversation changed. Reopen it to choose reply context.')
        elif self.token and not self.current():
            self.insert_button.setEnabled(False)
            self.feedback.setText('The conversation, draft, or pending send changed. Draft a fresh reply.')

    def send(self, command, payload, purpose):
        if not self.client: return
        identity = str(uuid.uuid4())
        self._requests[identity] = (command, purpose)
        self.client.request(command, dict(payload, request_id=identity))

    def update_actions(self):
        context = self.sms_page.ai_context() or {}
        ready = bool(self.output.toPlainText().strip() and self.token and self.current() and context.get('can_reply', True) and not self._busy and not self._closed)
        self.insert_button.setEnabled(ready)
        self.generate_button.setEnabled(bool(self.client and self._supported and self._candidates_loaded and self._selected_ids and not self._busy and not self._closed))
        self.refresh_context_button.setEnabled(bool(self.client and self._supported and not self._busy and not self._closed))
        for button, primary in ((self.generate_button, not ready and not self._busy), (self.insert_button, ready)):
            button.setProperty('primary', primary)
            button.setDefault(primary)
            button.style().unpolish(button); button.style().polish(button)

    def editor_changed(self):
        self._editor_revision += 1
        self.update_actions()

    def cancel(self):
        for identity in self._requests:
            self.client.request('ai_cancel', {'target_id': identity})
        self._requests.clear(); self.context_id = None; self._busy = False
        self.generate_button.setText('Draft again' if self.output.toPlainText() else 'Draft reply')
        self.cancel_button.setText('Close')
        self.update_actions()

    def clear(self):
        self.cancel()
        if self.tone_dialog: self.tone_dialog.reject()
        self.token = None; self._identity = None; self.generated_text = None
        self._candidates, self._selected_ids, self._candidates_loaded = [], set(), False
        self._selection_revision += 1
        self.tone = 'Natural'
        self.preview.blockSignals(True); self.preview.setRowCount(0); self.preview.blockSignals(False)
        self.output.clear()
        self.update_context_header()
        self.update_actions()

    def open_context(self):
        self._closed = False
        context = self.sms_page.ai_context()
        identity = tuple((context or {}).get(key) for key in ('account_id', 'device_id', 'conversation_id'))
        if self._identity != identity:
            self.clear()
        self.cancel()
        if not self._supported:
            self.feedback.setText('AI is unavailable in this core version. Restart the updated core and desktop.'); return
        if not context:
            self.token = None; self.insert_button.setEnabled(False)
            self.scope.setText('Draft a reply'); self.feedback.setText('Select a retained SMS conversation with no pending send first.'); return
        self.account_id = context['account_id']; self._identity = identity
        if not self.output.toPlainText().strip():
            self.token = copy.deepcopy(context.get('token', context))
        heading = getattr(self.sms_page, 'heading', None)
        self.scope.setText('Reply to ' + (heading.text() if heading else str(context['conversation_id'])))
        self.feedback.setText(context.get('reply_unavailable_reason') or 'Choose context, then draft an editable reply. Review & send stays in the SMS composer.')
        self.refresh_context()
        if self.output.toPlainText().strip() and not self.current():
            self.feedback.setText('The conversation or draft changed. Your previous text is kept for copying. Draft a fresh reply to use here.')

    def refresh_context(self):
        if self._busy or self._closed or not self._supported: return
        context = self.sms_page.ai_context()
        if not context or tuple(context.get(key) for key in ('account_id', 'device_id', 'conversation_id')) != self._identity:
            self.feedback.setText('Reopen this conversation to choose reply context.'); return
        for identity, pending in list(self._requests.items()):
            if pending == ('ai_prepare', 'preview'):
                self.client.request('ai_cancel', {'target_id': identity}); self._requests.pop(identity)
        self.send_prepare(context, 'preview')

    def send_prepare(self, context, purpose):
        message_ids = self.selected_ids() if purpose == 'generate' else None
        self.send('ai_prepare', {'account_id': context['account_id'], 'mode': 'draft_reply', 'source': 'sms',
            'device_id': context['device_id'], 'conversation_id': context['conversation_id'],
            'message_ids': message_ids, 'tone': self.tone}, purpose)

    def selected_ids(self):
        return [item['id'] for item in self._candidates if item['id'] in self._selected_ids]

    def update_context_header(self, truncated=False):
        suffix = ' · text clipped to limits' if truncated else ''
        self.context_toggle.setText(f'Conversation context · {len(self._selected_ids)} of {len(self._candidates)} selected' + suffix)

    def populate_context(self, candidates):
        # Contract v1 is required; older cores cannot silently supply unchecked text.
        if not isinstance(candidates, list) or len(candidates) > 20 or any(not isinstance(item, dict) or type(item.get('id')) is not int for item in candidates):
            raise ValueError('This core does not provide selectable reply context. Restart the updated core and desktop.')
        surviving = {item['id'] for item in candidates}
        selected = self._selected_ids & surviving if self._candidates_loaded else surviving
        if selected != self._selected_ids:
            self._selection_revision += 1
        self._candidates, self._selected_ids, self._candidates_loaded = candidates, selected, True
        self.preview.blockSignals(True)
        self.preview.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            checkbox = QTableWidgetItem()
            checkbox.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checkbox.setData(Qt.ItemDataRole.UserRole, candidate['id'])
            checkbox.setCheckState(Qt.CheckState.Checked if candidate['id'] in selected else Qt.CheckState.Unchecked)
            self.preview.setItem(row, 0, checkbox)
            when = candidate.get('timestamp')
            stamp = time.strftime('%d %b %H:%M', time.localtime(when)) if isinstance(when, (int, float)) else 'Unknown time'
            body = candidate.get('body', '') + ('\n[Preview clipped to local limits]' if candidate.get('body_truncated') else '')
            for column, text in ((1, stamp), (2, candidate.get('sender', '')), (3, body)):
                cell = QTableWidgetItem(str(text)); cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.preview.setItem(row, column, cell)
        self.preview.blockSignals(False)
        self.preview.resizeRowsToContents()
        self.update_context_header()
        self.update_actions()

    def selection_changed(self, item):
        if item.column() != 0: return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked: self._selected_ids.add(identifier)
        else: self._selected_ids.discard(identifier)
        self._selection_revision += 1
        self.cancel()
        self.update_context_header()
        self.feedback.setText('Choose at least one message to draft a reply.' if not self._selected_ids else 'Context selection changed. The next draft will use only checked messages.')

    def current(self):
        context = self.sms_page.ai_context()
        return bool(context and context.get('token', context) == self.token)

    def current_composer(self):
        context = self.sms_page.ai_context()
        if not context or not self.token: return False
        ignored = {'message_ids', 'context_version'}
        current = context.get('token', context)
        return ({key:value for key,value in current.items() if key not in ignored} ==
                {key:value for key,value in self.token.items() if key not in ignored})

    def generate(self):
        if self._busy or self._closed or not self._supported or not self._selected_ids: return
        if self.output.toPlainText().strip():
            self.ask_tone()
        else:
            self.begin_generation()

    def ask_tone(self):
        if self.tone_dialog and self.tone_dialog.isVisible():
            self.tone_dialog.raise_(); self.tone_dialog.activateWindow(); return
        identity = self._identity
        self.tone_dialog = ReplyToneDialog(self.tone, self.window())
        dialog = self.tone_dialog
        def confirmed():
            if self._closed or self._identity != identity: return
            tone = dialog.tone.currentText().strip()
            if not tone or len(tone) > 200: return
            self.tone = tone
            self.begin_generation()
        dialog.accepted.connect(confirmed)
        dialog.open(); dialog.raise_(); dialog.activateWindow()

    def begin_generation(self):
        context = self.sms_page.ai_context()
        if not context:
            self.feedback.setText('Select a retained SMS conversation with no pending send first.'); return
        identity = tuple(context.get(key) for key in ('account_id', 'device_id', 'conversation_id'))
        if self._identity != identity:
            self.clear(); self.account_id = context['account_id']
            self.feedback.setText('The conversation changed. Reopen it to choose reply context.'); return
        if not self._selected_ids: return
        self.cancel()
        self.account_id = context['account_id']; self.token = copy.deepcopy(context.get('token', context))
        self._generation_revision = self._editor_revision
        self._generation_selection_revision = self._selection_revision
        self._busy = True
        self.cancel_button.setText('Cancel generation')
        self.update_actions()
        self.feedback.setText('Preparing checked local context…')
        self.send_prepare(context, 'generate')

    def result(self, result):
        pending = self._requests.pop(result.get('request_id'), None)
        if not pending or self._closed: return
        command, purpose = pending
        if not result.get('ok'):
            self.cancel(); self.feedback.setText(result.get('error') or 'The reply could not be drafted. Your editor text was kept. Try again.'); return
        data = result.get('data', {})
        if data.get('account_id') != self.account_id:
            self.clear(); self.feedback.setText('The account changed. Reopen this conversation.'); return
        if command == 'ai_prepare':
            try:
                self.populate_context(data.get('candidates'))
            except ValueError as error:
                self.cancel(); self._candidates_loaded = False; self._candidates = []; self._selected_ids.clear()
                self.preview.blockSignals(True); self.preview.setRowCount(0); self.preview.blockSignals(False)
                self.update_context_header(); self.update_actions(); self.feedback.setText(str(error)); return
            self.context_limit.setText('Only the 20 most recent cached messages appear here.' + (' Older messages are excluded.' if data.get('has_older_messages') else ''))
            self.update_context_header(data.get('truncated', False))
            if purpose == 'generate':
                if (not self.current_composer() or self._selection_revision != self._generation_selection_revision or
                        data.get('selected_ids') != self.selected_ids() or data.get('tone') != self.tone):
                    self.cancel(); self.feedback.setText('The conversation or context selection changed. Check the messages and draft again.'); return
                self.token = copy.deepcopy(self.sms_page.ai_context().get('token', self.sms_page.ai_context()))
                self.feedback.setText('Drafting your reply… Your edits will be preserved.')
                self.send('ai_generate', {'context_id': data['context_id']}, 'generate')
        elif command == 'ai_generate':
            self.cancel()
            if self._editor_revision != self._generation_revision:
                self.feedback.setText('Your editor changed during generation. Your edits were kept. Draft again when ready.'); self.editor_changed(); return
            self.generated_text = data.get('text', '')
            self.output.setPlainText(self.generated_text)
            if not self.current():
                self.insert_button.setEnabled(False)
                self.feedback.setText('Reply drafted, but the conversation, draft, or pending send changed. Copy this text or draft a fresh reply.')
            else:
                self.update_actions()
                self.feedback.setText((self.sms_page.ai_context() or {}).get('reply_unavailable_reason') or 'Edit this reply, then use the draft. Review & send remains in the SMS composer.')
            self.generate_button.setText('Draft again')

    def insert(self):
        text = self.output.toPlainText()
        context = self.sms_page.ai_context() or {}
        if not context.get('can_reply', True):
            self.feedback.setText(context.get('reply_unavailable_reason') or 'This conversation is read-only. You can copy the draft.'); self.insert_button.setEnabled(False); return
        if not text.strip() or not self.token or not self.current():
            self.feedback.setText('The conversation, draft, or pending send changed. Draft a fresh reply.'); self.insert_button.setEnabled(False); return
        if self.token.get('draft'):
            answer = QMessageBox.question(self, 'Replace composer text?', 'Use this draft to replace the text currently in the SMS composer?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes: return
        if self.sms_page.insert_ai_draft(text, self.token):
            self.insert_button.setEnabled(False); self.generated_text = None
            self.feedback.setText('Draft inserted. Review & send when ready.'); self.used.emit()
        else:
            self.feedback.setText('The conversation, draft, or pending send changed. Draft a fresh reply.'); self.insert_button.setEnabled(False)

    def apply_snapshot(self, data):
        caps = data.get('capabilities', data); account = caps.get('account_id')
        if self.account_id != account:
            self.clear(); self.account_id = account
        self._supported = caps.get('features', {}).get('ai', True)
        if not self._supported:
            self.cancel(); self.feedback.setText('AI is unavailable in this core version. Restart the updated core and desktop.')
        elif self.token and not self.current():
            self.insert_button.setEnabled(False)
            self.feedback.setText('The conversation, draft, or pending send changed. Draft a fresh reply.')
        self.update_actions()

    def closed(self):
        self._closed = True
        if self.tone_dialog: self.tone_dialog.reject()
        self.cancel()

    def close_dialog(self):
        if self._busy:
            self.cancel(); self.feedback.setText('Generation cancelled. Your editor text was kept.'); self.editor_changed()
        else:
            dialog = self.window()
            if isinstance(dialog, QDialog): dialog.reject()
            else: self.closed()


class AISettings(QWidget):
    configuration_changed = pyqtSignal(dict)

    def __init__(self, ai_client, parent=None):
        super().__init__(parent)
        self.client = ai_client
        self._requests = {}
        self._revision = 0
        self._mutation = 0
        self._configured = False
        self._stored_model = 'gpt-6-luna'
        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem('gpt-6-luna')
        self.model.lineEdit().setMaxLength(100)
        self.model.setAccessibleName('OpenAI model')
        self.model.currentTextChanged.connect(self._edited)
        form.addRow('Model', self.model)
        self.catalog_status = label('Available models load from your API account after the key is configured.')
        form.addRow(self.catalog_status)
        self.save_model = QPushButton('Save model')
        form.addRow(self.save_model)
        form.addRow(label('Dedicated key: kdewallet → Pushbullet → ai-openai-api-key. Existing entries are never overwritten.'))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(4096)
        self.key.setAccessibleName('New OpenAI API key')
        form.addRow('New API key', self.key)
        self.save_key = QPushButton('Save new API key')
        self.use_key = QPushButton('Use existing saved key')
        form.addRow(self.save_key)
        form.addRow(self.use_key)
        self.feedback = label('Automatic summaries use recent cached notifications and SMS. Draft replies are generated only on request and reviewed in the SMS composer. OpenAI API usage may incur charges.')
        form.addRow(self.feedback)
        self.save_model.clicked.connect(lambda: self.send('ai_save_settings', {'model': self.model.currentText().strip()}))
        self.save_key.clicked.connect(self.save_secret)
        self.use_key.clicked.connect(lambda: self.send('ai_use_key', {}))
        if ai_client:
            ai_client.result.connect(self.result)
        else:
            for button in (self.save_model, self.save_key, self.use_key):
                button.setEnabled(False)

    def _edited(self):
        self._revision += 1

    def send(self, command, payload):
        if not self.client or any(pending[0] == command for pending in self._requests.values()):
            return
        request_id = str(uuid.uuid4())
        if command in ('ai_save_settings', 'ai_save_key', 'ai_use_key'):
            self._mutation += 1
        self._requests[request_id] = (command, self._revision, self._mutation)
        self.client.request(command, dict(payload, request_id=request_id))
        if command == 'ai_models':
            self.catalog_status.setText('Loading available text models…')

    def save_secret(self):
        key = self.key.text()
        self.key.clear()
        self.send('ai_save_key', {'key': key})

    def showEvent(self, event):
        super().showEvent(event)
        self.send('ai_settings', {})

    def apply_snapshot(self, data):
        caps = data.get('capabilities', data)
        supported = caps.get('features', {}).get('ai', True)
        for button in (self.save_model, self.save_key, self.use_key):
            button.setEnabled(bool(self.client and supported))
        if not supported:
            self.feedback.setText('AI is unavailable in this core version. Restart the updated core and desktop.')

    def result(self, result):
        pending = self._requests.pop(result.get('request_id'), None)
        if pending is None:
            return
        command, revision, mutation = pending
        if command == 'ai_settings' and mutation != self._mutation:
            return
        if not result.get('ok'):
            target = self.catalog_status if command == 'ai_models' else self.feedback
            target.setText(result.get('error', 'AI configuration could not be saved.'))
            return
        data = result.get('data', {})
        if command == 'ai_models':
            current = self.model.currentText()
            models = list(dict.fromkeys(data.get('models', [])))
            for retained in (self._stored_model, current):
                if retained and retained not in models:
                    models.append(retained)
            self.model.blockSignals(True)
            self.model.clear()
            self.model.addItems(models)
            self.model.setCurrentText(current)
            self.model.blockSignals(False)
            suffix = ' gpt-6-luna is recommended for fast, economical updates.' if ('gpt-6-luna' in data.get('models', []) or self._stored_model == 'gpt-6-luna') else ''
            notice = data.get('notice') or ''
            self.catalog_status.setText(f"{len(data.get('models', []))} available text models." + suffix + (' ' + notice if notice else ''))
            return
        self._configured = bool(data.get('configured'))
        self._stored_model = data.get('model', self._stored_model)
        if revision == self._revision:
            self.model.blockSignals(True)
            self.model.setCurrentText(self._stored_model)
            self.model.blockSignals(False)
        self.feedback.setText('AI key configured. Summaries update automatically; draft replies require review.' if self._configured else 'Set up or unlock your OpenAI API key to enable automatic summaries.')
        self.configuration_changed.emit(data)
        if self._configured and command in ('ai_settings', 'ai_save_key', 'ai_use_key'):
            self.send('ai_models', {})
