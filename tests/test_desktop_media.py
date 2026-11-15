"""Native raster/thumbnail tests with synthetic pixels and fake desktop workers."""
import base64

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from pbdesk.desktop.media import RemoteImage, contact_icon, decode_image, inline_image, media_icon
from test_desktop_chat import FakeWorker, message, page, snapshot


def picture(width=48, height=32):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor('#d32f2f'))
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    return bytes(array)


def encoded():
    return base64.b64encode(picture()).decode()


def test_inline_native_raster_and_safe_fallbacks(qtbot):
    image = inline_image(encoded())
    assert image is not None and image.pixelColor(0, 0) == QColor('#d32f2f')
    assert not media_icon({'icon': encoded()}).isNull()
    assert not media_icon({'icon': 'invalid'}).isNull()
    assert inline_image('invalid') is None
    assert inline_image('x' * 32769) is None
    assert inline_image(base64.b64encode(b'<svg><image href="https://evil.example"/></svg>').decode()) is None
    assert decode_image(picture(9000, 1)) is None
    single = contact_icon([{'thumbnail': encoded()}], 'Synthetic').pixmap(36, 36).toImage()
    group = contact_icon([{'thumbnail': encoded()}, {'name': 'Other'}], 'Synthetic').pixmap(36, 36).toImage()
    assert single.pixelColor(16, 16) == QColor('#d32f2f')
    assert group.pixelColor(16, 16) != QColor('#d32f2f')


def test_remote_requires_click_preserves_preview_and_discards_old_result(qtbot, tmp_path):
    worker = FakeWorker()
    reference = {'account_id': 'acct', 'kind': 'sms', 'message_id': 1, 'index': 0}
    widget = RemoteImage(worker, reference)
    qtbot.addWidget(widget)
    widget.resize(340, 240)
    widget.show()
    qtbot.wait(10)
    assert worker.requests == []
    qtbot.mouseClick(widget.load_button, Qt.MouseButton.LeftButton)
    command, value = worker.requests[-1]
    assert command == 'media_image' and not any('url' in key for key in value)
    worker.result.emit({'command': command, 'request_id': value['request_id'], 'account_id': 'other',
        'data': {'account_id': 'other', 'data': encoded()}})
    assert widget.image is None
    worker.result.emit({'command': command, 'request_id': value['request_id'], 'account_id': 'acct',
        'data': {'account_id': 'acct', 'data': encoded()}})
    assert widget.image is not None and widget.preview.isVisible()
    old_image = widget.image
    widget.set_reference(reference)
    assert widget.image is old_image
    capture = tmp_path / 'synthetic-native-image.png'
    assert widget.grab().save(str(capture)) and capture.stat().st_size > 100
    widget.set_reference({**reference, 'message_id': 2})
    widget.load()
    old_id = widget.request_id
    widget.set_reference(None)
    worker.result.emit({'command': command, 'request_id': old_id, 'account_id': 'acct',
        'data': {'account_id': 'acct', 'data': encoded()}})
    assert widget.image is None and widget.isHidden()
    assert all(command == 'media_image' for command, _ in worker.requests)


def test_sms_thumbnail_mms_explicit_load_and_draft_survive(qtbot):
    value = message(recipients=[{'address': '+639171234567', 'name': 'Synthetic', 'thumbnail': encoded()}],
        body='', image_urls=['https://static.pushbullet.com/synthetic.png'])
    widget, worker = page(qtbot, [value])
    widget.composer.setPlainText('Preserved draft')
    assert widget.avatar.pixmap().toImage().pixelColor(16, 16) == QColor('#d32f2f')
    assert widget.threads.item(0).icon().pixmap(36, 36).toImage().pixelColor(16, 16) == QColor('#d32f2f')
    assert 'Image attachment' in widget.threads.item(0).text()
    preview = widget.timeline_content.findChild(RemoteImage)
    assert preview is not None and not any(command == 'media_image' for command, _ in worker.requests)
    preview.load()
    request = worker.requests[-1][1]
    worker.result.emit({'command': 'media_image', 'request_id': request['request_id'], 'account_id': 'acct',
        'data': {'account_id': 'acct', 'data': encoded()}})
    qtbot.wait(50)
    assert preview.image is not None and widget.composer.toPlainText() == 'Preserved draft'
    widget.apply_snapshot(snapshot([value]))
    assert widget.timeline_content.findChild(RemoteImage) is preview
    widget.apply_snapshot(snapshot([message(account='other')], account='other'))
    assert widget.composer.toPlainText() == ''
    assert not worker._media_cache


def test_ai_insertion_token_rejects_typing_pending_and_context_switch(qtbot):
    widget, worker = page(qtbot, [message(), message(2, conversation='two')])
    context = widget.ai_context()
    assert context and context['conversation_id']
    assert 'recipients' not in repr(context)
    widget.composer.setPlainText('Typed after prepare')
    assert not widget.insert_ai_draft('AI draft', context['token'])
    current = widget.ai_context()
    assert widget.insert_ai_draft('Explicit draft', current['token'])
    assert widget.composer.toPlainText() == 'Explicit draft'
    assert not any(command == 'create_action' for command, _ in worker.requests)
    token = widget.ai_context()['token']
    old_thread = widget.current_thread
    alternate = next(key for key in widget._threads if key != old_thread)
    widget._activate_thread(alternate, True)
    widget._activate_thread(old_thread, True)
    assert not widget.insert_ai_draft('Stale draft', token)
    widget.composer.setPlainText('Ready for review')
    widget.submit()
    assert widget.ai_context() is None
    assert not widget.insert_ai_draft('While pending', token)


def test_remote_contact_is_explicit_and_group_never_uses_one_members_photo(qtbot):
    records = [message(recipients=[{'address': '+639171234567', 'name': 'Synthetic',
        'image_url': 'https://lh6.googleusercontent.com/fixture'}])]
    widget, worker = page(qtbot, records)
    assert widget.avatar_remote.isVisible()
    assert widget.avatar_remote.load_button.text() == 'Load contact image'
    assert not any(command == 'media_image' for command, _ in worker.requests)
    widget.avatar_remote.load()
    command, request = worker.requests[-1]
    assert request['kind'] == 'contact'
    worker.result.emit({'command': command, 'request_id': request['request_id'], 'account_id': 'acct',
        'data': {'account_id': 'acct', 'data': encoded()}})
    assert widget.avatar.pixmap().toImage().pixelColor(16, 16) == QColor('#d32f2f')
    assert widget.avatar_remote.isHidden()
    group = message(recipients=[{'address': '+639171234567', 'image_url': 'https://lh6.googleusercontent.com/fixture'},
        {'address': '+15550000001'}], addresses=['+639171234567', '+15550000001'])
    widget.apply_snapshot(snapshot([group]))
    assert widget.avatar_remote.isHidden()
    assert widget.avatar.pixmap().toImage().pixelColor(16, 16) != QColor('#d32f2f')


def test_loading_image_above_view_keeps_visible_message_anchor(qtbot):
    records = [message(index + 1, body='Synthetic text ' * 8,
        image_urls=['https://static.pushbullet.com/fixture.png'] if index == 0 else []) for index in range(20)]
    widget, worker = page(qtbot, records)
    scroll = widget.timeline.verticalScrollBar()
    scroll.setValue(scroll.maximum() // 2)
    qtbot.wait(20)
    anchor = widget._anchor()
    preview = widget.timeline_content.findChild(RemoteImage)
    preview.load()
    request = worker.requests[-1][1]
    worker.result.emit({'command': 'media_image', 'request_id': request['request_id'], 'account_id': 'acct',
        'data': {'account_id': 'acct', 'data': encoded()}})
    qtbot.wait(80)
    assert widget._anchor()[0] == anchor[0]
    assert abs(widget._anchor()[1] - anchor[1]) <= 3


def test_remote_contact_error_survives_polling_and_can_retry(qtbot):
    records = [message(recipients=[{'address': '+15550000001',
        'image_url': 'https://dl3.pushbulletusercontent.com/synthetic/contact.png'}])]
    widget, worker = page(qtbot, records)
    widget.composer.setPlainText('Preserved draft')
    remote = widget.avatar_remote
    qtbot.mouseClick(remote.load_button, Qt.MouseButton.LeftButton)
    request = worker.requests[-1][1]
    widget.apply_snapshot(snapshot(records))
    assert remote.request_id == request['request_id']
    assert remote.status.text() == 'Loading image…'
    assert not remote.load_button.isEnabled()
    worker.result.emit({'command': 'media_image', 'request_id': request['request_id'],
        'account_id': 'acct', 'error': 'Synthetic failure'})
    error = remote.status.text()
    assert 'unavailable' in error
    assert remote.load_button.text() == 'Retry image'
    widget.apply_snapshot(snapshot(records))
    assert remote.status.text() == error
    assert remote.load_button.text() == 'Retry image'
    assert remote.load_button.isEnabled()
    assert len([command for command, _ in worker.requests if command == 'media_image']) == 1
    qtbot.mouseClick(remote.load_button, Qt.MouseButton.LeftButton)
    retry = worker.requests[-1][1]
    assert retry['request_id'] != request['request_id']
    worker.result.emit({'command': 'media_image', 'request_id': retry['request_id'], 'account_id': 'acct',
        'data': {'account_id': 'acct', 'data': encoded()}})
    widget.apply_snapshot(snapshot(records))
    assert remote.isHidden()
    assert widget.avatar.pixmap().toImage().pixelColor(16, 16) == QColor('#d32f2f')
    assert widget.composer.toPlainText() == 'Preserved draft'
