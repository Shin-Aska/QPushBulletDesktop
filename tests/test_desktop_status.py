"""Status timing and account/core boundaries, with only private fake data."""
from test_desktop import isolated_desktop_preferences
from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal
import pytest

from pbdesk.desktop.app import MainWindow
from pbdesk.desktop.status import DesktopStatus, merge_capabilities


class FakeWorker(QObject):
    snapshot = pyqtSignal(dict)
    connection = pyqtSignal(str)
    failed = pyqtSignal(str)
    result = pyqtSignal(dict)
    stopped_all = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.requests = []

    def request(self, *args):
        self.requests.append(args)

    def stop(self):
        pass

    def isRunning(self):
        return True


CAP = {'account_id': 'fictional-a', 'provider_state': 'connected', 'encryption_state': 'ready',
       'encrypted_pending': 0, 'features': {'send': True},
       'push_sync': {'state': 'idle', 'last_success_at': '2026-11-14T00:00:00Z', 'error': None}}


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    widget = MainWindow(worker)
    qtbot.addWidget(widget)
    widget.show()
    yield widget, worker
    widget.exit_desktop()


def test_reconnect_and_refresh_delay_does_not_restart_on_polls():
    now = [0.0]
    status = DesktopStatus(clock=lambda: now[0])
    status.snapshot(CAP)
    assert status.view().label == 'Live updates'
    status.snapshot({'provider_state': 'reconnecting'})
    assert status.view().label == 'Live updates'
    assert status.view().next_update == 3.0
    now[0] = 2.0
    status.snapshot({'provider_state': 'reconnecting'})
    assert status.view().next_update == 3.0
    now[0] = 3.0
    assert status.view().label == 'Reconnecting…'
    status.snapshot({'provider_state': 'connected'})
    assert status.view().label == 'Live updates'
    status.snapshot({'push_sync': {'state': 'refreshing'}})
    assert status.view().label == 'Live updates'
    now[0] = 6.0
    assert status.view().label == 'Refreshing…'
    status.snapshot({'push_sync': {'state': 'idle'}})
    assert status.view().label == 'Live updates'
    assert not status.transients


@pytest.mark.parametrize(('patch', 'label'), [
    ({'provider_state': 'unauthorized'}, 'Credentials needed'),
    ({'provider_state': 'rate_limited'}, 'Rate limited'),
    ({'push_sync': {'state': 'rate_limited'}}, 'Rate limited'),
    ({'encryption_state': 'error'}, 'Decryption error'),
    ({'encryption_state': 'locked', 'encrypted_pending': 2}, 'Encryption locked'),
    ({'encryption_state': 'unconfigured', 'encrypted_pending': 2}, 'Encryption locked'),
    ({'push_sync': {'state': 'error', 'error': 'Fictional error'}}, 'Sync needs attention'),
])
def test_actionable_states_are_immediate(patch, label):
    status = DesktopStatus()
    status.snapshot(CAP)
    status.snapshot(patch)
    assert status.view().label == label
    assert status.view().tone in {'warn', 'error'}


def test_partial_capabilities_preserve_timestamp_features_and_key_state():
    result = merge_capabilities(CAP, {'push_sync': {'state': 'refreshing'}, 'features': {'devices': True}})
    assert result['push_sync']['last_success_at'] == CAP['push_sync']['last_success_at']
    assert result['encryption_state'] == 'ready'
    assert result['features'] == {'send': True, 'devices': True}
    assert CAP['push_sync']['state'] == 'idle'


def test_timer_promotes_without_new_snapshot_and_recovery_is_immediate(window, qtbot):
    widget, worker = window
    widget.connection_status.delay = .06
    worker.snapshot.emit({'capabilities': CAP})
    worker.snapshot.emit({'capabilities': {'provider_state': 'reconnecting'}})
    assert widget.provider_status.text() == 'Live updates'
    qtbot.waitUntil(lambda: widget.provider_status.text() == 'Reconnecting…', timeout=1000)
    worker.snapshot.emit({'capabilities': {'provider_state': 'connected'}})
    assert widget.provider_status.text() == 'Live updates'
    assert not widget.status_timer.isActive()
    worker.snapshot.emit({'capabilities': {'push_sync': {'state': 'refreshing'}}})
    assert 'Refreshing' not in widget.inbox_sync.text()
    qtbot.waitUntil(lambda: 'Refreshing' in widget.inbox_sync.text(), timeout=1000)
    worker.snapshot.emit({'capabilities': {'push_sync': {'state': 'idle'}}})
    assert 'Refreshing' not in widget.inbox_sync.text()


def test_core_loss_overrides_stale_connected_capabilities_and_command_error_does_not(window):
    widget, worker = window
    worker.snapshot.emit({'capabilities': CAP})
    widget.show_error('A fictional action could not be confirmed')
    assert widget.provider_status.text() == 'Live updates'
    worker.connection.emit('Disconnected · waiting for core')
    assert widget.provider_status.text() == 'Core unavailable'
    worker.snapshot.emit({'items': []})
    assert widget.provider_status.text() == 'Core unavailable'
    worker.failed.emit('Core refresh failed: <img src="https://invalid.example/image">')
    assert '&lt;img' in widget.provider_status.toolTip()
    assert '<img' not in widget.provider_status.toolTip()
    worker.snapshot.emit({'capabilities': {'provider_state': 'connected'}})
    assert widget.provider_status.text() == 'Live updates'
    assert widget.capabilities['encryption_state'] == 'ready'


def test_account_reset_removes_stale_capabilities_errors_and_transient_timer(window):
    widget, worker = window
    worker.snapshot.emit({'capabilities': CAP})
    worker.snapshot.emit({'capabilities': {'provider_state': 'reconnecting'}})
    widget.command_error = True
    widget.show_error('Previous fictional account error')
    assert widget.status_timer.isActive()
    worker.snapshot.emit({'capabilities': {'account_id': 'fictional-b'}})
    assert widget.capabilities == {'account_id': 'fictional-b'}
    assert widget.provider_status.text() == 'Checking updates'
    assert not widget.status_timer.isActive()
    assert not widget.connection_status.transients
    assert 'unknown' in widget.provider_status.accessibleDescription()
    assert '2026-11-14' not in widget.provider_status.accessibleDescription()
    assert not widget.command_error and widget.error.isHidden()


def test_status_keyboard_details_and_layout_are_stable(window, qtbot):
    widget, worker = window
    for size in [(850, 540), (1200, 790)]:
        widget.resize(*size)
        qtbot.wait(20)
        for cap in [CAP, dict(CAP, provider_state='unauthorized'),
                    dict(CAP, encryption_state='locked', encrypted_pending=99999)]:
            worker.snapshot.emit({'capabilities': cap})
            qtbot.wait(10)
            assert widget.size().width() == size[0]
            assert widget.provider_status.width() == 154
            for button in [widget.provider_status, widget.activity_button, widget.compose_button]:
                position = widget.mapFromGlobal(button.mapToGlobal(QPoint(0, 0)))
                assert button.isVisible()
                assert 0 <= position.x() < position.x() + button.width() <= widget.width()
        widget.provider_status.setFocus()
        qtbot.keyClick(widget.provider_status, Qt.Key.Key_Space)
        assert widget.status_dialog is not None
        text = widget.status_dialog.toPlainText()
        for part in ['Local core:', 'Provider stream:', 'Push synchronization:',
                     'Last push synchronization:', 'Incoming encryption / key readiness:', '99999 encrypted items waiting']:
            assert part in text
        assert 'all history' not in widget.provider_status.text().casefold()
        widget.dialogs[-1].reject()


def test_sync_metadata_stays_single_line_without_moving_content(window, qtbot):
    widget, worker = window
    widget.connection_status.delay = 0
    worker.snapshot.emit({'capabilities': {'account_id': CAP['account_id']},
                          'items': [{'id': 1, 'type': 'note', 'title': 'Fictional note',
                                     'body': 'Complete supplied text', 'created_at': '2026-11-14T00:00:00Z'}]})
    assert widget.inbox_sync.text() == 'Stored on this computer'
    assert not widget.inbox_sync.wordWrap()
    for width in [850, 1280]:
        widget.resize(width, 790)
        qtbot.wait(20)
        top = widget.list.mapTo(widget, QPoint(0, 0)).y()
        height = widget.inbox_sync.height()
        for capabilities in [CAP,
                             dict(CAP, push_sync=dict(CAP['push_sync'], state='refreshing')),
                             dict(CAP, provider_state='reconnecting'),
                             dict(CAP, push_sync={'state': 'error', 'last_success_at': None,
                                                  'error': 'Fictional diagnostic detail ' * 100}),
                             CAP]:
            worker.snapshot.emit({'capabilities': capabilities})
            qtbot.wait(10)
            assert widget.width() == width
            assert widget.inbox_sync.height() == height
            assert widget.list.mapTo(widget, QPoint(0, 0)).y() == top
            assert widget.inbox_sync.fontMetrics().horizontalAdvance(widget.inbox_sync.text()) <= widget.inbox_sync.contentsRect().width()
            refresh_x = widget.mapFromGlobal(widget.refresh_inbox_button.mapToGlobal(QPoint(0, 0))).x()
            assert refresh_x + widget.refresh_inbox_button.width() <= width
