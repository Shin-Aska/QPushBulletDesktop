"""First-run widgets and explicit key export use only fictional fixtures."""
import os
import httpx
from PyQt6.QtCore import QObject, pyqtSignal
from pbdesk.desktop.history_security import HistorySecurityDialog
from pbdesk.desktop.worker import CoreWorker
from pbdesk.config import Settings


class Worker(QObject):
    history_security = pyqtSignal(dict)
    result = pyqtSignal(dict)
    failed = pyqtSignal(str)
    def __init__(self):
        super().__init__()
        self.requests = []
        self.stopped = False
    def request(self, command, value=None): self.requests.append((command, value))
    def stop(self): self.stopped = True
    def isRunning(self): return True


def test_protection_precedes_recovery_and_account_ready(qtbot):
    worker = Worker()
    dialog = HistorySecurityDialog(worker)
    qtbot.addWidget(dialog)
    dialog.show()
    worker.history_security.emit({'state': 'new', 'recovery_offer_pending': False, 'history_ready': False})
    assert dialog.heading.text() == 'Protect your local history'
    dialog.perform()
    assert worker.requests == [('history_protect', None)]
    worker.result.emit({'command': 'history_protect', 'data': {'state': 'ready', 'unlocked': True, 'recovery_offer_pending': True, 'history_ready': False}})
    assert dialog.heading.text() == 'Back up your recovery key'
    assert dialog.backup.property('primary') and dialog.backup.isDefault()
    assert not dialog.primary.property('primary') and not dialog.primary.isDefault()
    assert not dialog.primary.autoDefault()
    assert dialog.layout().indexOf(dialog.backup) < dialog.layout().indexOf(dialog.primary)
    assert not dialog.finished_setup
    dialog.perform()
    assert worker.requests[-1] == ('history_finish', {'saved': False})
    worker.result.emit({'command': 'history_finish', 'data': {'history_ready': True}})
    assert dialog.finished_setup


def test_unlock_error_retry_recover_is_masked_and_cleared(qtbot):
    from PyQt6.QtWidgets import QLineEdit
    worker = Worker()
    dialog = HistorySecurityDialog(worker)
    qtbot.addWidget(dialog)
    worker.history_security.emit({'state': 'locked', 'history_ready': False})
    dialog.perform()
    assert worker.requests[-1][0] == 'history_unlock'
    worker.result.emit({'command': 'history_unlock', 'error': 'Authorization cancelled; retry'})
    assert dialog.primary.isEnabled()
    dialog.show_recovery()
    assert dialog.recovery.echoMode() == QLineEdit.EchoMode.Password
    dialog.recovery.setText('fictional-recovery-bundle')
    dialog.perform()
    assert dialog.recovery.text() == ''
    assert worker.requests[-1] == ('history_recover', {'recovery_key': 'fictional-recovery-bundle'})
    dialog.reject()
    assert worker.stopped


def test_migration_preview_contains_paths_only(qtbot):
    worker = Worker()
    dialog = HistorySecurityDialog(worker)
    qtbot.addWidget(dialog)
    worker.history_security.emit({'state': 'plaintext', 'paths': ['/fictional/pbdesk.sqlite'], 'history_ready': False})
    assert dialog.heading.text() == 'Encrypt existing history'
    assert dialog.paths.text() == '/fictional/pbdesk.sqlite'
    dialog.perform()
    assert worker.requests[-1][0] == 'history_migrate'


def test_export_only_explicit_chosen_file_is_private_no_key_signal(tmp_path, monkeypatch):
    worker = CoreWorker(Settings(tmp_path / 'state'))
    key = 'fictional-recovery-only-for-test'
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.path == '/api/v1/history/recovery-key'
        return httpx.Response(200, json={'recovery_key': key})
    path = tmp_path / 'chosen-backup'
    import stat
    original_fsync = os.fsync
    synced = []
    def record_sync(fd):
        synced.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
        original_fsync(fd)
    monkeypatch.setattr('pbdesk.desktop.worker.os.fsync', record_sync)
    with httpx.Client(base_url='http://fixture', transport=httpx.MockTransport(handle)) as client:
        result = worker._history_request(client, {}, 'history_save', {'path': str(path)})
        assert result == {'saved': True}
        assert synced == ['file', 'directory']
        assert key not in repr(result)
        assert path.read_text() == key + '\n'
        assert path.stat().st_mode & 0o777 == 0o600
        import pytest
        with pytest.raises(FileExistsError):
            worker._history_request(client, {}, 'history_save', {'path': str(path)})
    assert len(calls) == 2


def test_recovery_offer_enter_selects_backup_never_declines(qtbot, monkeypatch):
    from PyQt6.QtCore import Qt
    monkeypatch.setattr('pbdesk.desktop.history_security.QFileDialog.getSaveFileName',
        lambda *args, **kwargs: ('/fictional/user-chosen-backup', ''))
    worker = Worker()
    dialog = HistorySecurityDialog(worker)
    qtbot.addWidget(dialog)
    dialog.update_security({'state': 'ready', 'unlocked': True, 'recovery_offer_pending': True})
    dialog.show()
    qtbot.keyClick(dialog, Qt.Key.Key_Return)
    assert worker.requests == [('history_save', {'path': '/fictional/user-chosen-backup'})]
    assert not dialog.finished_setup


def test_export_directory_sync_failure_never_confirms_recovery_backup(qtbot, tmp_path, monkeypatch):
    import threading
    from pbdesk.credentials import health_response
    settings = Settings(tmp_path / 'state')
    key = 'fictional-recovery-material'
    calls, results = [], []
    actual = httpx.Client
    class Lease:
        def __init__(self, *args, **kwargs):
            self.stop_event = threading.Event()
        def start(self): return self
        def close(self): pass
    def handle(request):
        calls.append(request.url.path)
        if request.url.path == '/api/v1/health':
            return httpx.Response(200, json=health_response(settings, request.headers,
                '11111111-1111-1111-1111-111111111111', True, history_ready=False))
        assert request.url.path == '/api/v1/history/recovery-key'
        return httpx.Response(200, json={'recovery_key': key})
    def failed_sync(path):
        assert path == tmp_path
        raise OSError('fictional durability failure')
    monkeypatch.setattr('pbdesk.desktop.worker.ClientLease', Lease)
    monkeypatch.setattr('pbdesk.desktop.worker.httpx.Client', lambda **kwargs: actual(**kwargs, transport=httpx.MockTransport(handle)))
    monkeypatch.setattr('pbdesk.desktop.worker.sync_directory', failed_sync)
    worker = CoreWorker(settings)
    worker.result.connect(lambda result: (results.append(result), worker.stop()))
    worker.request('history_save', {'path': str(tmp_path / 'backup')})
    worker.run()
    assert len(results) == 1 and results[0].get('error')
    assert 'data' not in results[0] and key not in repr(results)
    assert calls == ['/api/v1/health', '/api/v1/history/recovery-key']
    ui_worker = Worker()
    dialog = HistorySecurityDialog(ui_worker)
    qtbot.addWidget(dialog)
    dialog.update_security({'state': 'ready', 'unlocked': True, 'recovery_offer_pending': True})
    dialog.command_result(results[0])
    assert not dialog.finished_setup
    assert not ui_worker.requests
    assert dialog.error.text()
