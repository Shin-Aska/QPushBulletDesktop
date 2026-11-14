"""Selectable SMS context and tone through production widgets, using fake core results."""
from copy import deepcopy
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout
from pbdesk.desktop.ai import SmsAiAssistant
from test_desktop_ai import FakeAi, FakeSms, preview, generated
from test_desktop_saved_summaries import reply


def candidates(start=1, count=20):
    return [{'id':number,'timestamp':1780000000+number*60,
             'direction':'outgoing' if number%3==0 else 'incoming',
             'sender':'You' if number%3==0 else 'Alex',
             'body':f'Message {number}: retained local conversation text.', 'body_truncated':False}
            for number in range(start,start+count)]


def prepared(client, rows=None, **changes):
    rows=rows if rows is not None else candidates()
    payload=next(payload for command,payload in reversed(client.requests) if command=='ai_prepare')
    chosen=payload.get('message_ids')
    chosen=[row['id'] for row in rows] if chosen is None else chosen
    data=preview(candidates=deepcopy(rows),selected_ids=[row['id'] for row in rows if row['id'] in chosen],
                 tone=payload['tone'],count=len(chosen),has_older_messages=True,
                 text='\n'.join(row['body'] for row in rows if row['id'] in chosen))
    data.update(changes)
    reply(client,'ai_prepare',data)
    return data


def assistant(qtbot):
    client,sms=FakeAi(),FakeSms()
    dialog=QDialog(); dialog.resize(640,500); layout=QVBoxLayout(dialog)
    page=SmsAiAssistant(client,sms,dialog); layout.addWidget(page)
    qtbot.addWidget(dialog); dialog.show(); page.open_context(); prepared(client)
    return client,sms,page,dialog


def finish(client,page, text='Generated editable reply'):
    prepared(client); reply(client,'ai_generate',dict(generated(),text=text))


def checkbox(page,row,checked):
    page.preview.item(row,0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)


def test_open_table_latest20_initial_checked_plain_wrapped_and_expands(qtbot):
    client,sms,page,dialog=assistant(qtbot)
    assert [command for command,payload in client.requests]==['ai_prepare']
    assert client.requests[-1][1]['message_ids'] is None and client.requests[-1][1]['tone']=='Natural'
    assert page.preview.rowCount()==20 and page.selected_ids()==list(range(1,21))
    assert '20 of 20 selected' in page.context_toggle.text()
    assert not page.context_box.isVisible()
    qtbot.mouseClick(page.context_toggle,Qt.MouseButton.LeftButton); qtbot.wait(20)
    assert page.preview.isVisible() and page.preview.height()>=215 and dialog.height()>500
    assert page.preview.wordWrap() and page.preview.item(0,3).text().startswith('Message 1:')
    assert page.preview.verticalScrollBar().maximum()>0
    qtbot.mouseClick(page.context_toggle,Qt.MouseButton.LeftButton)
    assert not page.context_box.isVisible() and dialog.height()==500


def test_selection_exact_ids_zero_blocks_and_refresh_preserves_survivors(qtbot):
    client,sms,page,dialog=assistant(qtbot)
    checkbox(page,0,False); checkbox(page,5,False)
    assert len(page.selected_ids())==18
    page.refresh_context(); prepared(client,candidates(2))
    assert page.preview.rowCount()==20
    assert 6 not in page.selected_ids() and 21 not in page.selected_ids()
    assert page.preview.item(19,0).checkState()==Qt.CheckState.Unchecked
    page.generate_button.click()
    payload=client.requests[-1][1]
    assert payload['message_ids']==list(range(2,6))+list(range(7,21))
    text=prepared(client,candidates(2))['text']
    assert 'Message 6:' not in text and 'Message 21:' not in text
    assert client.requests[-1][0]=='ai_generate'
    reply(client,'ai_generate',generated())
    for row in range(20): checkbox(page,row,False)
    assert not page.generate_button.isEnabled() and '0 of 20 selected' in page.context_toggle.text()
    count=len(client.requests); page.generate(); assert len(client.requests)==count
    page.refresh_context(); prepared(client,candidates(3))
    assert not page.selected_ids() and not page.generate_button.isEnabled()


def test_message_rows_reflow_when_dialog_narrows(qtbot):
    client,sms,page,dialog=assistant(qtbot)
    rows=candidates(); rows[0]['body']='A longer retained message about our plans for tomorrow. ' * 5
    page.refresh_context(); prepared(client,rows)
    page.context_toggle.setChecked(True); dialog.resize(800,760); qtbot.wait(20)
    wide_height=page.preview.rowHeight(0)
    dialog.resize(440,760); qtbot.wait(20)
    assert page.preview.rowHeight(0)>wide_height
    required=page.preview.sizeHintForRow(0)
    assert page.preview.rowHeight(0)>=required


def test_draft_again_tone_cancel_presets_custom_and_editor_preserved(qtbot):
    client,sms,page,dialog=assistant(qtbot)
    page.generate_button.click(); finish(client,page)
    page.output.setPlainText('My edited previous reply')
    count=len(client.requests); page.generate_button.click()
    tone=page.tone_dialog
    assert tone.isVisible() and tone.windowModality()==Qt.WindowModality.WindowModal
    assert tone.tone.currentText()=='Natural' and len(client.requests)==count
    tone.tone.setCurrentText('Friendly'); qtbot.mouseClick(tone.buttons.button(QDialogButtonBox.StandardButton.Cancel),Qt.MouseButton.LeftButton)
    assert len(client.requests)==count and page.output.toPlainText()=='My edited previous reply'
    page.generate_button.click(); tone=page.tone_dialog
    tone.tone.setCurrentText('Professional'); qtbot.mouseClick(tone.buttons.button(QDialogButtonBox.StandardButton.Ok),Qt.MouseButton.LeftButton)
    assert client.requests[-1][1]['tone']=='Professional' and page.output.toPlainText()=='My edited previous reply'
    prepared(client); reply(client,'ai_generate',{},ok=False)
    assert page.output.toPlainText()=='My edited previous reply'
    page.generate_button.click(); tone=page.tone_dialog
    assert tone.tone.currentText()=='Professional'
    tone.tone.setCurrentText('   '); assert not tone.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    tone.tone.setCurrentText('x'*250); assert len(tone.tone.currentText())==200
    tone.tone.setCurrentText('  Warm, with one clear question  '); tone.accept()
    assert client.requests[-1][1]['tone']=='Warm, with one clear question'
    prepared(client); page.output.setPlainText('Typed while drafting'); reply(client,'ai_generate',generated())
    assert page.output.toPlainText()=='Typed while drafting' and 'edits were kept' in page.feedback.text()
    assert not any(command in ('create_action','approve_action','dispatch') for command,payload in client.requests)


def test_selection_change_cancels_inflight_and_ignores_late_result(qtbot):
    client,sms,page,dialog=assistant(qtbot)
    page.output.setPlainText('Previous text stays')
    page.generate(); page.tone_dialog.accept(); prepared(client)
    identity=client.requests[-1][1]['request_id']
    checkbox(page,0,False)
    reply(client,'ai_generate',generated(),identity=identity)
    assert page.output.toPlainText()=='Previous text stays'
    assert 'selection changed' in page.feedback.text()
    assert any(command=='ai_cancel' and payload['target_id']==identity for command,payload in client.requests)


def test_unselected_incoming_during_prepare_excluded_and_composer_guards_hold(qtbot):
    for change in ('incoming','composer','route','account','pending'):
        client,sms,page,dialog=assistant(qtbot)
        checkbox(page,0,False); page.generate_button.click()
        if change=='incoming':
            sms.context['token']['message_ids']=list(range(2,22)); sms.context['token']['context_version']=2
        elif change=='composer': sms.context['token']['draft']='Typed newer composer'
        elif change=='route': sms.context['token']['recipient']='+639170000099'
        elif change=='account': sms.context['token']['account_id']='other'
        else: sms.context=None
        data=prepared(client,candidates(2))
        if change=='incoming':
            assert client.requests[-1][0]=='ai_generate'
            assert 21 not in data['selected_ids'] and 'Message 21:' not in data['text']
            assert page.token==sms.context['token']
        else:
            assert not any(command=='ai_generate' for command,payload in client.requests)
        page.closed(); dialog.close()


def test_scope_change_resets_checks_tone_and_closes_prompt(qtbot):
    client,sms,page,dialog=assistant(qtbot)
    checkbox(page,0,False); page.output.setPlainText('Previous scope draft'); page.generate()
    tone=page.tone_dialog; tone.tone.setCurrentText('Brief')
    sms.context['conversation_id']='another'; sms.context['token']['thread']='another'
    page.open_context(); prepared(client,candidates(101))
    assert not tone.isVisible() and not page.output.toPlainText()
    assert page.selected_ids()==list(range(101,121)) and page.tone=='Natural'
    assert not any(command=='ai_generate' for command,payload in client.requests)


def test_missing_new_context_contract_is_actionable_no_unchecked_cloud(qtbot):
    client,sms,page,dialog=assistant(qtbot)
    page.refresh_context(); reply(client,'ai_prepare',preview(candidates=None))
    assert not page.generate_button.isEnabled() and page.preview.rowCount()==0
    assert 'Restart the updated core' in page.feedback.text()
    assert not any(command=='ai_generate' for command,payload in client.requests)
