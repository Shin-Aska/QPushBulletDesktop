"""Native, instance-bound core controls; all operations use the desktop worker."""
import uuid

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QGroupBox, QLabel, QPushButton, QCheckBox,
                             QScrollArea, QMessageBox, QSizePolicy)

from .actions import plain_label


class CoreSettings(QWidget):
    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self._requests = {}
        self._verified_instance = None
        self._data = None
        self._status_valid = False
        self._supported = None
        self._restart_supported = None
        self._connected = False
        self._notification_revision = 0
        self._operation_error = None
        self._restart_from = None
        self._restart_timed_out = False
        self.restart_timer = QTimer(self)
        self.restart_timer.setSingleShot(True)
        self.restart_timer.setInterval(25000)
        self.restart_timer.timeout.connect(self.restart_timeout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        title = plain_label('Core backend')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        self.status = plain_label('Waiting for verified core status')
        self.status.setObjectName('coreBackendStatus')
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton('Refresh status')
        self.refresh_button.clicked.connect(self.refresh)
        self.restart_button = QPushButton('Restart core…')
        self.restart_button.clicked.connect(self.restart)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.restart_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.feedback = plain_label('Status is read from your authenticated local backend.')
        self.feedback.setObjectName('coreBackendFeedback')
        layout.addWidget(self.feedback)

        self.values = {}
        for title, fields in (
            ('Connection', (('instance', 'Verified instance'), ('uptime', 'Uptime'),
                            ('core', 'Core address'), ('web', 'Web address'))),
            ('Lifetime · launch controlled', (('mode', 'Mode'), ('clients', 'Active clients'),
                    ('renew', 'Lease renewal'), ('expiry', 'Lease expiry'), ('grace', 'Release grace'))),
            ('Local storage · launch controlled', (('application', 'Application'), ('state', 'State'),
                                                    ('database', 'Database')))):
            group = QGroupBox(title)
            form = QFormLayout(group)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            for key, caption in fields:
                value = plain_label('Not loaded')
                value.setWordWrap(True)
                value.setMinimumWidth(0)
                value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse |
                                              Qt.TextInteractionFlag.TextSelectableByKeyboard)
                value.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                value.setAccessibleName(caption)
                form.addRow(caption, value)
                self.values[key] = value
            layout.addWidget(group)

        group = QGroupBox('Desktop notifications')
        notification_layout = QVBoxLayout(group)
        self.notifications_enabled = QCheckBox('Deliver desktop notifications')
        self.notifications_enabled.setAccessibleName('Deliver desktop notifications')
        self.notifications_enabled.toggled.connect(self.notification_edited)
        notification_layout.addWidget(self.notifications_enabled)
        self.notification_outcome = plain_label('Delivery availability has not been checked.')
        self.notification_outcome.setWordWrap(True)
        notification_layout.addWidget(self.notification_outcome)
        self.apply_button = QPushButton('Apply notification setting')
        self.apply_button.clicked.connect(self.apply_notifications)
        notification_layout.addWidget(self.apply_button)
        layout.addWidget(group)
        note = plain_label('Addresses, storage paths and lease timings follow how the backend was launched. '
                           'In coupled mode, desktop and local web server processes keep the core alive; '
                           'browser tabs do not. The last client releases it after the grace period.')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.scroll = scroll
        worker.result.connect(self.result)
        self.update_controls()

    def send(self, command, values=None):
        if any(value['command'] == command for value in self._requests.values()):
            return None
        request_id = str(uuid.uuid4())
        self._requests[request_id] = {'command': command, 'instance': self._verified_instance,
                                      'revision': self._notification_revision,
                                      'dirty': bool(self._data and self.notifications_enabled.isChecked() != self.saved_preference(self._data))}
        self.worker.request(command, dict(values or {}, request_id=request_id))
        self.update_controls()
        return request_id

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        if self._supported is False:
            self.unsupported()
            return
        self.send('core_settings')

    def focus_details(self):
        self.values['application'].setFocus()
        self.scroll.ensureWidgetVisible(self.values['application'])

    def apply_snapshot(self, data):
        features = data.get('capabilities', {}).get('features', {})
        self._supported = features.get('core_settings', self._supported)
        self._restart_supported = features.get('core_restart', self._restart_supported)
        if self._supported is False:
            self.unsupported()
            return
        instance = data.get('instance_id')
        if instance:
            changed = instance != self._verified_instance
            self._verified_instance = instance
            self._connected = True
            if changed:
                self._data = None
                self._status_valid = False
                self.clear_details()
                # A late GET from the previous verified core cannot restore old state.
                for key, pending in list(self._requests.items()):
                    if pending['command'] == 'core_settings':
                        self._requests.pop(key)
                if self._restart_from:
                    self.status.setText('Reconnecting · verifying the new core…')
                else:
                    self.status.setText('Core connected · refresh to load settings')
                if self.isVisible() or self._restart_from:
                    self.refresh()
        self.update_controls()

    def connection_changed(self, message):
        if message.startswith('Connected'):
            self._connected = True
        elif any(word in message.lower() for word in ('disconnect', 'reconnect', 'starting', 'waiting')):
            self._connected = False
            if self._restart_from:
                self.status.setText('Reconnecting…')
            else:
                self.status.setText('Core disconnected · waiting for a verified connection')
        self.update_controls()

    def connection_error(self, message):
        self._connected = False
        self.feedback.setText(message)
        self.update_controls()

    def clear_details(self):
        for value in self.values.values():
            value.setText('Not loaded')
        self.notification_outcome.setText('Delivery availability has not been checked.')

    def unsupported(self):
        self._data = None
        self._status_valid = False
        self._requests.clear()
        self._restart_from = None
        self.restart_timer.stop()
        self.clear_details()
        self.status.setText('Core settings are unavailable in this backend version')
        self.feedback.setText('Start the updated backend externally, then reconnect the desktop. '
                              'This older core cannot expose settings or restart itself.')
        self.update_controls()

    def update_controls(self):
        loading = any(value['command'] == 'core_settings' for value in self._requests.values())
        mutation = any(value['command'] != 'core_settings' for value in self._requests.values())
        valid = bool(self._data and self._status_valid and self._connected and self._supported is not False and
                     self._data.get('instance_id') == self._verified_instance and
                     not self._data.get('restarting'))
        restarting = bool(self._restart_from)
        self.refresh_button.setEnabled(not loading and not mutation)
        self.notifications_enabled.setEnabled(valid and not loading and not mutation and not restarting)
        dirty = bool(self._data and self.notifications_enabled.isChecked() !=
                     self.saved_preference(self._data))
        self.apply_button.setEnabled(valid and dirty and not loading and not mutation and not restarting)
        self.restart_button.setEnabled(valid and self._restart_supported is True and
                                       not loading and not mutation and not restarting)

    def notification_edited(self):
        self._notification_revision += 1
        self.update_controls()

    def apply_notifications(self):
        if not self.apply_button.isEnabled():
            return
        self._operation_error = None
        self.feedback.setText('Saving desktop notification preference…')
        self.send('core_settings_update', {'expected_instance_id': self._verified_instance,
                    'notifications_enabled': self.notifications_enabled.isChecked()})

    def restart_confirmation(self):
        box = QMessageBox(self)
        box.setWindowTitle('Restart core')
        box.setIcon(QMessageBox.Icon.Question)
        box.setText('Restart the shared core backend?')
        box.setInformativeText('Other desktop clients and the local web server will briefly reconnect. '
                               'Unsent drafts stay in this desktop.')
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        restart = box.addButton('Restart core', QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        return box, restart

    def confirm_restart(self):
        box, restart = self.restart_confirmation()
        box.exec()
        return box.clickedButton() is restart

    def restart(self):
        instance = self._verified_instance
        if not self.restart_button.isEnabled() or not self.confirm_restart():
            return
        if not self.restart_button.isEnabled() or self._verified_instance != instance:
            self.feedback.setText('The core changed while confirmation was open. Refresh status and review the new instance.')
            return
        self._operation_error = None
        self._restart_from = self._verified_instance
        self._restart_timed_out = False
        self.status.setText('Restart requested…')
        self.feedback.setText('Waiting for the core to accept the restart request.')
        self.send('core_restart', {'expected_instance_id': self._verified_instance})
        self.restart_timer.start()

    def restart_timeout(self):
        if not self._restart_from:
            return
        self._restart_timed_out = True
        for key, pending in list(self._requests.items()):
            if pending['command'] == 'core_restart':
                self._requests.pop(key)
        self.status.setText('Restart has not been confirmed')
        self.feedback.setText('No new verified core instance has appeared. Refresh status to check the '
                              'backend; the restart request will not be repeated automatically.')
        self.update_controls()

    @staticmethod
    def duration(value):
        if not isinstance(value, (int, float)) or value < 0:
            return 'Not reported'
        seconds = int(value)
        if seconds >= 3600:
            return f'{seconds // 3600}h {(seconds % 3600) // 60}m'
        if seconds >= 60:
            return f'{seconds // 60}m {seconds % 60}s'
        return f'{seconds}s'

    @staticmethod
    def saved_preference(data):
        notifications = data.get('notifications', {})
        return bool(notifications.get('requested', notifications.get('enabled')))

    def render(self, data, revision, preserve_notification=False):
        self._data = data
        self._status_valid = True
        lifecycle = data.get('lifecycle', {})
        addresses, paths = data.get('addresses', {}), data.get('paths', {})
        values = {'instance': data['instance_id'], 'uptime': self.duration(data.get('uptime_seconds')),
                  'core': addresses.get('core') or 'Not reported', 'web': addresses.get('web') or 'Not running',
                  'mode': lifecycle.get('mode') or 'Not reported',
                  'clients': f"{lifecycle.get('desktop_clients', 0)} desktop · {lifecycle.get('web_clients', 0)} web server",
                  'renew': self.duration(lifecycle.get('renew_seconds')),
                  'expiry': self.duration(lifecycle.get('expiry_seconds')),
                  'grace': self.duration(lifecycle.get('release_grace_seconds'))}
        values.update({name: paths.get(name) or 'Not reported' for name in ('application', 'state', 'database')})
        for name, value in values.items():
            self.values[name].setText(str(value))
        notifications = data.get('notifications', {})
        if revision == self._notification_revision and not preserve_notification:
            self.notifications_enabled.blockSignals(True)
            self.notifications_enabled.setChecked(self.saved_preference(data))
            self.notifications_enabled.blockSignals(False)
        available = 'available' if notifications.get('available') else 'unavailable'
        enabled = 'On' if notifications.get('enabled') else 'Off'
        requested = 'On' if self.saved_preference(data) else 'Off'
        self.notification_outcome.setText(f"Saved preference: {requested} · Delivery: {enabled} · Service {available}\n"
                                          + str(notifications.get('outcome') or 'No delivery outcome reported'))

    def result(self, result):
        request_id = result.get('request_id')
        pending = self._requests.get(request_id)
        if not pending or result.get('command') != pending['command']:
            return
        self._requests.pop(request_id)
        command = pending['command']
        if result.get('error'):
            self._operation_error = result['error']
            if result.get('status') == 404:
                self._supported = False
                self.unsupported()
            elif command == 'core_restart' and result.get('ambiguous'):
                self.status.setText('Reconnecting…')
                self.feedback.setText('The restart response was interrupted. Waiting for a new verified '
                                      'core instance; the restart request will not be repeated.')
            else:
                if command == 'core_settings':
                    self._status_valid = False
                    if not self._restart_from:
                        self.status.setText('Status refresh failed · showing last verified details' if self._data else 'Core status could not be loaded')
                if command == 'core_restart':
                    self._restart_from = None
                    self.restart_timer.stop()
                    self.status.setText('Core restart failed')
                self.feedback.setText(result['error'])
                if command != 'core_settings':
                    self.refresh()
            self.update_controls()
            return
        data = result.get('data', {})
        if command == 'core_restart':
            if (data.get('accepted') is True and data.get('instance_id') == self._restart_from
                    and data.get('request_id') == request_id):
                self.status.setText('Reconnecting…')
                self.feedback.setText('Restart accepted. Waiting for a different verified core instance.')
            else:
                self.feedback.setText('The restart acknowledgement did not match this request. '
                                      'Refresh status to check the backend.')
            self.update_controls()
            return
        instance = data.get('instance_id')
        if not isinstance(instance, str) or not instance:
            self._status_valid = False
            self.status.setText('Core status could not be verified')
            self.feedback.setText('Core settings did not include a verified instance. Refresh status to try again.')
            self.update_controls()
            return
        if self._verified_instance and instance != self._verified_instance:
            self._status_valid = False
            self.status.setText('Core status could not be verified')
            self.feedback.setText('The core changed while settings were loading. Refresh status for the current instance.')
            self.update_controls()
            return
        self._verified_instance = instance
        self._connected = True
        self.render(data, pending['revision'], command == 'core_settings' and pending['dirty'])
        if self._restart_from and instance != self._restart_from:
            self._restart_from = None
            self.restart_timer.stop()
            for key, request in list(self._requests.items()):
                if request['command'] == 'core_restart':
                    self._requests.pop(key)
            self.status.setText('Core restarted · connected to a new verified instance')
            self.feedback.setText('The shared core is ready. Your desktop views and unsent drafts are preserved.')
        elif self._restart_from:
            if self._restart_timed_out and not data.get('restarting'):
                self._restart_from = None
                self.feedback.setText('The same core is still running. Restart was not confirmed. '
                                      'You can request another restart explicitly.')
                self.status.setText('Connected · verified core status')
            else:
                self.status.setText('Reconnecting · waiting for a new core instance')
        else:
            self.status.setText('Connected · verified core status')
            if command == 'core_settings_update':
                self.feedback.setText('Desktop notification preference saved.')
            elif data.get('restarting'):
                self.status.setText('Core is restarting · waiting for a verified connection')
            else:
                self.feedback.setText(self._operation_error or 'Status loaded from the authenticated local backend.')
        self.update_controls()
        if command == 'core_settings_update':
            self.refresh()
