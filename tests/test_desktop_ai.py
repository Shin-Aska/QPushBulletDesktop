"""Synthetic Qt checks; no live account, wallet, model or provider calls."""
import asyncio
from copy import deepcopy
import threading
import uuid
import httpx
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from pbdesk.config import Settings
from pbdesk.credentials import health_response
from pbdesk.desktop.ai import SummaryPage, SmsAiAssistant, AISettings
from pbdesk.desktop.ai_worker import AiWorker
from pbdesk.desktop.chat import SmsPage


class FakeAi(QObject):
    result = pyqtSignal(dict)
    def __init__(self):
        super().__init__()
        self.requests = []
    def request(self, command, payload):
        self.requests.append((command, deepcopy(payload)))
        return payload.get('request_id')
    def reply(self, data, ok=True):
        command, payload = next((c, p) for c, p in reversed(self.requests) if c != 'ai_cancel')
        self.result.emit({'command': command, 'request_id': payload['request_id'], 'ok': ok, 'data': data})


def preview(account='acct', **changes):
    data = dict(account_id=account, context_id='context-fake', text='Exact source text', count=1,
                truncated=False, scope='Selected retained text', tone='Natural', selected_ids=[1],
                candidates=[{'id':1,'timestamp':1780000000,'direction':'incoming','sender':'Alex','body':'Exact source text','body_truncated':False}], has_older_messages=False)
    data.update(changes)
    return data


def generated(account='acct'):
    return dict(account_id=account, text='Suggested plain reply <b>hello</b>', count=1,
                truncated=False, scope='Selected retained SMS conversation', generated_at=1780000000)



def test_settings_constructor_no_request_and_key_clears(qtbot):
    client = FakeAi()
    page = AISettings(client)
    qtbot.addWidget(page)
    assert not client.requests
    page.key.setText('sk-fake-new-only')
    page.save_secret()
    assert not page.key.text()
    assert client.requests[-1][0] == 'ai_save_key'
    assert client.requests[-1][1]['key'] == 'sk-fake-new-only'
    page.use_key.click()
    assert client.requests[-1][0] == 'ai_use_key'


class FakeSms:
    def __init__(self):
        self.context = {'account_id': 'acct', 'device_id': 'phone', 'conversation_id': 'thread',
                        'token': {'account_id': 'acct', 'thread': 'thread', 'draft': '', 'revision': 1}}
        self.insertions = []
    def ai_context(self):
        return deepcopy(self.context)
    def insert_ai_draft(self, text, token):
        if self.context is None or token != self.context['token']:
            return False
        self.insertions.append(text)
        return True


def prepare_reply(qtbot):
    client, sms = FakeAi(), FakeSms()
    page = SmsAiAssistant(client, sms)
    qtbot.addWidget(page)
    page.open_context()
    client.reply(preview())
    page.generate_button.click()
    client.reply(preview())
    client.reply(generated())
    return client, sms, page


def test_reply_explicit_insert_no_automatic_send(qtbot):
    client, sms, page = prepare_reply(qtbot)
    assert not sms.insertions
    assert page.insert_button.isEnabled()
    page.insert_button.click()
    assert sms.insertions == ['Suggested plain reply <b>hello</b>']
    assert not page.insert_button.isEnabled()
    assert not any(c in ('create_action', 'approve_action', 'dispatch') for c, _ in client.requests)


def test_reply_rejects_typed_or_navigation_or_pending(qtbot):
    for change in ('typed', 'navigation', 'pending'):
        client, sms, page = prepare_reply(qtbot)
        if change == 'typed':
            sms.context['token']['draft'] = 'User wrote this while generating'
        elif change == 'navigation':
            sms.context['token']['thread'] = 'new-thread'
        else:
            sms.context = None
        page.insert_button.click()
        assert not sms.insertions
        assert 'changed' in page.feedback.text()


def test_reply_rechecks_context_before_cloud(qtbot):
    client, sms = FakeAi(), FakeSms()
    page = SmsAiAssistant(client, sms)
    qtbot.addWidget(page)
    page.open_context()
    client.reply(preview())
    page.generate_button.click()
    sms.context['token']['draft'] = 'fresh typing'
    client.reply(preview())
    assert not any(c == 'ai_generate' for c, _ in client.requests)


def test_real_composer_guard_only_inserts(qtbot):
    from test_desktop_chat import FakeWorker, snapshot, message
    worker = FakeWorker()
    page = SmsPage(worker)
    qtbot.addWidget(page)
    page.apply_snapshot(snapshot([message()]))
    token = page.ai_context()['token']
    assert page.insert_ai_draft('Editable suggestion', token)
    assert page.composer.toPlainText() == 'Editable suggestion'
    assert not any(c in ('create_action', 'approve_action') for c, _ in worker.requests)
    assert not page.insert_ai_draft('overwrite', token)
    token = page.ai_context()['token']
    page.submit()
    assert not page.insert_ai_draft('pending overwrite', token)


def test_ai_worker_signed_identity_and_cancellable_stop(qtbot, tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual_client = httpx.AsyncClient
    entered = threading.Event()
    calls = []
    async def handle(request):
        calls.append(request)
        if request.url.path == '/api/v1/health':
            return httpx.Response(200, json=health_response(settings, request.headers, str(uuid.uuid4()), True))
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr('pbdesk.desktop.ai_worker.httpx.AsyncClient', lambda **kw: actual_client(transport=httpx.MockTransport(handle), **kw))
    worker = AiWorker(settings)
    worker.start()
    qtbot.wait(30)
    assert not calls
    worker.request('ai_generate', {'context_id': 'fake'})
    qtbot.waitUntil(entered.is_set, timeout=2000)
    assert 'authorization' not in calls[0].headers
    assert calls[1].headers['authorization'].startswith('Bearer ')
    worker.stop()
    assert worker.wait(2000)
    assert not worker.isRunning()


def test_ai_worker_never_sends_bearer_to_unverified_core(qtbot, tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual_client = httpx.AsyncClient
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'ready': True, 'application': 'impostor'})
    monkeypatch.setattr('pbdesk.desktop.ai_worker.httpx.AsyncClient', lambda **kw: actual_client(transport=httpx.MockTransport(handle), **kw))
    worker = AiWorker(settings)
    with qtbot.waitSignal(worker.result, timeout=2000) as captured:
        worker.start()
        worker.request('ai_settings')
    worker.stop()
    assert worker.wait(2000)
    assert captured.args[0]['ok'] is False
    assert len(calls) == 1 and 'authorization' not in calls[0].headers


def test_actual_missing_capability_is_actionable(qtbot):
    client = FakeAi()
    summary, settings = SummaryPage(client), AISettings(client)
    qtbot.addWidget(summary)
    qtbot.addWidget(settings)
    for widget in (summary, settings):
        widget.apply_snapshot({'capabilities': {'account_id': 'acct', 'features': {'ai': False}}})
        assert 'Restart the updated core' in widget.feedback.text()
    assert not summary.refresh_button.isEnabled()
    assert not settings.save_key.isEnabled()


class WindowAi(FakeAi):
    def stop(self):
        pass

    def wait(self, timeout):
        return True


def summary_window(qtbot, monkeypatch):
    from pbdesk.desktop.app import MainWindow
    from test_desktop import FakeWorker, ITEM
    from test_desktop_chat import snapshot, message
    monkeypatch.setattr('pbdesk.desktop.app.QSystemTrayIcon.isSystemTrayAvailable', lambda: False)
    worker, client = FakeWorker(), WindowAi()
    window = MainWindow(worker, client)
    qtbot.addWidget(window)
    data = snapshot([message()])
    data['items'] = [deepcopy(ITEM)]
    worker.snapshot.emit(data)
    return window, worker, client, data


def digest(status='ready', account='acct', good=True):
    return {'account_id': account, 'status': status, 'scope': 'recent', 'error': 'Temporary provider error.' if status == 'error' else None,
            'last_good': {'text': 'Needs attention\nReply to the delivery question.\n\nConversation highlights\nFamily: Saturday plans confirmed.\n\nNotifications\nBuild completed successfully.',
                          'generated_at': 1780000000, 'window_start': 1779990000, 'window_end': 1780000000,
                          'counts': {'notifications': 3, 'sms': 8, 'total': 11}, 'model': 'gpt-6-luna', 'fingerprint': 'fake'} if good else None}


def test_summary_starts_automatically_and_singleflights_local_poll(qtbot):
    client = FakeAi()
    page = SummaryPage(client)
    qtbot.addWidget(page)
    page.set_context('acct', [{'id': 1}])
    assert client.requests[-1][0] == 'ai_settings'
    client.reply({'configured': True, 'model': 'gpt-6-luna'})
    assert client.requests[-1][0] == 'ai_digest'
    assert client.requests[-1][1]['scope'] == 'recent'
    count = len(client.requests)
    page.refresh()
    page.refresh(force=True)
    assert len(client.requests) == count
    client.reply(digest())
    assert page.poll.isActive()
    assert 'Needs attention' in page.output.toPlainText()
    assert '3 notifications' in page.scope.text() and '8 SMS' in page.scope.text()
    assert not hasattr(page, 'prepare_button')
    page.stop()
    assert not page.poll.isActive()


def test_summary_keeps_last_good_during_updates_errors_and_clears_account(qtbot):
    client = FakeAi()
    page = SummaryPage(client)
    qtbot.addWidget(page)
    page.set_context('acct', [])
    client.reply({'configured': True})
    client.reply(digest())
    good = page.output.toPlainText()
    for status in ('updating', 'error'):
        page.refresh()
        client.reply(digest(status, good=False))
        assert page.output.toPlainText() == good
        assert ('Updating' if status == 'updating' else 'error') in page.feedback.text()
    page.refresh()
    client.reply({'account_id': 'acct'}, ok=False)
    assert page.output.toPlainText() == good
    page.set_context('other', [])
    assert not page.output.toPlainText()
    assert page.last_good is None
    page.stop()


def test_summary_unconfigured_has_settings_action_without_retry_loop(qtbot):
    client = FakeAi()
    page = SummaryPage(client)
    qtbot.addWidget(page)
    page.set_context('acct', [])
    client.reply({'configured': False})
    count = len(client.requests)
    page.set_context('acct', [{'id': 4}])
    page.refresh()
    assert len(client.requests) == count and not page.poll.isActive()
    assert not page.refresh_button.isEnabled()
    with qtbot.waitSignal(page.settings_requested):
        page.settings_button.click()
    page.configuration_changed({'configured': True})
    assert client.requests[-1][0] == 'ai_digest'
    page.stop()


def test_settings_catalog_retains_custom_and_unsaved_model(qtbot):
    client = FakeAi()
    page = AISettings(client)
    qtbot.addWidget(page)
    page.send('ai_settings', {})
    page.model.setCurrentText('unsaved-custom')
    client.reply({'configured': True, 'model': 'stored-custom'})
    assert page.model.currentText() == 'unsaved-custom'
    assert client.requests[-1][0] == 'ai_models'
    page.model.setCurrentText('edited-during-catalog')
    client.reply({'models': ['gpt-6-luna', 'gpt-other'], 'selected': 'stored-custom', 'default': 'gpt-6-luna'})
    assert page.model.currentText() == 'edited-during-catalog'
    assert page.model.findText('stored-custom') >= 0
    assert 'recommended' in page.catalog_status.text()
    page.save_model.click()
    assert client.requests[-1][1]['model'] == 'edited-during-catalog'


def test_settings_no_catalog_until_configured_and_preserves_on_error(qtbot):
    client = FakeAi()
    page = AISettings(client)
    qtbot.addWidget(page)
    page.send('ai_settings', {})
    client.reply({'configured': False, 'model': 'stored-custom'})
    assert not any(c == 'ai_models' for c, _ in client.requests)
    page.use_key.click()
    client.reply({'configured': True, 'model': 'stored-custom'})
    assert client.requests[-1][0] == 'ai_models'
    client.reply({}, ok=False)
    assert page.model.currentText() == 'stored-custom'
    assert not page.key.text()


def test_main_window_summary_automatic_without_navigation_preserves_drafts(qtbot, monkeypatch):
    window, worker, client, data = summary_window(qtbot, monkeypatch)
    page = window.summary_page
    assert window.tabs.currentIndex() != 2
    assert client.requests[-1][0] == 'ai_settings'
    client.reply({'configured': True})
    client.reply(digest())
    good = page.output.toPlainText()
    count = len(client.requests)
    window.sms_page.composer.setPlainText('My unsent text')
    worker.snapshot.emit(deepcopy(data))
    assert page.output.toPlainText() == good
    assert len(client.requests) == count
    assert window.sms_page.composer.toPlainText() == 'My unsent text'
    window.ai_settings.configuration_changed.emit({'configured': False})
    assert not page.poll.isActive()
    window.exit_desktop()


def test_cached_digest_poll_preserves_selection_and_scheduled_status(qtbot):
    from PyQt6.QtGui import QTextCursor
    import time
    client = FakeAi()
    page = SummaryPage(client)
    qtbot.addWidget(page)
    page.set_context('acct', [])
    client.reply({'configured': True})
    client.reply(digest())
    cursor = page.output.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    page.output.setTextCursor(cursor)
    selected = page.output.textCursor().selectedText()
    page.refresh()
    response = digest('queued')
    response['next_refresh_at'] = time.time() + 30
    response['last_good']['truncated'] = True
    client.reply(response)
    assert page.output.textCursor().selectedText() == selected
    assert 'scheduled' in page.feedback.text()
    assert 'truncated' in page.scope.text()
    page.stop()


def test_summary_markdown_formats_and_scrolls(qtbot):
    from PyQt6.QtGui import QFont, QTextTable
    client = FakeAi()
    page = SummaryPage(client)
    qtbot.addWidget(page)
    page.resize(640, 480)
    page.show()
    page.set_context('acct', [])
    client.reply({'configured': True})
    response = digest()
    markdown = ('# Needs attention\n\n**Reply today** with an *updated time*.\n\n'
                '- Confirm the appointment\n- Review the document\n\n'
                '## Conversation highlights\n\n'
                '| Conversation | Update |\n| --- | --- |\n| Family | Plans confirmed |\n\n'
                '```text\nKeep <tag> literal in code\n```\n\n'
                '## Notifications\n\n' + '\n\n'.join(f'- Update {n}: retained local activity.' for n in range(70)))
    response['last_good']['text'] = markdown
    client.reply(response)
    document = page.output.document()
    plain = page.output.toPlainText()
    assert '# Needs attention' not in plain and '**Reply today**' not in plain
    assert 'Reply today' in plain and 'Keep <tag> literal in code' in plain
    assert document.find('Needs attention').blockFormat().headingLevel() == 1
    assert document.find('Reply today').charFormat().fontWeight() >= QFont.Weight.Bold
    assert document.find('updated time').charFormat().fontItalic()
    assert document.find('Confirm the appointment').block().textList() is not None
    assert any(isinstance(frame, QTextTable) for frame in document.rootFrame().childFrames())
    assert page.output.isReadOnly() and page.generated_text == markdown
    qtbot.waitUntil(lambda: page.output.verticalScrollBar().maximum() > 0)
    page.stop()


def test_summary_markdown_poll_and_updates_preserve_scroll(qtbot):
    from PyQt6.QtGui import QTextCursor
    client = FakeAi()
    page = SummaryPage(client)
    qtbot.addWidget(page)
    page.resize(640, 480)
    page.show()
    page.set_context('acct', [])
    client.reply({'configured': True})
    response = digest()
    response['last_good']['text'] = '\n\n'.join(f'## Update {n}\n\n**Details** for this conversation.' for n in range(70))
    client.reply(response)
    bar = page.output.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 300)
    cursor = page.output.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    page.output.setTextCursor(cursor)
    bar.setValue(200)
    selected = page.output.textCursor().selectedText()
    page.refresh()
    client.reply(deepcopy(response))
    assert bar.value() == 200 and page.output.textCursor().selectedText() == selected
    page.refresh()
    response['last_good']['text'] += '\n\n## Another update\n\nNew information.'
    client.reply(response)
    qtbot.wait(10)
    assert bar.value() == 200
    page.output.clear()
    page.refresh()
    client.reply(response)
    assert 'Another update' in page.output.toPlainText()
    page.stop()


def test_summary_markdown_blocks_resources_and_opens_only_explicit_safe_links(qtbot, tmp_path, monkeypatch):
    import base64
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QImage, QTextDocument
    client = FakeAi()
    page = SummaryPage(client)
    qtbot.addWidget(page)
    opened = []
    monkeypatch.setattr('pbdesk.desktop.ai.QDesktopServices.openUrl', lambda url: opened.append(url.toString()))
    image_path = tmp_path / 'private.png'
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0xff0000)
    assert image.save(str(image_path))
    local_url = QUrl.fromLocalFile(str(image_path))
    data_url = QUrl('data:image/png;base64,' + base64.b64encode(image_path.read_bytes()).decode('ascii'))
    page.set_context('acct', [])
    client.reply({'configured': True})
    response = digest()
    response['last_good']['text'] = (
        '[Read update](https://example.invalid/read)\n\n'
        '![Remote image](https://example.invalid/tracker.png)\n\n'
        f'![Private image]({local_url.toString()})\n\n'
        f'![Inline image]({data_url.toString()})\n\n'
        '<b>Untrusted HTML</b>\n\n'
        '<img src="https://example.invalid/html-tracker.png">')
    client.reply(response)
    assert not opened and not page.output.openLinks() and not page.output.openExternalLinks()
    assert '<b>Untrusted HTML</b>' in page.output.toPlainText()
    for url in (local_url, data_url, QUrl('https://example.invalid/tracker.png'), QUrl('https://example.invalid/html-tracker.png')):
        assert page.output.document().resource(QTextDocument.ResourceType.ImageResource, url) is None
    page.output.anchorClicked.emit(QUrl('https://example.invalid/read'))
    assert opened == ['https://example.invalid/read']
    for value in ('file:///tmp/private', 'javascript:alert(1)', 'data:text/html,test',
                  'https://user:secret@example.invalid/', 'https://example.invalid/incomplete…'):
        page.output.anchorClicked.emit(QUrl(value))
    assert opened == ['https://example.invalid/read']
    page.stop()


def test_catalog_notice_and_no_recommendation_for_unsaved_luna(qtbot):
    client = FakeAi()
    page = AISettings(client)
    qtbot.addWidget(page)
    page.send('ai_settings', {})
    client.reply({'configured': True, 'model': 'custom-saved'})
    page.model.setCurrentText('gpt-6-luna')
    client.reply({'models': ['another-text-model'], 'selected': 'custom-saved',
                  'notice': 'Your saved model was not listed by this account.'})
    assert 'recommended' not in page.catalog_status.text()
    assert 'not listed' in page.catalog_status.text()
    assert page.model.currentText() == 'gpt-6-luna'


def test_worker_models_and_digest_routes_use_only_verified_local_transport(qtbot, tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    actual_client = httpx.AsyncClient
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path == '/api/v1/health':
            return httpx.Response(200, json=health_response(settings, request.headers, str(uuid.uuid4()), True))
        return httpx.Response(200, json={'models': ['gpt-6-luna']} if request.method == 'GET' else digest())
    monkeypatch.setattr('pbdesk.desktop.ai_worker.httpx.AsyncClient', lambda **kw: actual_client(transport=httpx.MockTransport(handle), **kw))
    worker = AiWorker(settings)
    worker.start()
    for command in ('ai_models', 'ai_digest'):
        with qtbot.waitSignal(worker.result, timeout=2000):
            worker.request(command, {'account_id': 'acct', 'scope': 'recent', 'force': False} if command == 'ai_digest' else {})
    worker.stop()
    assert worker.wait(2000)
    assert [(r.method, r.url.path) for r in calls] == [('GET', '/api/v1/health'), ('GET', '/api/v1/ai/models'), ('GET', '/api/v1/health'), ('POST', '/api/v1/ai/digest')]


def test_late_metadata_cannot_override_saved_configuration_or_clear_typed_key(qtbot):
    client = FakeAi()
    page = AISettings(client)
    qtbot.addWidget(page)
    page.send('ai_settings', {})
    old_id = client.requests[-1][1]['request_id']
    page.use_key.click()
    client.reply({'configured': True, 'model': 'custom-saved'})
    page.key.setText('sk-new-unsaved-fake')
    client.reply({'models': ['custom-saved'], 'selected': 'custom-saved'})
    assert page.key.text() == 'sk-new-unsaved-fake'
    client.result.emit({'request_id': old_id, 'ok': True, 'data': {'configured': False, 'model': 'old'}})
    assert page._configured is True and page.model.currentText() == 'custom-saved'


def test_saved_configuration_supersedes_summary_startup_metadata(qtbot):
    client = FakeAi()
    page = SummaryPage(client)
    qtbot.addWidget(page)
    page.set_context('acct', [])
    old_id = client.requests[-1][1]['request_id']
    page.configuration_changed({'configured': True})
    client.result.emit({'request_id': old_id, 'ok': True, 'data': {'configured': False}})
    assert page.poll.isActive() and page._configured is True
    page.stop()
