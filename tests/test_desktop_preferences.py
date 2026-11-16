"""Fictional shell preferences and reusable Settings; no real core or KWallet."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

from pbdesk.desktop.app import MainWindow
from pbdesk.desktop.devices import AddDeviceDialog, EncryptionDialog, RemoveDeviceDialog
from pbdesk.desktop.preferences import read_preferences, update_preferences
from pbdesk.desktop.theme import apply_theme, current_scheme, set_color_scheme, theme_signals
from test_desktop import FakeWorker


@pytest.fixture
def isolated(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv('PBDESK_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('PBDESK_APPLICATION_DIR', str(tmp_path / 'app'))
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    manager = theme_signals(qapp)
    old = (QPalette(qapp.palette()), qapp.styleSheet(), manager.scheme, manager._last_colors)
    apply_theme(qapp, 'system')
    yield tmp_path / 'state' / 'ui-preferences.json'
    manager._updating = True
    try:
        qapp.setStyleSheet(old[1])
        qapp.setPalette(old[0])
        qapp.processEvents()
        manager.scheme, manager._last_colors = old[2:]
        manager._scheduled = False
    finally:
        manager._updating = False


def make_window(qtbot):
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    return window, worker


def test_atomic_preference_merge_unknown_fields_and_parallel_writers(isolated, qapp):
    update_preferences({'future': {'enabled': True}, 'main_page': 'sms'})
    set_color_scheme(qapp, 'dark')
    assert read_preferences() == {'future': {'enabled': True}, 'main_page': 'sms', 'color_scheme': 'dark'}
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda index: update_preferences({f'writer_{index}': index}), range(18)))
    preferences = read_preferences()
    assert all(preferences[f'writer_{index}'] == index for index in range(18))
    assert preferences['color_scheme'] == 'dark' and preferences['main_page'] == 'sms'
    assert not isolated.exists()  # The desktop session mirror never writes a second authority.


@pytest.mark.parametrize('value', [None, 'broken json', '[]', '{"main_page":"devices"}',
                                   '{"main_page":"settings"}', '{"main_page":[]}'])
def test_startup_defaults_for_absent_malformed_or_non_main_page(isolated, qtbot, value):
    if value is not None:
        isolated.parent.mkdir(parents=True)
        isolated.write_text(value)
    window, worker = make_window(qtbot)
    assert window.tabs.currentIndex() == 0
    assert read_preferences().get('main_page', 'notifications') == 'notifications'
    window.exit_desktop()


def test_last_sms_page_survives_settings_and_color_scheme(isolated, qtbot, qapp):
    update_preferences({'color_scheme': 'light', 'main_page': 'sms', 'future': 42})
    window, worker = make_window(qtbot)
    window.show()
    assert window.tabs.currentIndex() == 1 and window.windowTitle() == 'pbdesk — SMS'
    window.sms_page.composer.setPlainText('Fictional unsaved draft')
    window.show_settings('Devices')
    qtbot.waitUntil(lambda: window.settings_dialog.isActiveWindow())
    for scheme in ('dark', 'light', 'system'):
        window.appearance.setCurrentIndex(window.appearance.findData(scheme))
        assert current_scheme(qapp) == scheme
        assert window.sms_page.composer.toPlainText() == 'Fictional unsaved draft'
        assert read_preferences()['main_page'] == 'sms'
    window.settings_dialog.reject()
    assert not worker.requests
    window.exit_desktop()
    restored, _ = make_window(qtbot)
    assert restored.tabs.currentIndex() == 1
    assert read_preferences()['future'] == 42
    restored.exit_desktop()


def test_settings_reentry_device_actions_shortcuts_account_switch_and_geometry(isolated, qtbot, monkeypatch):
    window, worker = make_window(qtbot)
    window.show()
    device = {'id': 'fictional-device', 'nickname': 'Example device', 'active': True,
              'can_push': True, 'can_sms': True}
    worker.snapshot.emit({'capabilities': {'account_id': 'first'},
                          'devices': {'account_id': 'first', 'devices': [device]}})
    window.navigate(1)
    window.show_settings('Devices')
    dialog, page, model = window.settings_dialog, window.devices_page, window.device_model
    qtbot.waitUntil(lambda: dialog.isActiveWindow())
    dialog.resize(850, 540)
    qtbot.wait(20)
    assert dialog.width() == 850 and dialog.height() == 540
    assert window.add_device_button.isVisible()
    assert window.rename_button.isEnabled() and window.remove_button.isEnabled()
    assert 'Example device' in window.device_detail.toPlainText()
    for button, kind in ((window.add_device_button, AddDeviceDialog),
                         (window.encryption_button, EncryptionDialog),
                         (window.remove_button, RemoveDeviceDialog)):
        button.click()
        assert isinstance(window.dialogs[-1], kind)
        window.dialogs[-1].reject()
    dialog.activateWindow()
    qtbot.waitUntil(lambda: dialog.isActiveWindow())
    monkeypatch.setattr('pbdesk.desktop.app.QInputDialog.getText', lambda *a, **k: ('Renamed example', True))
    window.rename_button.click()
    assert worker.requests[-1][0] == 'rename_device'
    qtbot.keyClick(dialog, Qt.Key.Key_F5)
    assert worker.requests[-1][0] == 'refresh_devices'
    qtbot.keyClick(dialog, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.focusWidget() is window.device_filter
    before = list(worker.requests)
    qtbot.keyClick(dialog, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert worker.requests == before and window.dialogs == [dialog]
    dialog.reject()
    for _ in range(3):
        window.show_settings()
        assert window.settings_dialog is dialog and window.devices_page is page and window.device_model is model
        assert window.settings_tabs.currentWidget() is page
        dialog.reject()
    assert read_preferences()['main_page'] == 'sms'
    window.show_settings('Local Web')
    before_web = len(worker.requests)
    window.web_enabled.click()
    assert worker.requests[before_web:] == [('web', True)]
    worker.snapshot.emit({'web': {'enabled': False, 'host': '127.0.0.1', 'port': 9999, 'error': 'Fictional unavailable'}})
    assert not window.web_enabled.isChecked() and '9999' in window.web_settings_state.text()
    assert not window.web_open_button.isEnabled()
    worker.snapshot.emit({'capabilities': {'account_id': 'second'},
                          'devices': {'account_id': 'second', 'devices': []}})
    assert not dialog.isVisible()
    assert window.device_model.rowCount() == 0
    assert not window.rename_button.isEnabled() and not window.remove_button.isEnabled()
    assert 'Example device' not in window.device_detail.toPlainText()
    assert read_preferences()['main_page'] == 'sms'
    window.exit_desktop()


def test_notification_media_selection_is_account_scoped_and_explicit(isolated, qtbot):
    window, worker = make_window(qtbot)
    window.show()
    image = {'id': 21, 'title': 'Fictional attachment', 'body': '', 'type': 'file',
             'file_type': 'image/png', 'file_url': 'https://example.invalid/fictional.png'}
    worker.snapshot.emit({'capabilities': {'account_id': 'first'}, 'items': [image]})
    from pbdesk.desktop.media import attachment_revision
    assert window.item_image.reference == {'account_id': 'first', 'kind': 'item', 'item_id': 21, 'index': 0,
                                           'revision': attachment_revision(image['file_url'])}
    assert window.item_image.isVisible() and window.notification_media_valid()
    assert not worker.requests
    window.item_image.load_button.click()
    assert worker.requests[-1][0] == 'media_image'
    assert 'file_url' not in worker.requests[-1][1]
    set_color_scheme(QApplication.instance(), 'dark')
    assert window.item_image.request_id == worker.requests[-1][1]['request_id']
    window.search.setText('nothing matches')
    qtbot.waitUntil(lambda: not window.item_image.isVisible())
    assert not window.item_image.isVisible() and not window.notification_media_valid()
    worker.snapshot.emit({'capabilities': {'account_id': 'second'}, 'items': []})
    assert not window.item_image.isVisible()
    assert window.item_image.reference == {}
    window.exit_desktop()


def test_summary_restoration_context_and_settings_use_real_injected_widgets(isolated, qtbot):
    from PyQt6.QtCore import QObject, pyqtSignal
    from pbdesk.desktop.ai import AISettings, SummaryPage, SmsAiAssistant

    class FakeAi(QObject):
        result = pyqtSignal(dict)

        def __init__(self):
            super().__init__()
            self.requests = []
            self.stopped = False
            self.waited = False

        def request(self, *args):
            self.requests.append(args)

        def stop(self):
            self.stopped = True

        def wait(self, timeout):
            self.waited = timeout
            return True

    update_preferences({'main_page': 'summary', 'color_scheme': 'system'})
    ai, worker = FakeAi(), FakeWorker()
    window = MainWindow(worker, ai)
    qtbot.addWidget(window)
    window.show()
    assert window.tabs.currentWidget() is window.summary_page
    assert isinstance(window.summary_page, SummaryPage) and isinstance(window.sms_ai, SmsAiAssistant)
    assert window.windowTitle() == 'pbdesk — Summary'
    assert window.compose_button.isHidden() and window.new_sms_button.isHidden()
    assert not ai.requests
    items = [{'id': 1, 'title': 'Selected notification', 'body': 'Fictional summary source'}]
    worker.snapshot.emit({'capabilities': {'account_id': 'fictional', 'features': {'ai': True}}, 'items': items})
    assert window.summary_page.account_id == 'fictional'
    assert ai.requests[-1][0] == 'ai_settings'

    def reply(command, data):
        payload = next(payload for name, payload in reversed(ai.requests) if name == command)
        ai.result.emit({'command': command, 'request_id': payload['request_id'], 'ok': True, 'data': data})

    # Saved configuration starts the account digest without preview/generate clicks.
    reply('ai_settings', {'configured': True, 'model': 'gpt-6-luna'})
    assert ai.requests[-1][0] == 'ai_digest'
    assert ai.requests[-1][1]['account_id'] == 'fictional'
    assert ai.requests[-1][1]['scope'] == 'recent'
    assert not hasattr(window.summary_page, 'prepare_button')
    reply('ai_digest', {'account_id': 'fictional', 'status': 'ready', 'last_good': {
        'text': 'Needs attention\nFictional digest\n\nConversation highlights\nNo recent SMS.\n\nNotifications\nSelected notification.',
        'generated_at': 1780000000, 'window_start': 1779990000, 'window_end': 1780000000,
        'counts': {'notifications': 1, 'sms': 0, 'total': 1}, 'model': 'gpt-6-luna', 'fingerprint': 'fictional'}})
    captured = window.summary_page.output.toPlainText()
    assert 'Fictional digest' in captured and '1 notifications' in window.summary_page.scope.text()
    requests = len(ai.requests)
    worker.snapshot.emit({'sms': {'device_id': 'fictional-phone'}})
    assert window.summary_page.output.toPlainText() == captured
    assert len(ai.requests) == requests
    window.show_settings('AI')
    assert isinstance(window.settings_tabs.currentWidget(), AISettings)
    assert ai.requests[-1][0] == 'ai_settings'
    reply('ai_settings', {'configured': True, 'model': 'gpt-6-luna'})
    assert ai.requests[-1][0] == 'ai_models'
    assert read_preferences()['main_page'] == 'summary'
    window.settings_dialog.reject()
    window.compose()
    assert not window.dialogs
    window.activateWindow()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    window.focus_search()
    assert QApplication.focusWidget() is window.summary_page.output
    worker.snapshot.emit({'capabilities': {'account_id': 'other'}, 'items': []})
    assert window.summary_page.output.toPlainText() == ''
    assert window.summary_page.last_good is None
    assert read_preferences()['main_page'] == 'summary'
    window.exit_desktop()
    assert ai.stopped and ai.waited == 3000
    restored, _ = make_window(qtbot)
    assert restored.tabs.currentWidget() is restored.summary_page
    restored.exit_desktop()


def test_draft_reply_controls_do_not_clip_narrow_sms_composer(isolated, qtbot):
    window, worker = make_window(qtbot)
    window.navigate(1)
    window.resize(850, 540)
    window.show()
    qtbot.wait(20)
    composer_rect = window.sms_page.composer.geometry()
    before_hint = window.minimumSizeHint()
    window.sms_ai_button.click()
    qtbot.wait(20)
    assert window.sms_ai_dialog.isVisible() and window.sms_ai.isVisible()
    assert (window.width(), window.height()) == (850, 540)
    assert window.minimumSizeHint() == before_hint
    assert window.sms_page.composer.geometry() == composer_rect
    assert not window.sms_ai.preview.isVisible() and window.sms_ai.output.isVisible()
    window.sms_ai.context_toggle.click()
    assert window.sms_ai.preview.isVisible()
    dialog = window.sms_ai_dialog
    dialog.reject()
    window.sms_ai_button.click()
    assert window.sms_ai_dialog is dialog
    assert not worker.requests
    window.exit_desktop()


def test_media_result_feedback_never_changes_global_command_errors(isolated, qtbot):
    window, worker = make_window(qtbot)
    window.show()
    image = {'id': 71, 'title': 'Fictional image', 'body': '', 'type': 'file',
             'file_type': 'image/png', 'file_url': 'https://example.invalid/fictional.png'}
    worker.snapshot.emit({'capabilities': {'account_id': 'fictional'}, 'items': [image]})
    window.item_image.load_button.click()
    request_id = window.item_image.request_id
    worker.result.emit({'command': 'media_image', 'request_id': request_id,
                        'account_id': 'fictional', 'error': 'Fictional attachment expired'})
    assert 'could not be loaded' in window.item_image.status.text()
    assert window.item_image.load_button.isEnabled()
    assert not window.command_error and not window.error.isVisible()
    worker.result.emit({'command': 'refresh_devices', 'error': 'Device refresh failed. Try again.'})
    assert window.command_error and window.error.isVisible()
    for result in ({'command': 'media_image', 'error': 'Another attachment expired'},
                   {'command': 'media_image', 'data': {'account_id': 'fictional'}}):
        worker.result.emit(result)
        assert window.command_error
        assert window.error.isVisible() and window.error.text() == 'Device refresh failed. Try again.'
    window.exit_desktop()


@pytest.mark.parametrize('scheme', ['system', 'dark'])
def test_narrow_sms_real_error_keeps_native_controls_and_composer(isolated, qtbot, qapp, scheme):
    from PyQt6.QtCore import QRect
    from test_desktop_chat import message, snapshot

    apply_theme(qapp, scheme)
    window, worker = make_window(qtbot)
    worker.snapshot.emit(snapshot([message()]))
    window.navigate(1)
    window.resize(850, 540)
    window.show()
    window.sms_page.composer.setPlainText('Fictional draft remains editable')
    worker.result.emit({'command': 'refresh_devices', 'error': 'Device refresh failed. Check the provider connection and retry.'})
    qtbot.wait(30)
    assert window.error.isVisible() and window.command_error
    assert (window.width(), window.height()) == (850, 540)
    assert window.sms_page.composer.height() == 86
    assert window.sms_page.composer.toPlainText() == 'Fictional draft remains editable'
    for button in (window.sms_ai_button, window.sms_page.send_button):
        assert button.height() >= button.sizeHint().height()
        window.sms_scroll.ensureWidgetVisible(button, 0, 0)
        qtbot.wait(10)
        top_left = button.mapTo(window.sms_scroll.viewport(), button.rect().topLeft())
        assert window.sms_scroll.viewport().rect().contains(QRect(top_left, button.size()))
    window.sms_scroll.ensureWidgetVisible(window.sms_page.composer, 0, 0)
    qtbot.wait(10)
    top_left = window.sms_page.composer.mapTo(window.sms_scroll.viewport(), window.sms_page.composer.rect().topLeft())
    assert window.sms_scroll.viewport().rect().contains(QRect(top_left, window.sms_page.composer.size()))
    window.exit_desktop()


def test_settings_devices_refresh_metadata_stays_one_line(isolated, qtbot):
    window, worker = make_window(qtbot)
    worker.snapshot.emit({'devices': {'account_id': 'fictional', 'devices': [],
                                     'synced_at': '2026-11-14T08:00:00Z'}})
    window.show_settings('Devices')
    for width in (1000, 850):
        window.settings_dialog.resize(width, 700)
        qtbot.wait(20)
        label = window.devices_status
        assert not label.wordWrap() and '\n' not in label.text()
        assert label.contentsRect().width() >= label.fontMetrics().horizontalAdvance(label.text())
        assert window.refresh_devices_button.isVisible() and window.add_device_button.isVisible()
        assert label.geometry().right() < window.refresh_devices_button.geometry().left()
    window.exit_desktop()


@pytest.mark.parametrize('scheme', ['system', 'dark'])
def test_narrow_partial_mms_wrapped_contact_keeps_native_actions(isolated, qtbot, qapp, scheme):
    from PyQt6.QtGui import QFont
    from test_desktop_chat import message, snapshot

    apply_theme(qapp, scheme)
    window, worker = make_window(qtbot)
    messages = [
        message(1, name='Alex · Fictional workshop planning team', body='Fictional retained conversation.'),
        message(2, name='Alex · Fictional workshop planning team',
                body='Fictional room layout.', image_urls=['https://example.invalid/fictional-mms.png']),
        message(3, conversation='two', name='Sam'),
        message(4, conversation='three', name='Fictional parcel desk'),
    ]
    data = snapshot(messages)
    data['sms']['sync'] = {'state': 'partial', 'last_success_at': 1794643200,
                           'error': 'Fictional history unavailable', 'unavailable_count': 3}
    worker.snapshot.emit(data)
    window.navigate(1)
    window.sms_page.phone.setCurrentIndex(window.sms_page.phone.findData('a'))
    window.sms_page._activate_thread(('history', 'one', 'a'), True)
    window.resize(850, 540)
    window.show()
    worker.result.emit({'command': 'refresh_sms',
                        'error': 'Fictional history refresh failed. Retained messages remain available.'})
    qtbot.wait(30)
    assert window.sms_page.threads.count() == 3
    assert len(window.sms_page.heading.text()) > 30
    assert 'Partial' in window.sms_page.sync_status.text()
    assert window.error.isVisible()
    for enlarged in (False, True):
        if enlarged:
            # Native font changes after the page is open must update action targets.
            for button in (window.sms_ai_button, window.sms_page.send_button):
                font = QFont(button.font())
                font.setPointSize(font.pointSize() + 2)
                button.setFont(font)
            qtbot.wait(20)
        assert (window.width(), window.height()) == (850, 540)
        assert window.sms_scroll.horizontalScrollBar().maximum() == 0
        assert window.sms_page.composer.height() == 86
        for button in (window.sms_ai_button, window.sms_page.send_button):
            assert button.height() >= button.sizeHint().height()
            window.sms_scroll.ensureWidgetVisible(button, 0, 0)
    window.exit_desktop()
