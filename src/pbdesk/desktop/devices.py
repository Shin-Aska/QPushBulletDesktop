"""Native device management; every credential and provider operation belongs to core."""
import uuid
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QTabWidget, QWidget, QLineEdit, QPushButton, QFormLayout, QRadioButton, QCheckBox, QHBoxLayout, QInputDialog
from .actions import plain_label
from .theme import primary


def request(worker, command, **values):
    request_id = str(uuid.uuid4())
    worker.request(command, dict(values, request_id=request_id))
    return request_id


class AddDeviceDialog(QDialog):
    registered = pyqtSignal(str)

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.request_id = None
        self.setWindowTitle('Add device')
        self.resize(570, 420)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        computer, other = QWidget(), QWidget()
        form = QVBoxLayout(computer)
        form.addWidget(plain_label('Register pbdesk on this computer'))
        form.addWidget(plain_label('Add this Linux client to your Pushbullet account.'))
        self.nickname = QLineEdit()
        self.nickname.setPlaceholderText('Device name')
        self.nickname.setMaxLength(128)
        self.nickname.setAccessibleName('Device name')
        form.addWidget(self.nickname)
        form.addWidget(plain_label('This creates a registration for this computer. A phone registers through its own client.'))
        self.register = primary(QPushButton('Register this computer'))
        self.register.setEnabled(False)
        self.nickname.textChanged.connect(lambda text: self.register.setEnabled(bool(text.strip())))
        self.register.clicked.connect(self.submit)
        form.addStretch()
        form.addWidget(self.register)
        guide = QVBoxLayout(other)
        guide.addWidget(plain_label('Connect a phone or browser'))
        guide.addWidget(plain_label('1  Set up the Pushbullet client on that device.\n\n2  Sign in to the same Pushbullet account.\n\n3  On Android, allow SMS and notification permissions.'))
        guide.addWidget(plain_label('The client registers itself. Then refresh to find it here.'))
        downloads = QPushButton('Open client downloads')
        downloads.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://www.pushbullet.com/apps')))
        guide.addWidget(downloads)
        refresh = primary(QPushButton('Refresh devices'))
        refresh.clicked.connect(lambda: request(worker, 'refresh_devices'))
        guide.addWidget(refresh)
        tabs.addTab(computer, 'This Linux computer')
        tabs.addTab(other, 'Phone or browser')
        layout.addWidget(tabs)
        self.feedback = plain_label()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        worker.result.connect(self.result)
        self.finished.connect(lambda: worker.result.disconnect(self.result))

    def submit(self):
        self.register.setEnabled(False)
        self.nickname.setEnabled(False)
        self.request_id = request(self.worker, 'add_device', nickname=self.nickname.text().strip())
        self.feedback.setText('Registering this Linux client…')

    def result(self, result):
        if result.get('request_id') != self.request_id:
            return
        if result.get('error'):
            self.feedback.setText(result['error'])
            self.register.setEnabled(not result.get('ambiguous'))
            self.nickname.setEnabled(not result.get('ambiguous'))
        else:
            device_id = result.get('data', {}).get('current_device_id')
            if device_id:
                self.registered.emit(device_id)
            self.accept()


class EncryptionDialog(QDialog):
    def __init__(self, worker, device, parent=None):
        super().__init__(parent)
        self.worker, self.device = worker, device
        self.request_id = None
        self.pending_command = None
        self.setWindowTitle('Encryption · ' + (device.get('nickname') or device['id']))
        self.resize(610, 470)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label('Incoming decryption for this device'))
        layout.addWidget(plain_label('Choose which local KWallet entry pbdesk uses.'))
        self.shared = QRadioButton('Use shared default')
        self.specific = QRadioButton('Use a device-specific entry')
        encryption = device.get('encryption', {})
        self.shared.setChecked(encryption.get('mode', 'shared') == 'shared')
        self.specific.setChecked(not self.shared.isChecked())
        layout.addWidget(self.shared)
        layout.addWidget(plain_label('kdewallet → Pushbullet → e2e-password'))
        layout.addWidget(self.specific)
        self.entry = QLineEdit(encryption.get('entry') if encryption.get('mode') == 'device' else 'e2e-' + device['id'])
        self.entry.setAccessibleName('Device-specific KWallet entry')
        row = QHBoxLayout()
        row.addWidget(self.entry, 1)
        create = QPushButton('Create entry…')
        row.addWidget(create)
        layout.addLayout(row)
        self.password = None
        def create_entry():
            # Password is masked and passed directly to core; never persisted in UI snapshots.
            password, ok = QInputDialog.getText(self, 'Create local KWallet entry', 'Password configured in the source client', QLineEdit.EchoMode.Password)
            if ok and password:
                self.password = password
                self.specific.setChecked(True)
                self.feedback.setText('New entry will be written to KWallet when you save selection.')
        create.clicked.connect(create_entry)
        self.shared.toggled.connect(lambda checked: self.entry.setEnabled(not checked))
        self.entry.setEnabled(self.specific.isChecked())
        layout.addWidget(plain_label('Use the password configured in the source client. This changes pbdesk only; it does not configure the phone.'))
        self.test = QPushButton('Test saved local decryption')
        self.test.clicked.connect(lambda: self.submit('test_encryption'))
        layout.addWidget(self.test)
        self.feedback = plain_label('Save a changed selection before testing. Key readiness is separate from a successful decryption test.')
        layout.addWidget(self.feedback)
        layout.addStretch()
        layout.addWidget(plain_label('Passwords stay in KWallet, not the history database.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.save = primary(buttons.addButton('Save selection', QDialogButtonBox.ButtonRole.ActionRole))
        self.save.clicked.connect(lambda: self.submit('encryption'))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        worker.result.connect(self.result)
        self.finished.connect(self.cleanup)

    def cleanup(self):
        self.password = None
        self.worker.result.disconnect(self.result)

    def submit(self, command):
        values = {'device_id': self.device['id']}
        if command == 'encryption':
            values.update(mode='shared' if self.shared.isChecked() else 'device')
            if values['mode'] == 'device':
                values['entry'] = self.entry.text().strip()
                if not values['entry'] or values['entry'] in {'api-key', 'e2e-password'}:
                    self.feedback.setText('Choose a device-specific encryption entry other than api-key.')
                    return
            if self.password is not None and values['mode'] == 'device':
                values['password'] = self.password
        self.pending_command = command
        self.request_id = request(self.worker, command, **values)
        self.password = None
        self.save.setEnabled(False)
        self.test.setEnabled(False)
        self.feedback.setText('Testing saved local decryption…' if command == 'test_encryption' else 'Saving local KWallet reference…')

    def result(self, result):
        if result.get('request_id') != self.request_id:
            return
        self.save.setEnabled(True)
        self.test.setEnabled(True)
        if result.get('error'):
            self.feedback.setText(result['error'])
        elif self.pending_command == 'test_encryption':
            data = result['data']
            self.feedback.setText(('Verified · sample decrypted locally.' if data.get('verified') else 'Not verified · ') + ('' if data.get('verified') else data.get('detail', 'No matching encrypted sample is available.')))
        else:
            self.feedback.setText('Local selection saved. Test saved local decryption to verify a matching sample.')


class RemoveDeviceDialog(QDialog):
    def __init__(self, worker, device, parent=None):
        super().__init__(parent)
        self.worker, self.device = worker, device
        self.request_id = None
        self.setWindowTitle('Remove device')
        self.resize(540, 330)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label('Remove ' + (device.get('nickname') or device['id']) + '?'))
        layout.addWidget(plain_label((device.get('nickname') or '(unnamed)') + ' · ' + device['id']))
        layout.addWidget(plain_label("This removes the device's Pushbullet registration. It does not erase messages from the phone."))
        layout.addWidget(plain_label('Local SMS history will be kept.'))
        layout.addWidget(plain_label('The shared default KWallet entry is kept.\nSaved actions keep their original destination; they are not rerouted.'))
        self.feedback = plain_label()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox()
        cancel = buttons.addButton('Keep device', QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setDefault(True)
        self.remove = buttons.addButton('Remove device', QDialogButtonBox.ButtonRole.ActionRole)
        self.remove.setProperty('danger', True)
        self.remove.clicked.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        worker.result.connect(self.result)
        self.finished.connect(lambda: worker.result.disconnect(self.result))

    def submit(self):
        self.remove.setEnabled(False)
        self.request_id = request(self.worker, 'remove_device', device_id=self.device['id'])
        self.feedback.setText('Removing this registration…')

    def result(self, result):
        if result.get('request_id') == self.request_id:
            if result.get('error'):
                self.feedback.setText(result['error'])
            else:
                self.accept()
