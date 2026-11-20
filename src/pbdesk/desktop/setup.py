"""Native first-run connection setup through the authenticated core worker."""
import uuid
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QPushButton, QLabel
from .actions import plain_label
from .theme import primary


class AccountSetup(QWidget):
    """Settings view for selecting a KWallet entry or storing a new Pushbullet token."""

    def __init__(self, worker, parent=None):
        """Initialize connection setup fields, buttons, and worker signal bindings.

        :param worker: Core worker handling configuration reads and provider setup.
        :type worker: pbdesk.desktop.worker.CoreWorker
        :param parent: Optional parent widget owning this settings tab.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.worker = worker
        self.revision = 0
        self.pending = None
        self._source = None
        self._dirty = False
        self._updating = False
        layout = QVBoxLayout(self)
        title = QLabel('Connect Pushbullet')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        description = plain_label('Choose a KWallet entry or save a new access token. Settings are saved in this application’s database; the token stays in KWallet.')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.mode = QComboBox()
        self.mode.addItem('Use an existing KWallet entry', 'existing')
        self.mode.addItem('Save a new access token in KWallet', 'new')
        layout.addWidget(self.mode)
        form = QFormLayout()
        self.wallet = QLineEdit('kdewallet')
        self.folder = QLineEdit('Pushbullet')
        self.entry = QLineEdit('api-key')
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setMaxLength(4096)
        self.token.setPlaceholderText('Access token from your Pushbullet account settings')
        for name, field in [('Wallet', self.wallet), ('Folder', self.folder), ('Entry', self.entry), ('Access token', self.token)]:
            field.setAccessibleName(name)
            if field is not self.token:
                field.setMaxLength(1024)
            form.addRow(name, field)
        layout.addLayout(form)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.mode.currentIndexChanged.connect(self._edited)
        for field in (self.wallet, self.folder, self.entry, self.token):
            field.textEdited.connect(self._edited)
        self._mode_changed()
        self.feedback = plain_label('Use an existing wallet. You may be asked to unlock it or approve access.')
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.connect_button = primary(QPushButton('Save and connect'))
        self.connect_button.clicked.connect(self.submit)
        layout.addWidget(self.connect_button)
        self.refresh_button = QPushButton('Reload saved connection')
        self.refresh_button.clicked.connect(lambda: worker.request('configuration_get', {'request_id': str(uuid.uuid4())}))
        layout.addWidget(self.refresh_button)
        note = plain_label('Existing entries are preserved. Device encryption and AI are optional and can be configured in their Settings tabs.')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        worker.result.connect(self._result)

    def _edited(self, *unused):
        """Mark the form dirty when the user modifies connection inputs.

        :param unused: Ignored signal arguments emitted by Qt input controls.
        :type unused: object
        """
        if not self._updating:
            self._dirty = True

    def _mode_changed(self):
        """Enable the token input only when creating a new KWallet entry."""
        self.token.setEnabled(self.mode.currentData() == 'new' and self.pending is None)
        if self.mode.currentData() != 'new':
            self.token.clear()

    def apply_configuration(self, configuration):
        """Update form fields, revision, and feedback from core configuration data.

        :param configuration: Core configuration snapshot dictionary containing ``setup``.
        :type configuration: dict
        """
        setup = configuration['setup']
        source = setup.get('source')
        if self.pending is None and not self._dirty:
            self.revision = setup['revision']
            if source and source.get('backend') == 'kwallet':
                self.wallet.setText(source.get('wallet') or 'kdewallet')
                self.folder.setText(source['folder'])
                self.entry.setText(source['entry'])
        self._source = source
        self.connect_button.setEnabled(setup.get('enabled', True) and self.pending is None)
        if setup.get('error'):
            self.feedback.setText(setup['error'])
        elif self._dirty and setup['revision'] != self.revision:
            self.feedback.setText('The saved connection changed. Reload it before saving this form.')
        elif setup.get('completed') and self.pending is None and not self._dirty:
            self.feedback.setText('Your saved connection is established. Unlock KWallet and reload credentials if connection access is locked.')

    def submit(self):
        """Validate wallet and token inputs and submit configure_provider to core."""
        if self.pending:
            return
        value = {'wallet': self.wallet.text().strip(), 'folder': self.folder.text().strip(),
            'entry': self.entry.text().strip(), 'expected_revision': self.revision}
        if not all(value[field] for field in ('wallet', 'folder', 'entry')):
            self.feedback.setText('Enter the wallet, folder and entry names.')
            return
        if self.mode.currentData() == 'new':
            value['token'] = self.token.text().strip()
            if not value['token']:
                self.feedback.setText('Enter a Pushbullet access token.')
                return
        self.pending = str(uuid.uuid4())
        for field in (self.wallet, self.folder, self.entry, self.mode, self.token, self.connect_button, self.refresh_button):
            field.setEnabled(False)
        self.feedback.setText('Verifying connection… Unlock KWallet or approve access if prompted.')
        self.worker.request('configure_provider', {**value, 'request_id': self.pending})
        self.token.clear()

    def _result(self, result):
        """Process configuration_get and configure_provider responses from the worker.

        :param result: Worker result dictionary emitted after a core command completes.
        :type result: dict
        """
        if result.get('command') == 'configuration_get' and isinstance(result.get('data'), dict):
            self._dirty = False
            self.apply_configuration(result['data'])
            return
        if result.get('command') != 'configure_provider' or result.get('request_id') != self.pending:
            return
        self.pending = None
        for field in (self.wallet, self.folder, self.entry, self.mode, self.connect_button, self.refresh_button):
            field.setEnabled(True)
        self._mode_changed()
        if result.get('error'):
            self.feedback.setText(result['error'])
        else:
            self._dirty = False
            self.apply_configuration(result['data'])
            self.feedback.setText('Connection saved. Pushbullet synchronization is starting.')
