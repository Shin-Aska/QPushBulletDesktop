"""Native first-run prompts and SQLite preference acknowledgments; fake workers."""
from copy import deepcopy
import pytest
from PyQt6.QtCore import Qt
from pbdesk.desktop.app import MainWindow
from pbdesk.desktop.preferences import read_preferences, bind_preferences, receive_preferences, update_preferences
from pbdesk.desktop.theme import current_scheme
from test_desktop import FakeWorker, isolated_desktop_preferences


def configuration(required=True, revision=0, preferences=None):
    return {'storage': 'sqlite', 'setup': {'required': required, 'completed': not required,
        'enabled': True, 'source': None, 'revision': revision}, 'preferences': preferences or {}}


def general(config):
    return {'configuration': config, 'capabilities': {'account_id': None,
        'features': {'configuration': True}, 'provider_state': 'unconfigured'},
        'devices': {'account_id': None, 'devices': []}, 'items': [], 'actions': []}


def test_first_run_opens_account_once_and_cancel_keeps_it_accessible(qtbot):
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    window.show()
    assert not window.settings_dialog.isVisible()
    worker.snapshot.emit(general(configuration()))
    assert window.settings_dialog.isVisible()
    assert window.settings_tabs.currentWidget() is window.account_setup
    window.settings_dialog.reject()
    worker.snapshot.emit(general(configuration()))
    assert not window.settings_dialog.isVisible()
    window.show_settings('Account')
    assert window.settings_dialog.isVisible()
    assert not worker.requests  # Merely opening first-run setup never opens a wallet.
    window.exit_desktop()


def test_established_locked_install_restores_sql_preferences_without_setup(qtbot, qapp):
    worker = FakeWorker()
    window = MainWindow(worker)
    qtbot.addWidget(window)
    window.show()
    data = general(configuration(False, preferences={'main_page': 'sms', 'color_scheme': 'dark'}))
    data['capabilities']['provider_state'] = 'locked'
    worker.snapshot.emit(data)
    assert not window.settings_dialog.isVisible()
    assert window.tabs.currentIndex() == 1 and current_scheme(qapp) == 'dark'
    window.navigate(2)
    command, value = worker.requests[-1]
    assert command == 'preferences_update' and value['changes'] == {'main_page': 'summary'}
    worker.result.emit({'command': command, 'request_id': value['request_id'],
        'data': {'preferences': {'main_page': 'summary', 'color_scheme': 'dark'}}})
    assert read_preferences()['main_page'] == 'summary'
    window.exit_desktop()


def test_preferences_wait_for_core_and_old_ack_does_not_erase_new_choice():
    worker = FakeWorker()
    bind_preferences(worker)
    update_preferences({'main_page': 'sms'})
    assert not worker.requests
    receive_preferences({'main_page': 'notifications', 'color_scheme': 'light'})
    first = deepcopy(worker.requests[-1][1])
    assert read_preferences()['main_page'] == 'sms'
    update_preferences({'main_page': 'summary'})
    second = deepcopy(worker.requests[-1][1])
    worker.result.emit({'command': 'preferences_update', 'request_id': first['request_id'],
        'data': {'preferences': {'main_page': 'sms', 'color_scheme': 'light'}}})
    assert read_preferences()['main_page'] == 'summary'
    worker.result.emit({'command': 'preferences_update', 'request_id': second['request_id'],
        'data': {'preferences': {'main_page': 'summary', 'color_scheme': 'light'}}})
    assert read_preferences()['main_page'] == 'summary'


def test_setup_sends_token_only_on_explicit_click_and_clears_field(qtbot):
    from pbdesk.desktop.setup import AccountSetup
    worker = FakeWorker()
    widget = AccountSetup(worker)
    qtbot.addWidget(widget)
    widget.show()
    widget.apply_configuration(configuration())
    widget.mode.setCurrentIndex(widget.mode.findData('new'))
    widget.token.setText('fixture-secret')
    assert not worker.requests
    qtbot.mouseClick(widget.connect_button, Qt.MouseButton.LeftButton)
    command, request = worker.requests[-1]
    assert command == 'configure_provider' and request['token'] == 'fixture-secret'
    assert widget.token.text() == '' and not widget.connect_button.isEnabled()
    worker.result.emit({'command': command, 'request_id': request['request_id'], 'error': 'Unlock the selected wallet.'})
    assert widget.connect_button.isEnabled() and widget.feedback.text() == 'Unlock the selected wallet.'


def test_setup_keeps_expected_revision_when_another_client_changes_saved_source(qtbot):
    from pbdesk.desktop.setup import AccountSetup
    worker = FakeWorker()
    widget = AccountSetup(worker)
    qtbot.addWidget(widget)
    widget.apply_configuration(configuration(False, revision=1))
    qtbot.keyClicks(widget.entry, '-custom')
    widget.apply_configuration(configuration(False, revision=2))
    widget.submit()
    assert worker.requests[-1][1]['expected_revision'] == 1
