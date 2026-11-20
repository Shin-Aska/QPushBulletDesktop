"""First desktop surface: protect, unlock, migrate or recover local history."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QLineEdit, QFileDialog)
from .theme import primary
from .widgets import application_icon


class HistorySecurityDialog(QDialog):
    """Modal dialog guiding local history encryption, unlocking, migration, and recovery."""

    def __init__(self, worker):
        """Initialize history security controls and connect worker status signals.

        :param worker: Core worker managing local history security operations.
        :type worker: pbdesk.desktop.worker.CoreWorker
        """
        super().__init__()
        self.worker = worker
        self.state = {}
        self.busy = False
        self.finished_setup = False
        self.recovering = False
        self.setWindowTitle('Protect your local history')
        self.setWindowIcon(application_icon())
        self.resize(560, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        self.heading = QLabel('Protect your local history')
        self.heading.setObjectName('pageTitle')
        layout.addWidget(self.heading)
        self.description = QLabel('Connecting to the private core…')
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.description)
        self.paths = QLabel()
        self.paths.setTextFormat(Qt.TextFormat.PlainText)
        self.paths.setWordWrap(True)
        layout.addWidget(self.paths)
        self.recovery = QLineEdit()
        self.recovery.setEchoMode(QLineEdit.EchoMode.Password)
        self.recovery.setPlaceholderText('Recovery key')
        self.recovery.setAccessibleName('Local history recovery key')
        self.recovery.setMaxLength(16384)
        self.recovery.hide()
        layout.addWidget(self.recovery)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        layout.addStretch()
        self.primary = primary(QPushButton('Retry connection'))
        self.primary.clicked.connect(self.perform)
        self.backup = QPushButton('Save recovery key…')
        self.backup.clicked.connect(self.save_key)
        self.backup.hide()
        layout.addWidget(self.backup)
        layout.addWidget(self.primary)
        self.recover_button = QPushButton('Use a recovery key')
        self.recover_button.clicked.connect(self.show_recovery)
        self.recover_button.hide()
        layout.addWidget(self.recover_button)
        self.import_button = QPushButton('Import recovery key file…')
        self.import_button.clicked.connect(self.import_key)
        self.import_button.hide()
        layout.addWidget(self.import_button)
        row = QHBoxLayout()
        self.refresh = QPushButton('Refresh status')
        self.refresh.clicked.connect(lambda: self.submit('history_status'))
        row.addWidget(self.refresh)
        row.addStretch()
        cancel = QPushButton('Exit desktop')
        cancel.clicked.connect(self.reject)
        for button in (cancel, self.refresh, self.recover_button, self.import_button):
            button.setAutoDefault(False)
        row.addWidget(cancel)
        layout.addLayout(row)
        worker.history_security.connect(self.update_security)
        worker.result.connect(self.command_result)
        worker.failed.connect(self.show_error)

    def show_error(self, message):
        """Display an error message, clear busy state, and re-enable controls.

        :param message: Error text explaining why the operation failed.
        :type message: str
        """
        self.busy = False
        self.error.setText(message)
        self.enable_controls()

    def enable_controls(self):
        """Enable or disable interactive controls based on the current busy state."""
        for control in (self.primary, self.backup, self.refresh, self.recover_button, self.import_button, self.recovery):
            control.setEnabled(not self.busy)

    def update_security(self, state):
        """Update prompts, buttons, and visibility from the latest history security state.

        :param state: History security status payload received from the core worker.
        :type state: dict
        """
        self.state = state
        self.busy = bool(state.get("busy"))
        if state.get('history_ready'):
            if not self.finished_setup:
                self.finished_setup = True
                self.accept()
            return
        pending = state.get('unlocked') and state.get('recovery_offer_pending')
        kind = state.get('state', 'unavailable')
        title, description, action = {
            'new': ('Protect your local history', 'pbdesk encrypts notifications, SMS, settings and AI summaries on this computer. Authorize your existing KWallet to create a dedicated random database key. Pushbullet account setup follows after your recovery choice.', 'Protect local history'),
            'plaintext': ('Encrypt existing history', 'Your existing history must be encrypted before pbdesk can open it. Authorize KWallet to create a dedicated random database key. Migration verifies retained data before switching to encrypted storage. Migration verifies encrypted copies before removing the affected plaintext originals. Older external backups remain your responsibility.', 'Encrypt existing history'),
            'locked': ('Unlock your local history', 'Authorize KWallet to unlock the encrypted history on this computer.', 'Unlock with KWallet'),
            'ready': ('Unlock your local history', 'Authorize KWallet to open your encrypted history.', 'Unlock with KWallet'),
            'recovery_required': ('Recover your local history', 'The database key is unavailable. Import your recovery key to verify the encrypted history and save a new dedicated KWallet entry.', 'Recover history'),
            'migration_incomplete': ('Finish encrypting history', 'A previous migration was interrupted. Retry to verify and resume it safely. The listed paths require attention.', 'Retry encryption'),
            'conflict': ('Local history needs attention', 'Conflicting storage files were detected. Resolve the listed paths before continuing; pbdesk will not replace them.', 'Refresh status'),
            'unavailable': ('Encrypted history is unavailable', 'Install the required SQLCipher driver for this pbdesk environment, then restart the core. No plaintext fallback is available.', 'Refresh status'),
        }.get(kind, ('Local history needs attention', 'Refresh status to retry.', 'Refresh status'))
        if pending:
            title = 'Back up your recovery key'
            description = 'Save the recovery key somewhere safe outside this computer. It can unlock your history if KWallet is lost. Anyone with this key and the database can read your history. You can explicitly continue without a backup.'
            action = 'Continue without backup'
        self.heading.setText(title)
        self.setWindowTitle(title)
        self.description.setText(description)
        self.primary.setText('Recover history' if self.recovering and not pending else action)
        self.paths.setText('\n'.join(state.get('paths', [])) if kind in {'plaintext', 'migration_incomplete', 'conflict'} else '')
        self.backup.setVisible(bool(pending))
        self.backup.setAutoDefault(bool(pending))
        self.backup.setDefault(bool(pending))
        self.primary.setAutoDefault(not pending)
        self.primary.setDefault(not pending)
        for button, emphasized in ((self.backup, bool(pending)), (self.primary, not pending)):
            button.setProperty('primary', emphasized)
            button.style().unpolish(button)
            button.style().polish(button)
        recover = kind in {'locked', 'ready', 'recovery_required'} and not pending
        self.recover_button.setVisible(recover and not self.recovering)
        self.recovery.setVisible(recover and (self.recovering or kind == 'recovery_required'))
        self.import_button.setVisible(self.recovery.isVisible())
        self.error.setText(state.get('error') or '')
        self.enable_controls()

    def submit(self, command, value=None):
        """Send a history security command through the worker if not already busy.

        :param command: History security worker command name.
        :type command: str
        :param value: Optional command payload parameters.
        :type value: dict | None
        """
        if self.busy:
            return
        self.busy = True
        self.error.setText('Waiting for KWallet authorization…' if command in {'history_protect', 'history_unlock', 'history_migrate', 'history_recover'} else 'Working…')
        self.enable_controls()
        self.worker.request(command, value)

    def perform(self):
        """Execute the primary history security action for the current state."""
        if not self.worker.isRunning():
            self.worker.stop_event.clear()
            self.worker.start()
            return
        if self.state.get('unlocked') and self.state.get('recovery_offer_pending'):
            self.submit('history_finish', {'saved': False})
            return
        kind = self.state.get('state')
        if self.recovering or kind == 'recovery_required':
            key = self.recovery.text()
            self.recovery.clear()
            self.submit('history_recover', {'recovery_key': key})
            return
        self.submit({'new': 'history_protect', 'plaintext': 'history_migrate', 'migration_incomplete': 'history_migrate',
                     'locked': 'history_unlock', 'ready': 'history_unlock'}.get(kind, 'history_status'))

    def show_recovery(self):
        """Switch the dialog into manual recovery key entry mode."""
        self.recovering = True
        self.update_security(self.state)
        self.recovery.setFocus()

    def import_key(self):
        """Prompt for a recovery key file path and submit a recovery request."""
        path, _ = QFileDialog.getOpenFileName(self, 'Import local history recovery key')
        if path:
            self.submit('history_recover', {'path': path})

    def save_key(self):
        """Prompt for a destination path and save the recovery key backup."""
        path, _ = QFileDialog.getSaveFileName(self, 'Save local history recovery key', 'pbdesk-recovery.txt')
        if path:
            self.submit('history_save', {'path': path})

    def command_result(self, result):
        """Handle worker results for history_* commands and advance setup flow.

        :param result: Worker result dictionary emitted after a command finishes.
        :type result: dict
        """
        if not result.get('command', '').startswith('history_'):
            return
        self.busy = False
        self.enable_controls()
        if result.get('error'):
            self.show_error(result['error'])
            return
        if result['command'] == 'history_save':
            self.submit('history_finish', {'saved': True})
        else:
            self.update_security(result.get('data', {}))

    def reject(self):
        """Stop the worker when exiting before history setup finishes."""
        if not self.finished_setup:
            self.worker.stop()
        super().reject()
