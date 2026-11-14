"""Account-scoped saved summary controls; every operation uses the private core."""
import copy
import datetime
import uuid
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QPlainTextEdit, QDialog, QFormLayout, QCheckBox, QListWidget,
    QListWidgetItem, QDialogButtonBox, QSplitter, QMessageBox, QScrollArea)

CONFIG_KEYS = ('name', 'sources', 'device_ids', 'conversations', 'conversation_filter', 'window_hours', 'model_override')


def text_label(text=''):
    item = QLabel(text)
    item.setWordWrap(True)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return item


def stamp(value):
    try:
        if isinstance(value, (float, int)):
            moment = datetime.datetime.fromtimestamp(value).astimezone()
        elif isinstance(value, str):
            moment = datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone()
        else:
            return 'Not recorded'
        return moment.strftime('%d %b %Y %H:%M %Z')
    except (ValueError, TypeError, OverflowError):
        return 'Not recorded'


def scope_text(snapshot):
    if not snapshot:
        return 'Captured scope: Not recorded (legacy summary)'
    sources = ', '.join({'sms': 'SMS', 'notifications': 'Notifications'}.get(source, source) for source in snapshot.get('sources', [])) or 'Not recorded'
    devices = snapshot.get('device_ids') or []
    pairs = snapshot.get('conversations') or []
    conversations = (', '.join(pair['conversation_id'] + ' on ' + pair['device_id'] for pair in pairs)
                     if snapshot.get('conversation_filter') == 'selected' else 'All conversations')
    return ('Captured scope: ' + sources + '\nDevices: ' + (', '.join(devices) if devices else 'All cached devices') +
            '\nConversations: ' + conversations + '\nPreset window: ' + str(snapshot.get('window_hours', 'Not recorded')) + ' hours' +
            ' · Model preference: ' + str(snapshot.get('model_override') or 'Use AI Settings'))


def metrics_text(data):
    source = dict(data.get('generation') or {}, **data)
    metrics = source.get('metrics') or {}
    counts = source.get('counts') or {}
    for key, count in (('notification_count','notifications'), ('sms_count','sms'), ('conversation_count','conversations')):
        if key not in source and count in counts: source[key] = counts[count]
    def value(key):
        result = source.get(key, metrics.get(key))
        return 'Not recorded' if result is None else str(result)
    return ('Input tokens: ' + value('input_tokens') + ' · Output tokens: ' + value('output_tokens') +
            ' · Cached input tokens: ' + value('cached_input_tokens') + '\nLatency (ms): ' + value('latency_ms') +
            '\nModel: ' + str(source.get('actual_response_model') or source.get('resolved_model') or source.get('model') or 'Not recorded') +
            ' · Prompt version: ' + value('prompt_version') + '\nTruncation: ' +
            ('Not recorded' if source.get('truncated') is None else ('Yes' if source['truncated'] else 'No')) +
            ' · Reuse count: ' + value('reuse_count') +
            '\nCoverage: ' + value('notification_count') + ' notifications · ' + value('sms_count') + ' SMS · ' + value('conversation_count') + ' conversations' +
            '\nWindow: ' + stamp(source.get('window_start')) + ' – ' + stamp(source.get('window_end')) +
            '\nSaved: ' + stamp(source.get('saved_at')) + ' · Generated: ' + stamp(source.get('generated_at')) +
            '\nLast reused: ' + stamp(source.get('last_reused_at')) +
            '\nOldest covered: ' + stamp(source.get('covered_oldest')) + ' · Newest covered: ' + stamp(source.get('covered_newest')) + '\nCost: Unavailable')


class CoreWidget(QWidget):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client, self.account_id, self.requests = client, None, {}
        if client:
            client.result.connect(self._result)

    def send(self, command, **payload):
        if not self.client or not self.account_id:
            return
        identity = str(uuid.uuid4())
        self.requests[identity] = (command, self.account_id, payload)
        self.client.request(command, dict(payload, account_id=self.account_id, request_id=identity))

    def set_account(self, account):
        if account == self.account_id:
            return
        for identity in self.requests:
            self.client.request('ai_cancel', {'target_id': identity})
        self.requests.clear()
        self.account_id = account
        self.reset()

    def reset(self):
        pass

    def _result(self, result):
        pending = self.requests.pop(result.get('request_id'), None)
        if not pending or pending[1] != self.account_id:
            return
        if not result.get('ok'):
            self.feedback.setText(result.get('error') or 'The private core could not complete this request. Reload and try again.')
            return
        data = result.get('data', {})
        returned_account = data.get('account_id', (data.get('preset') or {}).get('account_id', self.account_id))
        if returned_account != self.account_id:
            self.feedback.setText('The account changed. Reload this view.'); return
        self.received(pending[0], data, pending[2])


class PresetEditor(QDialog):
    saved = pyqtSignal(dict)
    def __init__(self, preset=None, choices=None, parent=None, is_active=False, choices_truncated=False):
        super().__init__(parent)
        self.setWindowTitle('Edit summary preset' if preset else 'New summary preset')
        self.resize(520, 590)
        self.preset = copy.deepcopy(preset)
        config = preset or {'name': '', 'sources': ['notifications', 'sms'], 'device_ids': [], 'conversations': [],
                            'conversation_filter': 'all', 'window_hours': 24, 'model_override': None}
        outer = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); form = QFormLayout(body); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll.setWidget(body); outer.addWidget(scroll)
        if not preset:
            self.template = QComboBox(); self.template.addItems(['All activity', 'Notifications only', 'Selected conversations'])
            form.addRow('Start from', self.template)
        self.name = QLineEdit(config['name']); self.name.setMaxLength(100); form.addRow('Name', self.name)
        self.notifications = QCheckBox('Notifications'); self.sms = QCheckBox('SMS')
        self.notifications.setChecked('notifications' in config['sources']); self.sms.setChecked('sms' in config['sources'])
        form.addRow('Sources', self.notifications); form.addRow('', self.sms)
        self.window = QComboBox()
        for hours, title in ((1,'Past hour'), (6,'Past 6 hours'), (24,'Past day'), (72,'Past 3 days'), (168,'Past week')):
            self.window.addItem(title, hours)
        self.window.setCurrentIndex(self.window.findData(config['window_hours'])); form.addRow('Time window', self.window)
        self.model = QLineEdit(config['model_override'] or ''); self.model.setPlaceholderText('Use AI Settings model'); self.model.setMaxLength(100)
        form.addRow('Model override', self.model)
        self.devices = QListWidget(); self.devices.setMaximumHeight(105)
        devices, conversations = choices or ([], [])
        for device in devices:
            item = QListWidgetItem(device.get('nickname') or device['id']); item.setData(Qt.ItemDataRole.UserRole, device['id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked if device['id'] in config['device_ids'] else Qt.CheckState.Unchecked); self.devices.addItem(item)
        for identifier in config['device_ids']:
            if not any(self.devices.item(i).data(Qt.ItemDataRole.UserRole) == identifier for i in range(self.devices.count())):
                item = QListWidgetItem(identifier + ' (cached selection)'); item.setData(Qt.ItemDataRole.UserRole, identifier); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked); self.devices.addItem(item)
        form.addRow('Devices (empty = all)', self.devices)
        self.selected = QCheckBox('Only selected SMS conversations'); self.selected.setChecked(config['conversation_filter'] == 'selected'); form.addRow(self.selected)
        self.search = QLineEdit(); self.search.setPlaceholderText('Search cached conversations…'); form.addRow(self.search)
        self.conversations = QListWidget(); self.conversations.setMinimumHeight(100)
        pairs = {(p['device_id'], p['conversation_id']) for p in config['conversations']}
        seen = set()
        for conversation in conversations + config['conversations']:
            pair = conversation['device_id'], conversation['conversation_id']
            if pair in seen: continue
            seen.add(pair)
            item = QListWidgetItem((conversation.get('name') or pair[1]) + ' · ' + pair[0]); item.setData(Qt.ItemDataRole.UserRole, pair)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked if pair in pairs else Qt.CheckState.Unchecked); self.conversations.addItem(item)
        form.addRow(self.conversations)
        if choices_truncated:
            form.addRow(text_label('Some cached choices are omitted by the local limit. Search covers the choices shown here. Existing selections are kept.'))
        self.active_notice = text_label()
        outer.addWidget(self.active_notice)
        self.feedback = text_label(); outer.addWidget(self.feedback)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save); self.buttons.rejected.connect(self.reject); outer.addWidget(self.buttons)
        self.set_active(is_active)
        if not preset:
            self.template.currentIndexChanged.connect(self.apply_template)
        self.search.textChanged.connect(lambda text: [self.conversations.item(i).setHidden(text.casefold() not in self.conversations.item(i).text().casefold()) for i in range(self.conversations.count())])

    def set_active(self, active):
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('Save && apply' if active else 'Save preset')
        self.active_notice.setText('Saving changes updates your automatic summary scope. The next update may send matching cached text to OpenAI.' if active else
            'Save this preset without changing the active summary. Only locally cached choices are shown. If this preset becomes active on another desktop, saved changes apply to its next automatic update.')

    def apply_template(self, index):
        self.notifications.setChecked(index != 2)
        self.sms.setChecked(index != 1)
        self.selected.setChecked(index == 2)
        if not self.name.text().strip(): self.name.setText(self.template.currentText())

    def save(self):
        sources = [name for name, box in (('notifications', self.notifications), ('sms', self.sms)) if box.isChecked()]
        pairs = [self.conversations.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.conversations.count()) if self.conversations.item(i).checkState() == Qt.CheckState.Checked]
        if not self.name.text().strip() or not sources or (self.selected.isChecked() and (not pairs or 'sms' not in sources)):
            self.feedback.setText('Enter a name and choose a source. Selected conversations require SMS and at least one conversation.'); return
        self.saved.emit({'name': self.name.text().strip(), 'sources': sources,
            'device_ids': [self.devices.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.devices.count()) if self.devices.item(i).checkState() == Qt.CheckState.Checked],
            'conversations': [{'device_id': a, 'conversation_id': b} for a,b in pairs] if self.selected.isChecked() else [], 'conversation_filter': 'selected' if self.selected.isChecked() else 'all',
            'window_hours': self.window.currentData(), 'model_override': self.model.text().strip() or None})


class PresetControls(CoreWidget):
    changed = pyqtSignal()
    active_changed = pyqtSignal()
    mutated = pyqtSignal()
    def __init__(self, client, parent=None):
        super().__init__(client, parent)
        self.presets, self.active_revision, self.active_id, self.active_preset_revision, self.choices = [], 0, None, None, ([], [])
        self.editor = None
        self.options_truncated = False
        self._user_selected = False
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        row = QHBoxLayout(); layout.addLayout(row)
        self.combo = QComboBox(); self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon); self.combo.setMinimumContentsLength(10); self.combo.setAccessibleName('Summary preset'); row.addWidget(self.combo, 1)
        self.activate = QPushButton('Activate'); self.activate.clicked.connect(self.activate_selected); row.addWidget(self.activate)
        self.edit = QPushButton('Edit presets…'); self.edit.clicked.connect(self.manage); row.addWidget(self.edit)
        self.feedback = text_label(); layout.addWidget(self.feedback)
        self.combo.activated.connect(lambda: setattr(self, '_user_selected', True))
        self.combo.currentIndexChanged.connect(self.selection_changed)
        self.selection_changed()

    def reset(self):
        if self.editor: self.editor.reject()
        self.choices = ([], [])
        self.options_truncated = False
        self._user_selected = False
        self.presets, self.active_id, self.active_revision, self.active_preset_revision = [], None, 0, None
        self.combo.clear(); self.feedback.clear(); self.load()

    def load(self):
        if not self.account_id: return
        for command in ('ai_presets', 'ai_active_preset', 'ai_preset_options'):
            if not any(p[0] == command for p in self.requests.values()): self.send(command, **({'include_archived': True} if command == 'ai_presets' else {}))

    def selection_changed(self):
        self.activate.setEnabled(bool(self.account_id and self.combo.currentData() and self.combo.currentData() != self.active_id))
        self.edit.setEnabled(bool(self.account_id))

    def activate_selected(self):
        if self.combo.currentData():
            self._user_selected = False
            self.send('ai_activate_preset', preset_id=self.combo.currentData(), expected_revision=self.active_revision)
            self.activate.setEnabled(False)

    def rebuild(self):
        selected = self.combo.currentData()
        self.combo.blockSignals(True); self.combo.clear()
        for preset in self.presets:
            if not preset.get('archived'):
                self.combo.addItem(preset['name'] + (' · Active' if preset['id'] == self.active_id else ''), preset['id'])
        index = self.combo.findData(selected if self._user_selected else self.active_id)
        self.combo.setCurrentIndex(index if index >= 0 else 0); self.combo.blockSignals(False); self.selection_changed()
        active = next((preset for preset in self.presets if preset['id'] == self.active_id), None)
        self.feedback.setText('Active: ' + active['name'] + ' · Shared across your desktops' if active else '')
        if self.editor:
            self.editor.set_active(bool(self.editor.preset and self.editor.preset['id'] == self.active_id))

    def received(self, command, data, payload):
        if command == 'ai_preset_options':
            self.choices = (data['devices'], data['conversations'])
            self.options_truncated = bool(data.get('truncated'))
        elif command == 'ai_presets':
            known = {preset['id']:preset for preset in self.presets}
            self.presets = [known[preset['id']] if preset['id'] in known and known[preset['id']]['revision'] > preset['revision'] else preset for preset in data['presets']]
            self.rebuild(); self.changed.emit()
        elif command in ('ai_active_preset', 'ai_activate_preset'):
            if data['revision'] < self.active_revision or (data['revision'] == self.active_revision and data['preset']['id'] == self.active_id and self.active_preset_revision is not None and data['preset']['revision'] < self.active_preset_revision): return
            old = (self.active_id, self.active_preset_revision)
            self.active_id = data['preset']['id']; self.active_revision = data['revision']; self.active_preset_revision = data['preset']['revision']
            found = any(preset['id'] == self.active_id for preset in self.presets)
            self.presets = [data['preset'] if preset['id'] == self.active_id else preset for preset in self.presets]
            if not found: self.presets.append(data['preset'])
            self.rebuild()
            if old != (self.active_id, self.active_preset_revision):
                self.changed.emit()
                if old[0] is not None: self.active_changed.emit()
            if command == 'ai_activate_preset': self.feedback.setText('Active on all desktops'); self.changed.emit()
        else:
            found = any(preset['id'] == data['id'] for preset in self.presets)
            self.presets = [data if preset['id'] == data['id'] else preset for preset in self.presets]
            if not found: self.presets.append(data)
            if self.editor: self.editor.accept(); self.editor = None
            self.load()
            self.mutated.emit()

    def manage(self):
        dialog = QDialog(self); dialog.setWindowTitle('Summary presets'); dialog.resize(460, 370)
        layout = QVBoxLayout(dialog); listing = QListWidget(); layout.addWidget(listing)
        for preset in self.presets:
            item = QListWidgetItem(preset['name'] + (' · Archived' if preset.get('archived') else '')); item.setData(Qt.ItemDataRole.UserRole, preset); listing.addItem(item)
        layout.addWidget(text_label('Creating and editing keeps the current active preset. Archived presets keep their saved history.'))
        row = QHBoxLayout(); layout.addLayout(row)
        def edit(preset=None):
            self.editor = PresetEditor(preset, self.choices, dialog, is_active=bool(preset and preset['id'] == self.active_id), choices_truncated=self.options_truncated)
            self.editor.saved.connect(lambda config: self.send('ai_update_preset' if preset else 'ai_create_preset', **dict(config=config, expected_revision=preset['revision'] if preset else 0, **({'preset_id': preset['id']} if preset else {}))))
            self.editor.open()
            self.editor.raise_()
            self.editor.activateWindow()
        new = QPushButton('New preset'); new.clicked.connect(lambda: edit()); row.addWidget(new)
        update = QPushButton('Edit'); update.clicked.connect(lambda: edit(listing.currentItem().data(Qt.ItemDataRole.UserRole)) if listing.currentItem() and not listing.currentItem().data(Qt.ItemDataRole.UserRole).get('archived') else None); row.addWidget(update)
        archive = QPushButton('Archive'); archive.clicked.connect(lambda: self.send('ai_archive_preset', preset_id=listing.currentItem().data(Qt.ItemDataRole.UserRole)['id'], expected_revision=listing.currentItem().data(Qt.ItemDataRole.UserRole)['revision']) if listing.currentItem() else None); row.addWidget(archive)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); close.rejected.connect(dialog.reject); layout.addWidget(close)
        self.mutated.connect(dialog.accept); dialog.exec()

    def _result(self, result):
        pending = self.requests.get(result.get('request_id'))
        if pending and not result.get('ok') and self.editor:
            self.editor.feedback.setText(result.get('error') or 'Save failed. Your edits are kept. Reload the preset to resolve a revision conflict.')
        super()._result(result)


class HistoryPage(CoreWidget):
    def __init__(self, client, browser_class, parent=None):
        super().__init__(client, parent)
        self.offset, self.total, self.entry, self.list_revision, self.detail_revision = 0, 0, None, 0, 0
        self.policy = None
        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,0)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QScrollArea.Shape.NoFrame); outer.addWidget(self.scroll)
        self.body = QWidget(); self.scroll.setWidget(self.body)
        layout = QVBoxLayout(self.body)
        filters = QHBoxLayout(); layout.addLayout(filters)
        self.search = QLineEdit(); self.search.setPlaceholderText('Search saved summaries…'); self.search.setMaxLength(256); filters.addWidget(self.search, 1)
        self.preset_filter = QComboBox(); self.preset_filter.addItem('All presets', None); filters.addWidget(self.preset_filter)
        self.pinned = QCheckBox('Pinned'); filters.addWidget(self.pinned)
        dates = QHBoxLayout(); layout.addLayout(dates)
        self.date_from = QLineEdit(); self.date_from.setPlaceholderText('From YYYY-MM-DD')
        self.date_to = QLineEdit(); self.date_to.setPlaceholderText('Through YYYY-MM-DD')
        dates.addWidget(self.date_from); dates.addWidget(self.date_to)
        self.settings = QPushButton('History settings…'); self.settings.clicked.connect(self.open_settings); dates.addWidget(self.settings)
        self.splitter = QSplitter(Qt.Orientation.Horizontal); layout.addWidget(self.splitter, 1)
        self.listing = QListWidget(); self.listing.setMinimumWidth(160); self.listing.setMinimumHeight(90); self.splitter.addWidget(self.listing)
        detail = QWidget(); dl = QVBoxLayout(detail); dl.setContentsMargins(0,0,0,0)
        self.heading = text_label('Choose a saved summary'); dl.addWidget(self.heading)
        self.output = browser_class(); self.output.setMinimumHeight(140); self.output.setPlaceholderText('Saved summaries stay available here. Pin an entry to keep it beyond the retention period.'); dl.addWidget(self.output, 1)
        self.details_button = QPushButton('Details'); self.details_button.setCheckable(True); dl.addWidget(self.details_button)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setMinimumHeight(100); self.details.setMaximumHeight(170); self.details.setAccessibleName('Saved summary generation details'); self.details.hide(); dl.addWidget(self.details); self.details_button.toggled.connect(self.details.setVisible); self.details_button.toggled.connect(self.adapt)
        self.pin = QPushButton('Pin summary'); self.pin.setEnabled(False); self.pin.clicked.connect(self.toggle_pin); dl.addWidget(self.pin)
        self.splitter.addWidget(detail); self.splitter.setChildrenCollapsible(False); self.splitter.setStretchFactor(1,1)
        pager = QHBoxLayout(); layout.addLayout(pager)
        self.previous = QPushButton('Previous'); self.next = QPushButton('Next'); self.count = text_label()
        self.previous.clicked.connect(lambda: self.page(-1)); self.next.clicked.connect(lambda: self.page(1))
        pager.addWidget(self.previous); pager.addWidget(self.count, 1); pager.addWidget(self.next)
        self.feedback = text_label(); layout.addWidget(self.feedback)
        self.debounce = QTimer(self); self.debounce.setSingleShot(True); self.debounce.setInterval(300); self.debounce.timeout.connect(self.filter_changed)
        self.search.textChanged.connect(lambda: self.debounce.start())
        for line in (self.date_from, self.date_to): line.editingFinished.connect(self.filter_changed)
        self.preset_filter.currentIndexChanged.connect(self.filter_changed); self.pinned.toggled.connect(self.filter_changed)
        self.listing.currentItemChanged.connect(self.select)
        self.previous.setEnabled(False); self.next.setEnabled(False)

    def reset(self):
        if hasattr(self, 'settings_dialog'): self.settings_dialog.reject()
        self.debounce.stop(); self.list_revision += 1; self.detail_revision += 1
        self.listing.clear(); self.output.clear(); self.details.clear(); self.heading.setText('Choose a saved summary'); self.pin.setEnabled(False)
        self.entry, self.policy, self.offset = None, None, 0
        self.feedback.clear()
        self.search.clear(); self.date_from.clear(); self.date_to.clear(); self.pinned.setChecked(False)
        self.preset_filter.blockSignals(True); self.preset_filter.clear(); self.preset_filter.addItem('All presets', None); self.preset_filter.blockSignals(False)

    def set_presets(self, presets):
        current = self.preset_filter.currentData(); self.preset_filter.blockSignals(True); self.preset_filter.clear(); self.preset_filter.addItem('All presets', None)
        for preset in presets: self.preset_filter.addItem(preset['name'] + (' · Archived' if preset.get('archived') else ''), preset['id'])
        self.preset_filter.setCurrentIndex(max(0,self.preset_filter.findData(current))); self.preset_filter.blockSignals(False)

    def filter_changed(self):
        self.offset = 0; self.load()

    def page(self, direction):
        self.offset = max(0, self.offset + 50 * direction); self.load()

    def load(self):
        if not self.account_id: return
        payload = {'limit':50, 'offset':self.offset}
        if self.search.text().strip(): payload['query'] = self.search.text().strip()
        if self.preset_filter.currentData(): payload['preset_id'] = self.preset_filter.currentData()
        if self.pinned.isChecked(): payload['pinned'] = True
        try:
            for line, key, offset in ((self.date_from,'date_from',0),(self.date_to,'date_to',1)):
                if line.text().strip(): payload[key] = datetime.datetime.combine(datetime.date.fromisoformat(line.text().strip()) + datetime.timedelta(days=offset), datetime.time.min).astimezone().isoformat()
        except ValueError:
            self.feedback.setText('Use YYYY-MM-DD for dates.'); return
        self.list_revision += 1
        # Cancel superseded queries so stale filters cannot repaint the list.
        for identity, pending in list(self.requests.items()):
            if pending[0] == 'ai_history': self.client.request('ai_cancel', {'target_id':identity}); self.requests.pop(identity)
        self.send('ai_history', **payload)

    def showEvent(self, event):
        super().showEvent(event); self.load()

    def adapt(self):
        narrow = self.width() < 650
        self.body.setMinimumHeight((680 if self.details_button.isChecked() else 560) if narrow else 0)
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes([110, 440] if narrow else [230, 650])

    def resizeEvent(self, event):
        self.adapt()
        super().resizeEvent(event)

    def select(self, current, previous):
        if not current: return
        for identity, pending in list(self.requests.items()):
            if pending[0] == 'ai_history_detail': self.client.request('ai_cancel', {'target_id':identity}); self.requests.pop(identity)
        self.send('ai_history_detail', entry_id=current.data(Qt.ItemDataRole.UserRole))

    def render(self, entry):
        self.entry = entry
        snapshot = entry.get('preset_snapshot') or {}
        self.heading.setText((snapshot.get('name') or 'Legacy summary') + '\nSaved ' + stamp(entry.get('saved_at')) + ' · Generated ' + stamp(entry.get('generated_at')))
        self.output.set_markdown(entry.get('markdown') or entry.get('generation', {}).get('markdown') or '')
        self.details.setPlainText(metrics_text(entry) + '\n' + scope_text(snapshot))
        self.pin.setEnabled(True); self.pin.setText('Unpin summary' if entry.get('pinned') else 'Pin summary')

    def toggle_pin(self):
        if self.entry: self.send('ai_pin_history', entry_id=self.entry['id'], pinned=not self.entry.get('pinned',False))

    def received(self, command, data, payload):
        if command == 'ai_history':
            if not data['total']: self.offset = 0
            if data['total'] and self.offset >= data['total']:
                self.offset = ((data['total'] - 1) // 50) * 50
                self.load()
                return
            selected = self.listing.currentItem().data(Qt.ItemDataRole.UserRole) if self.listing.currentItem() else None
            self.listing.blockSignals(True); self.listing.clear()
            for entry in data['items']:
                item = QListWidgetItem(('★ ' if entry.get('pinned') else '') + (entry.get('preset_snapshot') or {}).get('name','Legacy summary') + '\n' + stamp(entry.get('saved_at')))
                item.setData(Qt.ItemDataRole.UserRole, entry['id']); self.listing.addItem(item)
                if entry['id'] == selected: self.listing.setCurrentItem(item)
            self.listing.blockSignals(False)
            if not self.listing.currentItem():
                self.entry = None; self.output.clear(); self.heading.setText('Choose a saved summary'); self.details.clear(); self.pin.setEnabled(False)
            self.total = data['total']; self.previous.setEnabled(self.offset > 0); self.next.setEnabled(self.offset + 50 < self.total)
            self.count.setText(f'{self.offset + 1 if self.total else 0}–{min(self.offset+50,self.total)} of {self.total}')
            self.feedback.setText('No saved summaries match these filters.' if not data['items'] else '')
        elif command in ('ai_history_detail', 'ai_pin_history'):
            selected = self.listing.currentItem()
            if selected and selected.data(Qt.ItemDataRole.UserRole) == data['id']: self.render(data)
            if command == 'ai_pin_history': self.load()
        elif command == 'ai_history_settings':
            self.settings_feedback.clear()
            self.policy = data; self.retention.setCurrentIndex(self.retention.findData(data['retention_days'])); self.retention.setEnabled(True); self.retention_save.setEnabled(True)
        elif command == 'ai_save_history_settings':
            self.retention_save.setEnabled(True)
            if payload.get('preview'):
                days = payload['retention_days']; count = data['affected_count']
                answer = QMessageBox.question(self.settings_dialog, 'Apply retention policy?', f'Keep unpinned summaries for {days} days. This removes {count} older unpinned summaries. Pinned summaries are protected.\n\nApply this policy?', QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
                if answer == QMessageBox.StandardButton.Apply:
                    self.retention_save.setEnabled(False)
                    self.send('ai_save_history_settings', retention_days=days, expected_revision=data['revision'], preview=False, confirmed_retention_days=days)
            else:
                self.policy = data; self.settings_dialog.accept(); self.feedback.setText(f"Retention saved. Removed {data.get('removed_count',0)} unpinned summaries."); self.load()

    def _result(self, result):
        pending = self.requests.get(result.get('request_id'))
        if pending and pending[0] in ('ai_history_settings', 'ai_save_history_settings') and not result.get('ok'):
            self.settings_feedback.setText(result.get('error') or 'The policy could not be saved. Reload its current revision and try again.')
            self.retention_save.setEnabled(self.policy is not None)
        super()._result(result)

    def cancel_settings_requests(self):
        for identity, pending in list(self.requests.items()):
            if pending[0] in ('ai_history_settings', 'ai_save_history_settings'):
                self.client.request('ai_cancel', {'target_id': identity}); self.requests.pop(identity)

    def preview_policy(self):
        if self.policy:
            self.retention_save.setEnabled(False)
            self.send('ai_save_history_settings', retention_days=self.retention.currentData(), expected_revision=self.policy['revision'], preview=True)

    def open_settings(self):
        if hasattr(self, 'settings_dialog') and self.settings_dialog.isVisible():
            self.settings_dialog.raise_(); return
        self.settings_dialog = QDialog(self); self.settings_dialog.setWindowTitle('Saved summary retention'); layout = QVBoxLayout(self.settings_dialog)
        layout.addWidget(text_label('Pinned summaries are always kept. A preview and confirmation are required before applying this policy.'))
        self.retention = QComboBox()
        for days in (7,30,90,365): self.retention.addItem(f'{days} days',days)
        self.retention.setEnabled(False); layout.addWidget(self.retention)
        self.retention_save = QPushButton('Preview changes…'); self.retention_save.setEnabled(False); layout.addWidget(self.retention_save)
        self.retention_save.clicked.connect(self.preview_policy)
        self.settings_feedback = text_label(); layout.addWidget(self.settings_feedback)
        self.settings_dialog.finished.connect(self.cancel_settings_requests)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); close.rejected.connect(self.settings_dialog.reject); layout.addWidget(close)
        self.send('ai_history_settings'); self.settings_dialog.show()
