"""Synthetic native core controls; never start services, touch wallets or providers."""
from copy import deepcopy
import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication
from pbdesk.desktop.core_settings import CoreSettings
from pbdesk.desktop.preferences import read_preferences, update_preferences
from test_desktop import isolated_desktop_preferences


FIRST = '11111111-1111-4111-8111-111111111111'
SECOND = '22222222-2222-4222-8222-222222222222'


def settings(instance=FIRST, enabled=True, available=True, requested=None, **changes):
    return dict(instance_id=instance, uptime_seconds=3724,
                lifecycle=dict(mode='coupled', desktop_clients=2, web_clients=1,
                               renew_seconds=2, expiry_seconds=8, release_grace_seconds=3),
                addresses=dict(core='http://127.0.0.1:43123', web='http://127.0.0.1:43124'),
                paths=dict(application='/tmp/fictional-pbdesk/application',
                           state='/tmp/fictional-pbdesk/state',
                           database='/tmp/fictional-pbdesk/state/pbdesk.sqlite3'),
                notifications=dict(enabled=enabled, available=available, requested=enabled if requested is None else requested,
                                   outcome='Ready for delivery' if available else 'Notification service unavailable'),
                restarting=False, **changes)


def snapshot(instance=FIRST):
    return {'instance_id': instance, 'capabilities': {'features': {'core_settings': True, 'core_restart': True}}}


class Worker(QObject):
    result = pyqtSignal(dict)
    def __init__(self):
        super().__init__()
        self.requests = []
    def request(self, command, value=None):
        self.requests.append((command, deepcopy(value)))
    def reply(self, data=None, command=None, **fields):
        name, value = next((name, value) for name, value in reversed(self.requests) if command is None or command == name)
        self.result.emit(dict(command=name, request_id=value['request_id'], data=data or {}, **fields))


def ready(qtbot):
    worker = Worker()
    page = CoreSettings(worker)
    qtbot.addWidget(page)
    assert not worker.requests and not page.restart_button.isEnabled()
    page.apply_snapshot(snapshot())
    page.show()
    assert worker.requests[-1][0] == 'core_settings'
    worker.reply(settings())
    assert page.restart_button.isEnabled()
    return page, worker


def start_restart(page, worker, monkeypatch):
    monkeypatch.setattr(page, 'confirm_restart', lambda: True)
    page.restart_button.click()
    command, payload = worker.requests[-1]
    assert command == 'core_restart' and payload['expected_instance_id'] == FIRST
    return payload['request_id']


def test_core_settings_status_paths_lifetime_and_truthful_delivery(qtbot):
    page, worker = ready(qtbot)
    assert page.values['instance'].text() == FIRST
    assert page.values['uptime'].text() == '1h 2m'
    assert page.values['clients'].text() == '2 desktop · 1 web server'
    assert page.values['renew'].text() == '2s' and page.values['expiry'].text() == '8s'
    assert page.values['database'].text().endswith('pbdesk.sqlite3')
    assert 'Delivery: On' in page.notification_outcome.text()
    assert 'Service available' in page.notification_outcome.text()
    assert page.notifications_enabled.isChecked() and not page.apply_button.isEnabled()
    page.notifications_enabled.setChecked(False)
    page.apply_button.click()
    assert worker.requests[-1][0] == 'core_settings_update'
    assert worker.requests[-1][1]['notifications_enabled'] is False
    assert worker.requests[-1][1]['expected_instance_id'] == FIRST
    assert not page.restart_button.isEnabled() and not page.apply_button.isEnabled()
    worker.reply(settings(enabled=False, available=False))
    assert worker.requests[-1][0] == 'core_settings'
    worker.reply(settings(enabled=False, available=False))
    assert not page.notifications_enabled.isChecked()
    assert 'Delivery: Off' in page.notification_outcome.text() and 'unavailable' in page.notification_outcome.text()
    assert page.restart_button.isEnabled()


def test_late_get_keeps_notification_edit_and_wrong_request_does_not_unlock(qtbot):
    page, worker = ready(qtbot)
    page.refresh()
    pending_id = worker.requests[-1][1]['request_id']
    page.notifications_enabled.setChecked(False)
    worker.result.emit({'command': 'core_settings_update', 'request_id': pending_id, 'data': settings()})
    assert not page.restart_button.isEnabled()
    worker.reply(settings())
    assert not page.notifications_enabled.isChecked() and page.apply_button.isEnabled()
    worker.result.emit({'command': 'core_settings', 'request_id': 'unrelated', 'data': settings(enabled=True)})
    assert not page.notifications_enabled.isChecked()


def test_restart_cancel_and_confirmation_are_explicit(qtbot, monkeypatch):
    page, worker = ready(qtbot)
    box, restart = page.restart_confirmation()
    qtbot.addWidget(box)
    assert 'shared core' in box.text() and 'Other desktop clients' in box.informativeText()
    assert 'briefly reconnect' in box.informativeText()
    assert box.defaultButton() is not restart
    monkeypatch.setattr(page, 'confirm_restart', lambda: False)
    page.restart_button.click()
    assert not any(command == 'core_restart' for command, _ in worker.requests)


def test_restart_202_is_reconnecting_only_new_verified_instance_completes(qtbot, monkeypatch):
    page, worker = ready(qtbot)
    request_id = start_restart(page, worker, monkeypatch)
    count = len(worker.requests)
    page.restart()
    assert len(worker.requests) == count
    worker.reply({'accepted': True, 'instance_id': FIRST, 'request_id': request_id})
    assert 'Reconnecting' in page.status.text() and 'restarted' not in page.status.text()
    assert not page.restart_button.isEnabled()
    page.apply_snapshot(snapshot())
    assert not page.restart_button.isEnabled()
    page.apply_snapshot(snapshot(SECOND))
    assert worker.requests[-1][0] == 'core_settings'
    worker.reply(settings(SECOND))
    assert 'restarted' in page.status.text() and page.values['instance'].text() == SECOND
    assert page.restart_button.isEnabled() and not page.restart_timer.isActive()
    assert sum(command == 'core_restart' for command, _ in worker.requests) == 1


def test_restart_context_change_during_confirmation_prevents_post(qtbot, monkeypatch):
    page, worker = ready(qtbot)
    def confirm():
        page.apply_snapshot(snapshot(SECOND))
        return True
    monkeypatch.setattr(page, 'confirm_restart', confirm)
    page.restart_button.click()
    assert not any(command == 'core_restart' for command, _ in worker.requests)
    assert 'changed while confirmation' in page.feedback.text()


def test_restart_timeout_ambiguous_and_manual_refresh_never_repeat_post(qtbot, monkeypatch):
    page, worker = ready(qtbot)
    start_restart(page, worker, monkeypatch)
    worker.reply(error='Connection interrupted', ambiguous=True)
    assert 'Reconnecting' in page.status.text()
    assert not page.restart_button.isEnabled()
    page.restart_timeout()
    assert 'not been confirmed' in page.status.text()
    assert page.refresh_button.isEnabled()
    page.refresh()
    worker.reply(settings())
    assert page.restart_button.isEnabled() and 'not confirmed' in page.feedback.text()
    assert sum(command == 'core_restart' for command, _ in worker.requests) == 1


def test_late_restart_ack_cannot_replace_new_verified_success(qtbot, monkeypatch):
    page, worker = ready(qtbot)
    request_id = start_restart(page, worker, monkeypatch)
    page.apply_snapshot(snapshot(SECOND))
    worker.reply(settings(SECOND), command='core_settings')
    assert 'restarted' in page.status.text()
    worker.result.emit({'command': 'core_restart', 'request_id': request_id,
                        'data': {'accepted': True, 'instance_id': FIRST, 'request_id': request_id}})
    assert 'restarted' in page.status.text()


def test_old_get_and_stale_instance_cannot_enable_controls(qtbot):
    page, worker = ready(qtbot)
    page.refresh()
    old_id = worker.requests[-1][1]['request_id']
    page.apply_snapshot(snapshot(SECOND))
    worker.result.emit({'command': 'core_settings', 'request_id': old_id, 'data': settings()})
    assert not page.restart_button.isEnabled()
    worker.reply(settings())  # Matching request, wrong verified instance.
    assert not page.restart_button.isEnabled()
    assert 'changed while settings' in page.feedback.text()
    page.refresh()
    worker.reply(settings(SECOND))
    assert page.restart_button.isEnabled()


@pytest.mark.parametrize('method', ['feature', '404'])
def test_unsupported_backend_is_actionable_and_never_fakes_controls(qtbot, method):
    worker = Worker()
    page = CoreSettings(worker)
    qtbot.addWidget(page)
    if method == 'feature':
        page.apply_snapshot({'capabilities': {'features': {'core_settings': False, 'core_restart': False}}})
    else:
        page.refresh()
        worker.reply(error='Not found', status=404)
    assert 'updated backend externally' in page.feedback.text()
    assert not page.restart_button.isEnabled() and not page.notifications_enabled.isEnabled()
    count = len(worker.requests)
    page.refresh()
    assert len(worker.requests) == count


def test_api_failure_is_actionable_and_window_stays_usable(qtbot):
    page, worker = ready(qtbot)
    page.notifications_enabled.setChecked(False)
    page.apply_button.click()
    worker.reply(error='Settings could not be saved. Refresh status to try again.', ambiguous=False, status=409)
    assert 'could not be saved' in page.feedback.text()
    assert worker.requests[-1][0] == 'core_settings'
    worker.reply(settings())
    assert page.apply_button.isEnabled()


def test_main_window_core_restart_preserves_draft_navigation_and_dialog(qtbot, monkeypatch):
    from pbdesk.desktop.app import MainWindow
    from test_desktop import FakeWorker
    from test_desktop_chat import snapshot as sms_snapshot, message
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    window.show()
    data = sms_snapshot([message()])
    data.update(snapshot())
    data['capabilities'].update(account_id='acct')
    worker.snapshot.emit(data)
    window.navigate(1)
    window.sms_page.composer.setPlainText('An unsent draft survives backend restart')
    window.show_settings('Core')
    page = window.core_settings
    def reply(command, data):
        payload = next(value for name, value in reversed(worker.requests) if name == command)
        worker.result.emit({'command': command, 'request_id': payload['request_id'], 'data': data})
    reply('core_settings', settings())
    request_id = start_restart(page, worker, monkeypatch)
    reply('core_restart', {'accepted': True, 'instance_id': FIRST, 'request_id': request_id})
    worker.connection.emit('Reconnecting to private core…')
    worker.snapshot.emit(snapshot(SECOND))
    reply('core_settings', settings(SECOND))
    assert window.settings_dialog.isVisible()
    assert window.settings_tabs.currentWidget() is page
    assert window.tabs.currentIndex() == 1
    assert window.sms_page.composer.toPlainText() == 'An unsent draft survives backend restart'
    assert read_preferences()['main_page'] == 'sms'
    assert 'restarted' in page.status.text()
    window.settings_dialog.reject()
    window.exit_desktop()


@pytest.mark.parametrize('page_name,index,title', [('summary', 2, 'Summary'), ('notifications', 0, 'Notifications'), ('sms', 1, 'SMS')])
def test_visual_sidebar_order_preserves_all_saved_pages_and_logical_mapping(qtbot, monkeypatch, page_name, index, title):
    from pbdesk.desktop.app import MainWindow
    from test_desktop import FakeWorker
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    update_preferences({'main_page': page_name})
    window = MainWindow(FakeWorker())
    qtbot.addWidget(window)
    window.show()
    visual = sorted(window.navigation, key=lambda button: button.y())
    assert [button.text() for button in visual] == ['Summary', 'Notifications', 'SMS']
    assert window.tabs.currentIndex() == index and window.heading.text() == title
    assert window.navigation[index].isChecked()
    assert read_preferences()['main_page'] == page_name
    for logical, expected in ((2, 'Summary'), (0, 'Notifications'), (1, 'SMS')):
        window.navigation[logical].click()
        assert window.tabs.currentIndex() == logical and window.heading.text() == expected
        assert window.navigation[logical].isChecked()
        assert sum(button.isChecked() for button in window.navigation) == 1
    window.exit_desktop()


def test_requested_notification_preference_survives_unavailable_delivery(qtbot):
    page, worker = ready(qtbot)
    page.refresh()
    worker.reply(settings(enabled=False, available=False, requested=True))
    assert page.notifications_enabled.isChecked()
    assert not page.apply_button.isEnabled()
    assert 'Saved preference: On' in page.notification_outcome.text()
    assert 'Delivery: Off' in page.notification_outcome.text()
    assert 'unavailable' in page.notification_outcome.text()


def test_reordered_navigation_shortcuts_dispatch_by_page_identity(qtbot, monkeypatch):
    from pbdesk.desktop.app import MainWindow
    from test_desktop import FakeWorker
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    window = MainWindow(FakeWorker())
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    refreshed = []
    monkeypatch.setattr(window.summary_page, 'refresh', lambda *args: refreshed.append('summary'))
    monkeypatch.setattr(window.sms_page, 'refresh', lambda *args: refreshed.append('sms'))
    for index, expected, focus in ((2, 'summary', window.summary_page.output),
                                    (0, 'notifications', window.search),
                                    (1, 'sms', window.sms_page.search)):
        window.navigation[index].click()
        before = len(window.worker.requests)
        qtbot.keyClick(window, Qt.Key.Key_F5)
        if expected == 'notifications':
            assert window.worker.requests[before:][-1][0] == 'search'
        else:
            assert refreshed[-1] == expected
        qtbot.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        assert QApplication.focusWidget() is focus
    window.navigation[2].click()
    before = len(window.dialogs)
    qtbot.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert len(window.dialogs) == before
    window.navigation[1].click()
    calls = []
    monkeypatch.setattr(window.sms_page, 'start_new', lambda: calls.append('sms'))
    qtbot.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert calls == ['sms']
    window.exit_desktop()


def test_core_tab_shortcuts_reuse_and_inline_errors_preserve_global_state(qtbot, monkeypatch):
    from pbdesk.desktop.app import MainWindow
    from test_desktop import FakeWorker
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    window = MainWindow(FakeWorker())
    qtbot.addWidget(window)
    window.show()
    window.show_settings('Core')
    dialog, page = window.settings_dialog, window.core_settings
    dialog.activateWindow()
    qtbot.waitUntil(dialog.isActiveWindow)
    name, payload = window.worker.requests[-1]
    assert name == 'core_settings'
    window.worker.result.emit({'command': name, 'request_id': payload['request_id'], 'data': settings()})
    qtbot.keyClick(dialog, Qt.Key.Key_F5)
    assert window.worker.requests[-1][0] == 'core_settings'
    qtbot.keyClick(dialog, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.focusWidget() is page.values['application']
    count = len(window.worker.requests)
    qtbot.keyClick(dialog, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert len(window.worker.requests) == count
    window.command_error = True
    window.show_error('Unrelated provider error')
    name, payload = window.worker.requests[-1]
    window.worker.result.emit({'command': name, 'request_id': payload['request_id'], 'data': settings()})
    assert window.command_error is True and window.error.text() == 'Unrelated provider error'
    page.refresh()
    name, payload = window.worker.requests[-1]
    window.worker.result.emit({'command': name, 'request_id': payload['request_id'], 'error': 'Cannot load core settings'})
    assert window.command_error is True and window.error.text() == 'Unrelated provider error'
    for _ in range(2):
        dialog.reject()
        window.show_settings('Core')
        assert window.settings_dialog is dialog and window.core_settings is page
    dialog.reject()
    window.exit_desktop()


def test_failed_status_refresh_disables_restart_and_old_backend_clears_details(qtbot):
    page, worker = ready(qtbot)
    page.refresh()
    worker.reply(error='Status service unavailable. Refresh to check the backend.', status=503)
    assert not page.restart_button.isEnabled()
    assert page.refresh_button.isEnabled()
    assert 'refresh failed' in page.status.text()
    page.refresh()
    worker.reply(error='Not found', status=404)
    assert page.values['instance'].text() == 'Not loaded'
    assert 'not been checked' in page.notification_outcome.text()


def test_narrow_core_paths_scroll_without_horizontal_overflow(qtbot):
    page, worker = ready(qtbot)
    page.resize(800, 460)
    value = settings()
    value['paths']['application'] = '/tmp/' + 'long-application-directory-' * 12
    page.refresh()
    worker.reply(value)
    qtbot.wait(10)
    assert page.scroll.horizontalScrollBar().maximum() == 0
    page.scroll.ensureWidgetVisible(page.apply_button)
    assert page.apply_button.isVisible()


def test_restart_requires_explicit_supported_feature_and_valid_status(qtbot):
    worker = Worker()
    page = CoreSettings(worker)
    qtbot.addWidget(page)
    page.refresh()
    worker.reply(settings())
    assert not page.restart_button.isEnabled()
    page.apply_snapshot(snapshot())
    assert page.restart_button.isEnabled()
    page.refresh()
    worker.reply({'instance_id': ''})
    assert not page.restart_button.isEnabled()
    assert 'could not be verified' in page.status.text()
