"""Literal, complete supplied content and click-only notification web links."""
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QTextDocument, QTextOption
from PyQt6.QtWidgets import QApplication, QComboBox
import pytest

from pbdesk.desktop.notification_content import linked_text, valid_http_url
from pbdesk.desktop.theme import apply_theme, colors, current_scheme
from test_desktop_status import CAP, window


def anchors(browser):
    result = []
    block = browser.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isAnchor():
                result.append((fragment.text(), fragment.charFormat().anchorHref()))
            iterator += 1
        block = block.next()
    return result


def item(**patch):
    value = {'id': 1, 'type': 'mirror', 'title': 'Fictional notification',
             'body': 'First line\nSecond line', 'created_at': '2026-11-14T00:00:00Z', 'url': None}
    value.update(patch)
    return value


def test_complete_long_body_newlines_long_query_wrap_and_raw_copy(window, qtbot):
    widget, worker = window
    query_url = 'https://example.invalid/report?token=' + 'x' * 1800 + '&view=one&ref=(two)#section'
    body = 'First  line\n\nSecond line <b>literal</b>\n' + 'Full supplied text. ' * 750 + '\n' + query_url + '\nFinal supplied line'
    message = item(title='Title <tag>\nSecond title line', body=body, url='https://example.invalid/top?a=1&b=2')
    worker.snapshot.emit({'capabilities': CAP, 'items': [message]})
    widget.resize(850, 540)
    qtbot.wait(40)
    rendered = widget.detail.toPlainText()
    assert body in rendered
    assert message['title'] in rendered
    assert query_url in rendered
    assert widget.detail.wordWrapMode() == QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
    assert widget.detail.horizontalScrollBar().maximum() == 0
    assert widget.detail.verticalScrollBar().maximum() > 0
    links = [target for text, target in anchors(widget.detail)]
    assert query_url in links
    assert message['url'] in links
    widget.copy_item_button.click()
    assert QApplication.clipboard().text() == '\n\n'.join([message['title'], body, message['url']])


def test_markup_is_literal_and_resources_and_opening_are_disabled(window, monkeypatch):
    widget, worker = window
    opened = []
    monkeypatch.setattr('pbdesk.desktop.app.QDesktopServices.openUrl', lambda url: opened.append(url.toString()))
    body = '<img src="https://example.invalid/pixel">\n<script>bad()</script>\n[Useful](https://example.invalid/a_(b)?x=1&y=2)\nLabel (https://example.invalid/other).'
    worker.snapshot.emit({'items': [item(body=body)]})
    assert body in widget.detail.toPlainText()
    assert not widget.detail.openLinks() and not widget.detail.openExternalLinks()
    assert not opened
    assert widget.detail.loadResource(QTextDocument.ResourceType.ImageResource, QUrl('https://example.invalid/pixel')) is None
    assert widget.detail.loadResource(QTextDocument.ResourceType.HtmlResource, QUrl('file:///tmp/fictional')) is None
    assert not widget.detail.document().resource(QTextDocument.ResourceType.ImageResource, QUrl('https://example.invalid/pixel'))
    targets = [target for text, target in anchors(widget.detail)]
    assert 'https://example.invalid/a_(b)?x=1&y=2' in targets
    assert 'https://example.invalid/other' in targets
    assert not any(fragment.charFormat().isImageFormat() for fragment in _fragments(widget.detail))
    widget.detail.anchorClicked.emit(QUrl('https://example.invalid/other'))
    assert opened == ['https://example.invalid/other']
    for unsafe in ['javascript:alert(1)', 'file:///tmp/fictional', 'https://user:secret@example.invalid/', 'https://example.invalid/incomplete…']:
        widget.detail.anchorClicked.emit(QUrl(unsafe))
    assert opened == ['https://example.invalid/other']


def _fragments(browser):
    block = browser.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                yield fragment
            iterator += 1
        block = block.next()


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'file:///tmp/fictional', 'ftp://example.invalid/',
    'https://user:secret@example.invalid/', 'https://example.invalid/path...',
    'https://example.invalid/path…', 'https://example.invalid/a\x00b',
    'https://example.invalid/a\tb', 'https://example.invalid/%0Asecret',
    'https://example.invalid/\u202ebad', 'https://example.invalid:99999/',
])
def test_unsafe_or_visibly_incomplete_url_is_plain_text(window, url):
    widget, worker = window
    assert not valid_http_url(url)
    worker.snapshot.emit({'items': [item(body=url, url=url)]})
    assert not anchors(widget.detail)
    assert '<a href=' not in linked_text(url)


def test_mirror_note_only_and_title_does_not_synthesize_links(window):
    widget, worker = window
    message = item(title='https://example.invalid/title', body='https://example.invalid/path_(balanced), end.')
    worker.snapshot.emit({'items': [message]})
    assert 'Shown as provided by the phone.' in widget.detail.toPlainText()
    assert anchors(widget.detail) == [('https://example.invalid/path_(balanced)', 'https://example.invalid/path_(balanced)')]
    worker.snapshot.emit({'items': [dict(message, type='note')]})
    assert 'The source app may shorten' not in widget.detail.toPlainText()


def test_settings_switch_scheme_rerenders_selected_content_and_preserves_context(window, qapp, qtbot, tmp_path, monkeypatch):
    widget, worker = window
    monkeypatch.setenv('PBDESK_STATE_DIR', str(tmp_path))
    apply_theme(qapp, 'light')
    messages = [item(id=1), item(id=2, body='Selected supplied body\n' + 'More text\n' * 100)]
    device = {'id': 'fictional-phone', 'nickname': 'Fictional phone', 'active': True, 'can_push': True, 'can_sms': True}
    worker.snapshot.emit({'capabilities': CAP, 'items': messages, 'devices': {'account_id': CAP['account_id'], 'devices': [device]}})
    widget.list.setCurrentIndex(widget.model.index(1))
    widget.detail.verticalScrollBar().setValue(30)
    before_scroll = widget.detail.verticalScrollBar().value()
    before_sync = widget.inbox_sync.text()
    before_cap = dict(widget.capabilities)
    widget.sms_page.composer.setPlainText('Fictional unsent draft')
    light_html = widget.detail.toHtml()
    before_requests = list(worker.requests)
    changed = []
    from pbdesk.desktop.theme import theme_signals
    theme_signals(qapp).changed.connect(lambda: changed.append(True))
    widget.show_settings()
    dialog = widget.dialogs[-1]
    assert dialog.windowTitle() == 'Settings'
    scheme = dialog.findChild(QComboBox, 'appearanceScheme')
    assert scheme.currentData() == 'light'
    assert 'System' in scheme.itemText(scheme.findData('system'))
    scheme.setCurrentIndex(scheme.findData('dark'))
    qtbot.wait(20)
    assert current_scheme(qapp) == 'dark' and changed
    assert (tmp_path / 'ui-preferences.json').exists()
    assert widget.list.currentIndex().data(Qt.ItemDataRole.UserRole)['id'] == 2
    assert widget.sms_page.composer.toPlainText() == 'Fictional unsent draft'
    assert widget.capabilities == before_cap and widget.inbox_sync.text() == before_sync
    assert widget.detail.verticalScrollBar().value() == before_scroll
    assert widget.detail.toHtml() != light_html
    assert colors(widget.palette())['text'] in widget.detail.toHtml()
    assert colors(widget.palette())['text'] in widget.device_detail.document().defaultStyleSheet()
    assert worker.requests == before_requests
    dialog.reject()
    apply_theme(qapp, 'system')
