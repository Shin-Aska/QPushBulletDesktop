"""Production widgets with synthetic core results; no database or outgoing services."""
from copy import deepcopy
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox
from pbdesk.desktop.ai import SummaryPage, SmsAiAssistant
from pbdesk.desktop.summary_widgets import PresetControls, PresetEditor, metrics_text
from test_desktop_ai import FakeAi, FakeSms, preview, generated, digest


def reply(client, command, data, ok=True, identity=None):
    payload = next(p for c,p in reversed(client.requests) if c == command)
    client.result.emit({'command':command, 'request_id':identity or payload['request_id'], 'ok':ok, 'data':deepcopy(data), 'error':'Revision conflict; reload the preset.' if not ok else None})


def preset(identifier='all', **changes):
    data = dict(id=identifier, account_id='acct', revision=1, archived=False,
        name='All activity', sources=['notifications','sms'], device_ids=[], conversations=[],
        conversation_filter='all', window_hours=24, model_override=None)
    data.update(changes); return data


def entry(identifier='one', **changes):
    data = dict(id=identifier, account_id='acct', preset_snapshot=preset(), preset_id='all', pinned=False,
        markdown='# Saved summary\n\n**Read this**', generated_at='2026-11-13T09:00:00Z',
        saved_at='2026-11-14T09:00:00Z', resolved_model='synthetic-model', prompt_version='v2',
        input_tokens=120, output_tokens=40, cached_input_tokens=0, latency_ms=800, reuse_count=1, truncated=False, notification_count=3, sms_count=8, conversation_count=2,
        window_start=1794560400, window_end=1794646800, covered_oldest='2026-11-13T09:00:00Z', covered_newest='2026-11-14T09:00:00Z')
    data.update(changes); return data


def summary(qtbot):
    client=FakeAi(); page=SummaryPage(client); qtbot.addWidget(page)
    page.set_context('acct',[])
    reply(client,'ai_presets',{'account_id':'acct','presets':[preset(),preset('other', name='Notifications only')]})
    reply(client,'ai_active_preset',{'preset':preset(), 'revision':3})
    reply(client,'ai_preset_options',{'account_id':'acct','devices':[], 'conversations':[], 'truncated':False})
    return client,page


def test_passive_preset_population_selection_and_edit_does_not_generate(qtbot):
    client,page=summary(qtbot)
    assert not any(c in ('ai_generate','ai_digest','ai_activate_preset') for c,p in client.requests)
    page.presets.combo.setCurrentIndex(1)
    assert not any(c == 'ai_activate_preset' for c,p in client.requests)
    page.presets.activate.click()
    assert client.requests[-1][0] == 'ai_activate_preset'
    assert client.requests[-1][1]['expected_revision'] == 3
    assert client.requests[-1][1]['preset_id'] == 'other'
    assert not any(c in ('ai_generate','ai_digest') for c,p in client.requests)
    page.stop()


def test_history_detail_pin_metrics_and_latest_poll_stays_separate(qtbot):
    client,page=summary(qtbot)
    reply(client,'ai_settings',{'configured':True})
    reply(client,'ai_digest',digest())
    latest=page.output.toPlainText()
    history=page.history; history.load()
    reply(client,'ai_history',{'items':[entry()], 'total':1, 'offset':0,'limit':50})
    history.listing.setCurrentRow(0); reply(client,'ai_history_detail',entry())
    assert 'Read this' in history.output.toPlainText()
    assert '14 Nov 2026' in history.heading.text() and '13 Nov 2026' in history.heading.text()
    assert 'Input tokens: 120' in history.details.toPlainText() and 'Cached input tokens: 0' in history.details.toPlainText()
    assert 'Cost: Unavailable' in history.details.toPlainText()
    history.pin.click(); assert client.requests[-1][0] == 'ai_pin_history'
    assert client.requests[-1][1]['pinned'] is True
    reply(client,'ai_pin_history',entry(pinned=True))
    reply(client,'ai_history',{'items':[entry(pinned=True)],'total':1,'limit':50,'offset':0})
    assert history.listing.currentItem().data(Qt.ItemDataRole.UserRole) == 'one'
    page.refresh(); reply(client,'ai_digest',digest('updating'))
    assert page.output.toPlainText() == latest
    assert history.listing.currentItem().data(Qt.ItemDataRole.UserRole) == 'one'
    assert 'Read this' in history.output.toPlainText()
    assert not any(c == 'ai_activate_preset' for c,p in client.requests)
    page.stop()


def test_account_boundary_and_stale_history_queries(qtbot):
    client,page=summary(qtbot); history=page.history
    history.load(); old=client.requests[-1][1]['request_id']
    history.search.setText('new search'); history.filter_changed()
    reply(client,'ai_history',{'items':[entry('stale')],'total':1,'limit':50,'offset':0},identity=old)
    assert history.listing.count() == 0
    current=client.requests[-1][1]['request_id']
    page.set_context('other',[])
    reply(client,'ai_history',{'items':[entry()],'total':1,'limit':50,'offset':0},identity=current)
    assert history.listing.count() == 0 and not history.output.toPlainText()
    assert page.presets.combo.count() == 0 and page.presets.choices == ([],[])
    page.stop()


def test_editor_exact_pairs_selected_empty_validation_and_no_activation(qtbot):
    choices=([{'id':'a','nickname':'Phone A'},{'id':'b','nickname':'Phone B'}], [
        {'device_id':'a','conversation_id':'same','name':'Alex'}, {'device_id':'b','conversation_id':'same','name':'Alex'}])
    editor=PresetEditor(None,choices); qtbot.addWidget(editor)
    editor.name.setText('Selected conversations'); editor.selected.setChecked(True)
    captured=[]; editor.saved.connect(captured.append); editor.save()
    assert not captured and 'at least one conversation' in editor.feedback.text()
    editor.conversations.item(1).setCheckState(Qt.CheckState.Checked); editor.save()
    assert captured[-1]['conversations'] == [{'device_id':'b','conversation_id':'same'}]
    assert captured[-1]['conversation_filter'] == 'selected'
    retained=preset(conversations=captured[-1]['conversations'], conversation_filter='selected')
    edit=PresetEditor(retained,([],[])); qtbot.addWidget(edit)
    assert edit.conversations.count() == 1
    edit.conversations.item(0).setCheckState(Qt.CheckState.Unchecked); result=[]; edit.saved.connect(result.append); edit.save()
    assert not result and edit.selected.isChecked()
    edit.conversations.item(0).setCheckState(Qt.CheckState.Checked); edit.selected.setChecked(False); edit.save()
    assert result[-1]['conversations'] == [] and result[-1]['conversation_filter'] == 'all'


def test_metrics_legacy_unknown_is_not_zero_or_estimated():
    text=metrics_text({'origin':'legacy','resolved_model':'old-model'})
    assert 'Input tokens: Not recorded' in text and 'Latency (ms): Not recorded' in text
    assert 'Cost: Unavailable' in text and 'old-model' in text
    assert 'Input tokens: 0' in metrics_text({'input_tokens':0})


def test_retention_preview_explicit_confirmation_revision_and_no_open_deletion(qtbot,monkeypatch):
    client,page=summary(qtbot); history=page.history
    history.open_settings()
    assert client.requests[-1][0] == 'ai_history_settings'
    assert not any(c == 'ai_save_history_settings' for c,p in client.requests)
    reply(client,'ai_history_settings',{'retention_days':30,'revision':7})
    history.retention.setCurrentIndex(history.retention.findData(7)); history.retention_save.click()
    assert client.requests[-1][1]['preview'] is True
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Cancel)
    reply(client,'ai_save_history_settings',{'retention_days':7,'revision':7,'affected_count':12})
    assert client.requests[-1][1]['preview'] is True
    history.retention_save.click()
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Apply)
    reply(client,'ai_save_history_settings',{'retention_days':7,'revision':7,'affected_count':12})
    payload=client.requests[-1][1]
    assert payload['preview'] is False and payload['expected_revision'] == 7 and payload['confirmed_retention_days'] == 7
    page.set_context('other',[])
    assert not history.settings_dialog.isVisible()
    page.stop()


def assistant(qtbot):
    client,sms=FakeAi(),FakeSms(); page=SmsAiAssistant(client,sms); qtbot.addWidget(page)
    page.open_context(); reply(client,'ai_prepare',preview())
    return client,sms,page


def draft(client,page):
    page.generate_button.click(); reply(client,'ai_prepare',preview()); reply(client,'ai_generate',generated())


def test_sms_open_local_only_one_click_and_current_editor_insert(qtbot):
    client,sms,page=assistant(qtbot)
    assert [c for c,p in client.requests] == ['ai_prepare']
    page.generate_button.click(); page.generate_button.click()
    assert [c for c,p in client.requests].count('ai_prepare') == 2
    reply(client,'ai_prepare',preview()); assert client.requests[-1][0] == 'ai_generate'
    reply(client,'ai_generate',generated())
    page.output.setPlainText('My final edited text'); page.insert_button.click()
    assert sms.insertions == ['My final edited text']
    assert not any(c in ('create_action','approve_action','dispatch') for c,p in client.requests)


def test_sms_cancel_late_result_inflight_edits_and_close(qtbot):
    client,sms,page=assistant(qtbot)
    page.generate_button.click(); reply(client,'ai_prepare',preview())
    identity=client.requests[-1][1]['request_id']; page.output.setPlainText('Typed during generation')
    reply(client,'ai_generate',generated(),identity=identity)
    assert page.output.toPlainText() == 'Typed during generation' and 'edits were kept' in page.feedback.text()
    page.output.clear(); page.generate_button.click(); reply(client,'ai_prepare',preview())
    identity=client.requests[-1][1]['request_id']; page.cancel_button.click()
    reply(client,'ai_generate',generated(),identity=identity)
    assert not page.output.toPlainText() and 'cancelled' in page.feedback.text()
    page.generate_button.click(); reply(client,'ai_prepare',preview()); identity=client.requests[-1][1]['request_id']; page.closed()
    reply(client,'ai_generate',generated(),identity=identity)
    assert not page.output.toPlainText() and not sms.insertions


def test_sms_reopen_other_thread_or_edited_composer_cannot_rebind_old_text(qtbot):
    client,sms,page=assistant(qtbot); draft(client,page); page.closed()
    sms.context['conversation_id']='other'; sms.context['token']['thread']='other'
    page.open_context()
    assert not page.output.toPlainText() and not page.insert_button.isEnabled()
    reply(client,'ai_prepare',preview()); draft(client,page); page.closed()
    sms.context['token']['draft']='New composer words'
    page.open_context()
    assert page.output.toPlainText() and not page.insert_button.isEnabled()
    page.output.setPlainText('Editor changed after composer changed')
    assert not page.insert_button.isEnabled()
    page.insert(); assert not sms.insertions


def test_sms_nonempty_composer_requires_explicit_replace_and_rechecks(qtbot,monkeypatch):
    client,sms,page=assistant(qtbot)
    sms.context['token']['draft']='Original composer'
    page.generate_button.click(); reply(client,'ai_prepare',preview()); reply(client,'ai_generate',generated())
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Cancel)
    page.insert_button.click(); assert not sms.insertions
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    page.output.setPlainText('Use current editor'); page.insert_button.click(); assert sms.insertions == ['Use current editor']


def test_sms_account_clear_and_no_cloud_after_changed_prepare(qtbot):
    client,sms,page=assistant(qtbot); page.generate_button.click()
    sms.context['token']['draft']='new'; reply(client,'ai_prepare',preview())
    assert not any(c == 'ai_generate' for c,p in client.requests)
    page.output.setPlainText('Account private draft')
    page.apply_snapshot({'account_id':'other','features':{'ai':True}})
    assert not page.output.toPlainText() and page.preview.rowCount() == 0 and not page.insert_button.isEnabled()


def test_history_date_filters_use_local_calendar_boundaries(qtbot, monkeypatch):
    import os, time
    original = os.environ.get('TZ')
    try:
        monkeypatch.setenv('TZ','Asia/Manila'); time.tzset()
        client,page=summary(qtbot)
        page.history.date_from.setText('2026-11-14'); page.history.date_to.setText('2026-11-14'); page.history.load()
        payload=client.requests[-1][1]
        assert payload['date_from'] == '2026-11-14T00:00:00+08:00'
        assert payload['date_to'] == '2026-11-15T00:00:00+08:00'
        from pbdesk.desktop.summary_widgets import stamp
        assert '14 Nov 2026 00:00' in stamp('2026-11-13T16:00:00Z')
        page.stop()
    finally:
        if original is None: os.environ.pop('TZ',None)
        else: os.environ['TZ']=original
        time.tzset()


def test_history_metrics_coverage_unknown_and_actual_zero():
    text=metrics_text({'notification_count':0,'sms_count':4,'conversation_count':1,
        'window_start':1780000000,'window_end':1780001000,
        'covered_oldest':'2026-11-13T09:00:00Z','covered_newest':'2026-11-13T10:00:00Z'})
    assert 'Coverage: 0 notifications · 4 SMS · 1 conversations' in text
    assert 'Window: Not recorded' not in text and 'Oldest covered: Not recorded' not in text
    assert 'Coverage: Not recorded notifications' in metrics_text({})


def test_remote_activation_same_id_edit_and_late_old_digest_are_guarded(qtbot):
    client,page=summary(qtbot); reply(client,'ai_settings',{'configured':True})
    initial=digest(); initial.update(active_preset_id='all',active_preset_revision=1,active_revision=3)
    reply(client,'ai_digest',initial); assert page.last_good
    page.refresh(); remote=digest(good=False); remote.update(active_preset_id='other',active_preset_revision=1,active_revision=4)
    reply(client,'ai_digest',remote)
    assert not page.output.toPlainText() and page.presets.active_id == 'other'
    reply(client,'ai_active_preset',{'preset':preset('all'),'revision':3})
    assert page.presets.active_id == 'other'
    page.refresh(); newer=digest(); newer.update(active_preset_id='other',active_preset_revision=1,active_revision=4)
    reply(client,'ai_digest',newer); assert page.last_good
    count=sum(c == 'ai_digest' for c,p in client.requests)
    reply(client,'ai_active_preset',{'preset':preset('other', revision=2),'revision':4})
    assert not page.output.toPlainText()
    assert sum(c == 'ai_digest' for c,p in client.requests) == count
    page.refresh()
    reply(client,'ai_digest',newer)
    assert not page.output.toPlainText()
    page.stop()


def test_first_active_response_order_selects_server_scope_without_activation(qtbot):
    for active_first in (True,False):
        client=FakeAi(); controls=PresetControls(client); qtbot.addWidget(controls); controls.set_account('acct')
        results=[('ai_active_preset',{'preset':preset('other'),'revision':3}),('ai_presets',{'presets':[preset(),preset('other')]})]
        for command,data in results if active_first else reversed(results): reply(client,command,data)
        assert controls.combo.currentData() == 'other'
        assert not any(c == 'ai_activate_preset' for c,p in client.requests)
        controls.set_account(None)


def test_history_removed_last_page_clamps_pagination(qtbot):
    client,page=summary(qtbot); history=page.history; history.offset=50; history.load()
    reply(client,'ai_history',{'items':[],'total':1,'limit':50,'offset':50})
    assert history.offset == 0 and client.requests[-1][1]['offset'] == 0
    reply(client,'ai_history',{'items':[entry()],'total':1,'limit':50,'offset':0})
    assert history.count.text() == '1–1 of 1'
    page.stop()


def test_narrow_history_stacks_with_readable_scrollable_detail(qtbot):
    client,page=summary(qtbot); page.resize(590,420); page.show(); page.tabs.setCurrentIndex(1)
    history=page.history; history.load(); reply(client,'ai_history',{'items':[entry()], 'total':1,'limit':50,'offset':0})
    history.listing.setCurrentRow(0); reply(client,'ai_history_detail',entry())
    history.details_button.setChecked(True); qtbot.wait(20)
    assert history.splitter.orientation() == Qt.Orientation.Vertical
    assert history.output.height() >= 140 and history.details.height() >= 100
    assert history.scroll.verticalScrollBar().maximum() > 0
    page.stop()


def test_sms_read_only_conversation_keeps_copyable_draft_without_use_or_send(qtbot):
    from pbdesk.desktop.chat import SmsPage
    from test_desktop_chat import FakeWorker, snapshot, message
    worker,client=FakeWorker(),FakeAi(); sms=SmsPage(worker); qtbot.addWidget(sms)
    sms.apply_snapshot(snapshot([message(addresses=['group-placeholder'])]))
    assert sms.ai_context()['can_reply'] is False
    page=SmsAiAssistant(client,sms); qtbot.addWidget(page); page.open_context(); reply(client,'ai_prepare',preview())
    assert 'read-only' in page.feedback.text()
    page.generate_button.click(); reply(client,'ai_prepare',preview()); reply(client,'ai_generate',generated())
    assert page.output.toPlainText() and not page.insert_button.isEnabled()
    assert 'read-only' in page.feedback.text()
    page.insert(); assert not sms.composer.toPlainText()
    assert not any(c in ('create_action','approve_action') for c,p in worker.requests)


def test_active_editor_discloses_apply_and_follows_shared_active_changes(qtbot):
    from PyQt6.QtWidgets import QDialogButtonBox
    client,page=summary(qtbot); controls=page.presets
    editor=PresetEditor(preset(),parent=controls,is_active=True); qtbot.addWidget(editor); controls.editor=editor
    assert editor.buttons.button(QDialogButtonBox.StandardButton.Save).text().replace('&&', '&') == 'Save & apply'
    assert 'next update may send matching cached text to OpenAI' in editor.active_notice.text()
    controls.send('ai_active_preset'); reply(client,'ai_active_preset',{'preset':preset('other'),'revision':4})
    assert editor.buttons.button(QDialogButtonBox.StandardButton.Save).text() == 'Save preset'
    assert 'without changing the active summary' in editor.active_notice.text()
    page.stop()


def test_preset_manager_child_editor_accepts_native_focus_keyboard_and_save(qtbot):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QListWidget, QPushButton, QDialogButtonBox
    client,page=summary(qtbot); page.show(); failures=[]
    def interact():
        manager=QApplication.activeModalWidget()
        try:
            assert manager.windowTitle() == 'Summary presets'
            manager.findChild(QListWidget).setCurrentRow(0)
            edit=next(button for button in manager.findChildren(QPushButton) if button.text() == 'Edit')
            qtbot.mouseClick(edit, Qt.MouseButton.LeftButton)
            editor=page.presets.editor
            assert editor.parentWidget() is manager
            editor.name.setFocus(); qtbot.waitUntil(lambda: QApplication.focusWidget() is editor.name)
            editor.name.selectAll(); qtbot.keyClicks(editor.name,'Native keyboard edit')
            qtbot.mouseClick(editor.buttons.button(QDialogButtonBox.StandardButton.Save),Qt.MouseButton.LeftButton)
            assert client.requests[-1][0] == 'ai_update_preset'
            assert client.requests[-1][1]['config']['name'] == 'Native keyboard edit'
            assert client.requests[-1][1]['expected_revision'] == 1
            reply(client,'ai_update_preset',preset(name='Native keyboard edit',revision=2))
        except Exception as error:
            failures.append(error)
        finally:
            if manager: manager.reject()
    QTimer.singleShot(0,interact)
    page.presets.manage()
    assert not failures, str(failures)
    page.stop()


def test_visible_sms_use_edited_draft_closes_and_restores_composer_focus(qtbot,monkeypatch):
    from PyQt6.QtWidgets import QApplication
    from test_desktop_ai import summary_window
    window,worker,client,data=summary_window(qtbot,monkeypatch)
    window.navigate(1); window.resize(1000,720); window.show(); window.raise_(); window.activateWindow()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    qtbot.mouseClick(window.sms_ai_button,Qt.MouseButton.LeftButton)
    reply(client,'ai_prepare',preview())
    qtbot.waitUntil(lambda: window.sms_ai_dialog.isActiveWindow())
    qtbot.mouseClick(window.sms_ai.generate_button,Qt.MouseButton.LeftButton)
    reply(client,'ai_prepare',preview()); reply(client,'ai_generate',generated())
    window.sms_ai.output.setFocus(); window.sms_ai.output.selectAll()
    qtbot.keyClicks(window.sms_ai.output,'Edited reply from visible assistant')
    qtbot.mouseClick(window.sms_ai.insert_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.sms_ai_dialog.isVisible())
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window and QApplication.focusWidget() is window.sms_page.composer)
    assert window.sms_page.composer.toPlainText() == 'Edited reply from visible assistant'
    assert not any(command in ('create_action','approve_action','dispatch') for command,*unused in worker.requests)
    window.exit_desktop()


def test_active_editor_notice_stays_visible_with_save_at_narrow_default_scroll(qtbot):
    from PyQt6.QtCore import QPoint, QRect
    from PyQt6.QtWidgets import QScrollArea, QDialogButtonBox
    editor=PresetEditor(preset(),is_active=True); qtbot.addWidget(editor)
    editor.resize(380,520); editor.show(); qtbot.wait(20)
    scroll=editor.findChild(QScrollArea); scroll.verticalScrollBar().setValue(0)
    notice=editor.active_notice
    visible_rect=QRect(notice.mapTo(editor,QPoint(0,0)),notice.size())
    assert notice.isVisible() and editor.rect().contains(visible_rect)
    assert notice.parentWidget() is editor
    assert visible_rect.bottom() < editor.buttons.geometry().top()
    assert notice.height() >= notice.fontMetrics().height() * 2
    assert editor.buttons.button(QDialogButtonBox.StandardButton.Save).isVisible()
    assert 'next update may send matching cached text to OpenAI' in notice.text()
