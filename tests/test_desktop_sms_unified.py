"""Synthetic multi-phone SMS routing; no provider or persistent history access."""
from copy import deepcopy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from pbdesk.desktop.chat import SmsPage, MessageText
from test_desktop_chat import FakeWorker, message, snapshot, create_request, succeed


def unified(qtbot, duplicate_names=False):
    worker = FakeWorker()
    page = SmsPage(worker)
    qtbot.addWidget(page)
    page.resize(1100, 740)
    page.show()
    data = snapshot()
    if duplicate_names:
        data['devices']['devices'][0]['nickname'] = 'My phone'
        data['devices']['devices'][1]['nickname'] = 'My phone'
    data.pop('sms'); data.pop('sms_device_id')
    data['sms_by_device'] = {device: {'account_id': 'acct', 'messages': [
        message(number, device=device, conversation='same', name='Alex', body=f'{device} fictional history')],
        'sync': {'state': 'ready'}} for device, number in [('a', 1), ('b', 2)]}
    page.apply_snapshot(data)
    qtbot.wait(20)
    return page, worker


def test_default_combined_list_distinct_same_contact_device_labels_recency_search(qtbot):
    page, worker = unified(qtbot, duplicate_names=True)
    assert page.phone.currentText() == 'All devices' and page.filter_device_id is None
    assert worker.requests == [] and page.selection_request_id is None
    assert set(page._threads) == {('history', 'same', 'a'), ('history', 'same', 'b')}
    assert page.threads.item(0).data(Qt.ItemDataRole.UserRole) == ('history', 'same', 'b')
    assert [page.threads.item(n).data(Qt.ItemDataRole.UserRole + 1)['device'] for n in range(2)] == ['My phone · b', 'My phone · a']
    assert 'b' in page.context_label.text()
    assert any('My phone · b' in label.text() for label in page.timeline_content.findChildren(QLabel))
    page.search.setText('a fictional')
    assert page.threads.count() == 1
    assert page.threads.item(0).data(Qt.ItemDataRole.UserRole)[2] == 'a'
    assert not any(command in {'refresh_sms', 'create_action', 'approve_action', 'media_image'} for command, _ in worker.requests)


def test_unified_row_selection_preserves_filter_polling_and_routes_drafts(qtbot):
    page, worker = unified(qtbot)
    requests = deepcopy(worker.requests)
    for device in ('a', 'b'):
        page._activate_thread(('history', 'same', device))
        page.composer.setPlainText(device + ' reply draft')
    page._activate_thread(('history', 'same', 'a'))
    assert page.composer.toPlainText() == 'a reply draft'
    assert page.filter_device_id is None and worker.requests == requests
    request = create_request(page, worker)
    assert request['payload']['device_id'] == 'a'
    page._activate_thread(('history', 'same', 'b'))
    succeed(worker, request)
    assert page.composer.toPlainText() == 'b reply draft'
    assert not any(command in {'refresh_sms', 'approve_action'} for command, _ in worker.requests)


def test_filter_roundtrip_preserves_selected_conversation_and_draft(qtbot):
    page, worker = unified(qtbot)
    page._activate_thread(('history', 'same', 'b'))
    page.composer.setPlainText('B draft')
    page.phone.setCurrentIndex(page.phone.findData('a'))
    assert set(page._threads) == {('history', 'same', 'a')}
    assert page.selected_device_id == 'a'
    page.composer.setPlainText('A draft')
    page.phone.setCurrentIndex(0)
    assert page.current_thread == ('history', 'same', 'b')
    assert page.composer.toPlainText() == 'B draft'
    assert worker.requests[-1][0] == 'select_device'
    assert worker.requests[-1][1]['device_id'] is None
    assert worker.requests[-1][1]['account_id'] == 'acct'
    assert not any(command == 'refresh_sms' for command, _ in worker.requests)


def test_new_message_all_requires_explicit_sender_keeps_each_sender_draft(qtbot):
    page, worker = unified(qtbot)
    page.start_new()
    assert page.selected_device_id is None and page.send_from.currentData() is None
    page.recipient.setText('+12025550123')
    page.composer.setPlainText('Unassigned draft')
    count = len(worker.requests); page.submit()
    assert len(worker.requests) == count and not page.send_button.isEnabled()
    page.send_from.setCurrentIndex(page.send_from.findData('a'))
    assert page.composer.toPlainText() == 'Unassigned draft'
    page.composer.setPlainText('A new draft')
    page.send_from.setCurrentIndex(page.send_from.findData('b'))
    page.composer.setPlainText('B new draft')
    page.send_from.setCurrentIndex(page.send_from.findData('a'))
    assert page.composer.toPlainText() == 'A new draft'
    assert create_request(page, worker)['payload']['device_id'] == 'a'
    page.phone.setCurrentIndex(page.phone.findData('b')); page.start_new()
    assert page.selected_device_id == 'b' and page.send_from.currentData() == 'b'


def test_stale_generation_account_mismatch_and_partial_failures_keep_other_phone(qtbot):
    page, worker = unified(qtbot)
    current = page.selection_request_id
    for changes in ({'sms_request_id': 'stale'}, {'account_id': 'other'}):
        part = {'account_id': 'acct', 'messages': [message(90, device='a')]}
        data = {'sms_request_id': current, 'sms_by_device': {'a': part}}
        if 'account_id' in changes:
            part.update(changes)
        else:
            data.update(changes)
        page.apply_snapshot(data)
        assert page.sms_cache['a']['messages'][0]['id'] == 1
    page.apply_snapshot({'sms_request_id': current, 'sms_by_device': {
        'a': {'account_id': 'acct', 'messages': [], 'read_error': 'Read unavailable'},
        'b': {'account_id': 'acct', 'messages': [message(3, device='b', conversation='same')]}}})
    assert page.sms_cache['a']['messages'][0]['id'] == 1
    assert page.sms_cache['b']['messages'][0]['id'] == 3
    assert 'Read unavailable' in page.sync_status.accessibleDescription()
    assert page.filter_device_id is None


def test_refresh_all_explicit_only_active_sms_and_filtered_refresh(qtbot):
    page, worker = unified(qtbot)
    assert page.refresh_button.text() == 'Refresh all devices'
    page.refresh()
    calls = [payload for command, payload in worker.requests if command == 'refresh_sms']
    assert [call['device_id'] for call in calls] == ['a', 'b']
    assert all(call['account_id'] == 'acct' for call in calls)
    page.phone.setCurrentIndex(page.phone.findData('removed'))
    count = len(worker.requests); page.refresh()
    assert worker.requests[count:][0][1]['device_id'] == 'removed'


def test_history_only_unknown_phone_stays_readable_and_never_reroutes(qtbot):
    page, worker = unified(qtbot)
    page.apply_snapshot({'sms_by_device': {'gone': {'account_id': 'acct', 'messages': [
        message(4, device='gone', conversation='same')]}}})
    page._activate_thread(('history', 'same', 'gone'))
    page.composer.setPlainText('Retained history draft')
    assert page.selected_device_id == 'gone'
    assert 'history only' in page.context_label.text()
    assert 'history only' in page.validation_message()
    count = len(worker.requests); page.submit(); assert len(worker.requests) == count
    page.apply_snapshot({'devices': {'account_id': 'acct', 'devices': []}})
    assert page.selected_device_id == 'gone' and page.current_thread[2] == 'gone'
    assert page.composer.toPlainText() == 'Retained history draft'


def test_ai_context_and_media_references_follow_selected_phone_only(qtbot):
    page, worker = unified(qtbot)
    for device, number in [('a', 1), ('b', 2)]:
        page._activate_thread(('history', 'same', device))
        context = page.ai_context()
        assert context['device_id'] == device
        assert context['token']['thread'] == ['history', 'same', device]
        assert context['token']['message_ids'] == [number]
        assert [text.toPlainText() for text in page.timeline_content.findChildren(MessageText)] == [device + ' fictional history']
    token = page.ai_context()['token']
    page._activate_thread(('history', 'same', 'a'))
    assert not page.insert_ai_draft('Late B draft', token)
    assert not any(command in {'ai_prepare', 'ai_generate', 'media_image'} for command, _ in worker.requests)


def test_account_reset_discards_unified_filters_drafts_pending_and_late_success(qtbot):
    page, worker = unified(qtbot)
    request = create_request(page, worker)
    page.phone.setCurrentIndex(page.phone.findData('a'))
    page.apply_snapshot(snapshot([], account='new'))
    assert page.filter_device_id is None and page.phone.currentText() == 'All devices'
    assert page.current_thread is None and page.selected_device_id is None
    assert not page._drafts and not page._pending and not page._filter_contexts
    assert not page.sms_cache or all(part.get('account_id') == 'new' for part in page.sms_cache.values())
    succeed(worker, request)
    assert not page.actions and not page.composer.toPlainText()


def test_unified_contact_and_mms_reference_never_crosses_devices(qtbot):
    from pbdesk.desktop.media import RemoteImage
    page, worker = unified(qtbot)
    parts = {}
    for device, number in [('a', 11), ('b', 22)]:
        parts[device] = {'account_id': 'acct', 'messages': [message(number, device=device,
            conversation='same', recipients=[{'address': '+639171234567', 'name': 'Alex',
                'image_url': 'https://example.invalid/shared-contact.png'}],
            image_urls=['https://example.invalid/shared-mms.png'])]}
    page.apply_snapshot({'sms_by_device': parts})
    for device, number in [('a', 11), ('b', 22)]:
        page._activate_thread(('history', 'same', device))
        assert page.avatar_remote.reference['message_id'] == number
        images = page.timeline_content.findChildren(RemoteImage)
        assert len(images) == 1 and images[0].reference['message_id'] == number
        assert images[0].reference['account_id'] == 'acct'
    assert not any(command == 'media_image' for command, _ in worker.requests)


def test_unified_narrow_long_names_partial_and_read_errors_remain_accessible(qtbot, qapp):
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QPalette
    from pbdesk.desktop.theme import apply_theme, theme_signals
    manager = theme_signals(qapp)
    old = (QPalette(qapp.palette()), qapp.styleSheet(), manager.scheme, manager._last_colors)
    try:
        for scheme in ('light', 'dark'):
            apply_theme(qapp, scheme)
            page, worker = unified(qtbot, duplicate_names=True)
            for device in page.devices[:2]:
                device['nickname'] = 'Personal phone with a very long repeated nickname'
            page.apply_snapshot({'sms_by_device': {
                'a': {'account_id': 'acct', 'read_error': 'History read unavailable <fixture>', 'messages': []},
                'b': {'account_id': 'acct', 'messages': [message(4, device='b', conversation='same')],
                      'sync': {'state': 'partial', 'unavailable_count': 2}}}})
            page.resize(760, 600); qtbot.wait(30)
            assert page.width() == 760
            assert '2 need attention' in page.sync_status.text()
            assert 'History read unavailable <fixture>' in page.sync_status.accessibleDescription()
            assert '&lt;fixture&gt;' in page.sync_status.toolTip()
            assert page.sync_status.wordWrap()
            assert page.threads.count() == 2
            for n in range(2):
                row = page.threads.item(n)
                assert row.data(Qt.ItemDataRole.UserRole)[2] in row.toolTip()
                assert page.threads.visualItemRect(row).height() >= 100
            page.start_new()
            assert page.send_from.currentData() is None and not page.send_button.isEnabled()
            for widget in (page.phone, page.send_from, page.composer, page.send_button):
                top = widget.mapTo(page, widget.rect().topLeft())
                assert page.rect().contains(QRect(top, widget.size()))
            page.close()
    finally:
        manager._updating = True
        try:
            qapp.setStyleSheet(old[1]); qapp.setPalette(old[0]); qapp.processEvents()
            manager.scheme, manager._last_colors = old[2:]
            manager._scheduled = False
        finally:
            manager._updating = False


def test_same_recipient_local_actions_remain_bound_to_each_phone(qtbot):
    from test_desktop_chat import successful_action
    page, worker = unified(qtbot)
    actions = [successful_action({'account_id': 'acct', 'payload': {'kind': 'sms',
        'device_id': device, 'recipient': '+12025550123', 'body': device + ' pending',
        'transport': 'provider_plaintext'}}, id=number) for device, number in [('a', 71), ('b', 72)]]
    page.apply_snapshot({'actions': actions})
    assert page._threads[('local', '+12025550123', 'a')]['actions'] == [actions[0]]
    assert page._threads[('local', '+12025550123', 'b')]['actions'] == [actions[1]]
    for action in actions:
        page.open_action(action)
        assert page.filter_device_id is None
        assert page.current_thread == ('local', '+12025550123', action['device_id'])
        assert page.selected_device_id == action['device_id']
        assert [text.toPlainText() for text in page.timeline_content.findChildren(MessageText)] == [action['body']]
    assert not any(command in {'create_action', 'approve_action', 'refresh_sms'} for command, _ in worker.requests)


def test_device_metadata_change_updates_header_row_and_existing_bubble(qtbot):
    page, worker = unified(qtbot)
    page._activate_thread(('history', 'same', 'a'))
    page.composer.setPlainText('Keep this draft')
    devices = deepcopy(page.devices)
    devices[0].update(nickname='<Renamed phone>', active=False, can_sms=False)
    page.apply_snapshot({'devices': {'account_id': 'acct', 'devices': devices}})
    assert '<Renamed phone> · history only' in page.context_label.text()
    assert any('<Renamed phone> · history only' in label.text() for label in page.timeline_content.findChildren(QLabel))
    assert page.composer.toPlainText() == 'Keep this draft'
    assert 'history only' in page.validation_message()
    assert page.filter_device_id is None


def test_initial_same_account_non_all_worker_filter_is_explicitly_reset(qtbot):
    worker = FakeWorker()
    worker.device_id = 'b'
    worker.account_id = 'acct'
    page = SmsPage(worker)
    qtbot.addWidget(page)
    page.apply_snapshot(snapshot([message()]))
    assert len(worker.requests) == 1
    command, payload = worker.requests[0]
    assert command == 'select_device'
    assert payload == {'device_id': None, 'account_id': 'acct',
                       'request_id': page.selection_request_id}
    assert page.selection_request_id and page.filter_device_id is None


def test_account_reset_default_rejects_old_filter_generation_without_read_command(qtbot):
    page, worker = unified(qtbot)
    page.phone.setCurrentIndex(page.phone.findData('a'))
    old_generation = page.selection_request_id
    count = len(worker.requests)
    page.apply_snapshot(snapshot([], account='new'))
    assert len(worker.requests) == count
    assert page.selection_request_id is None and page.filter_device_id is None
    page.apply_snapshot({'sms_request_id': old_generation, 'sms_by_device': {
        'a': {'account_id': 'new', 'messages': [message(90, account='new')]}}})
    assert not page.sms_cache['a']['messages']
