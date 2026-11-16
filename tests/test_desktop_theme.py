"""Fictional Qt-only theme checks; never connect to core or load account data."""
import json

import pytest
from PyQt6.QtCore import QObject, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QStandardItem, QStandardItemModel, QTextCursor
from PyQt6.QtWidgets import QApplication, QHeaderView, QStyle, QStyleOptionViewItem, QTableView

from pbdesk.desktop.actions import ComposeDialog, ReviewDialog
from pbdesk.desktop.chat import MessageText, SmsPage
from pbdesk.desktop.theme import apply_theme, colors, current_scheme, set_color_scheme, theme_signals
from pbdesk.desktop.widgets import ConversationDelegate, NotificationDelegate, PillDelegate, _row_colors


class Worker(QObject):
    result = pyqtSignal(dict)
    snapshot = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.requests = []

    def request(self, *args):
        self.requests.append(args)


DEVICES = [dict(id='fictional-phone', nickname='Example phone', active=True, can_sms=True, can_push=True)]
ACTION = dict(id=42, revision=1, fingerprint='fictional-fingerprint', kind='sms',
              state='pending_approval', device_id='fictional-phone', recipient='+12025550123',
              title='', body='Literal <example> text\n' * 60, url=None,
              expires_at='2026-11-15T00:00:00Z', created_at='2026-11-14T00:00:00Z',
              requested_by='test', transport='provider_plaintext', upstream_id=None,
              error_message=None)


@pytest.fixture
def theme_app(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv('PBDESK_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('PBDESK_APPLICATION_DIR', str(tmp_path / 'app'))
    manager = theme_signals(qapp)
    old = (QPalette(qapp.palette()), qapp.styleSheet(), manager.scheme, manager._last_colors,
           QFont(qapp.font()))
    apply_theme(qapp, 'system')
    qapp.processEvents()
    yield qapp
    manager._updating = True
    try:
        qapp.setStyleSheet(old[1])
        qapp.setPalette(old[0])
        qapp.setFont(old[4])
        qapp.processEvents()
        manager.scheme, manager._last_colors = old[2:4]
        manager._scheduled = False
    finally:
        manager._updating = False


def _contrast(a, b):
    def luminance(value):
        rgb = QColor(value).getRgbF()[:3]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    left, right = sorted((luminance(a), luminance(b)))
    return (right + .05) / (left + .05)


def test_system_preserves_installed_style_palette_and_font(theme_app):
    app = theme_app
    before_palette, before_font = QPalette(app.palette()), QFont(app.font())
    # QSS uses a proxy; removing it exposes the same installed native style.
    app.setStyleSheet('')
    native_name = app.style().objectName()
    apply_theme(app, 'system')
    assert app.palette() == before_palette
    assert app.font() == before_font
    app.setStyleSheet('')
    assert app.style().objectName() == native_name
    apply_theme(app, 'system')


def test_preference_roundtrip_invalid_and_initialize_once(theme_app, tmp_path):
    app = theme_app
    path = tmp_path / 'state' / 'ui-preferences.json'
    from pbdesk.desktop.preferences import read_preferences, receive_preferences
    set_color_scheme(app, 'dark')
    assert read_preferences() == {'color_scheme': 'dark'}
    assert not path.exists()
    assert current_scheme(app) == 'dark'
    from pbdesk.desktop import preferences
    preferences._states.clear()  # A fresh process restores its session mirror from core.
    receive_preferences({'color_scheme': 'light'})
    apply_theme(app)
    assert current_scheme(app) == 'dark'  # Initialization is deliberately idempotent.
    manager = theme_signals(app)
    apply_theme(app, 'system')
    manager._last_colors = None  # Simulate a fresh UI process with the same state directory.
    apply_theme(app)
    assert current_scheme(app) == 'light'
    for invalid in ('unknown', []):
        apply_theme(app, 'system')
        receive_preferences({'color_scheme': invalid})
        manager._last_colors = None
        apply_theme(app)
        assert current_scheme(app) == 'system'
    with pytest.raises(ValueError):
        set_color_scheme(app, 'invalid')
    assert not (tmp_path / 'app').exists()
    assert not path.exists()


def test_live_system_palette_events_and_explicit_reset(theme_app, qtbot):
    app = theme_app
    app.setStyleSheet('')
    app.setPalette(QPalette())
    platform = QPalette(app.palette())
    apply_theme(app, 'light')
    apply_theme(app, 'dark')
    assert app.palette().color(QPalette.ColorRole.Base).name() == '#1c2027'
    apply_theme(app, 'system')
    assert app.palette() == platform
    changed = []
    theme_signals(app).changed.connect(lambda: changed.append(colors()))
    # A completed Qt application-palette event is the same entry point used by
    # platform integration. Stylesheet repolish must not recurse or cache startup colors.
    latest = QPalette(platform)
    latest.setColor(QPalette.ColorRole.Highlight, QColor('#5432a8'))
    latest.setColor(QPalette.ColorRole.HighlightedText, QColor('#ffffff'))
    app.setPalette(latest)
    qtbot.waitUntil(lambda: bool(changed))
    assert changed[-1]['highlight'] == '#5432a8'
    assert '#5432a8' in app.styleSheet()
    count = len(changed)
    app.processEvents()
    assert len(changed) == count
    # QPalette() has no explicit role mask; a return to System releases custom roles.
    apply_theme(app, 'dark')
    apply_theme(app, 'system')
    assert app.palette() == platform


@pytest.mark.parametrize('scheme', ['light', 'dark'])
def test_semantic_contrast_and_native_selection_paints(theme_app, scheme):
    apply_theme(theme_app, scheme)
    c = colors()
    for bg, fg in (('base', 'text'), ('base', 'muted'), ('highlight', 'highlighted_text'),
                   ('positive_bg', 'positive_fg'), ('warning_bg', 'warning_fg'), ('negative_bg', 'negative_fg')):
        assert _contrast(c[bg], c[fg]) >= 4.5, (scheme, bg, fg)
    option = QStyleOptionViewItem()
    option.palette = theme_app.palette()
    option.font = theme_app.font()
    option.rect = QRect(0, 0, 420, 110)
    option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected
    selected = _row_colors(option)
    assert selected['text'] == selected['muted'] == c['highlighted_text']
    model = QStandardItemModel()
    item = QStandardItem('Active')
    item.setData(dict(title='Example title', body='Example body', created_at='now'), Qt.ItemDataRole.UserRole)
    model.appendRow(item)
    delegates = [NotificationDelegate(lambda _: '12:00'), ConversationDelegate(lambda _: '12:00'), PillDelegate({0})]
    for delegate in delegates:
        image = QImage(420, 110, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            delegate.paint(painter, option, model.index(0, 0))
        finally:
            painter.end()
        assert image.pixelColor(410, 5).name() == c['highlight']
        if isinstance(delegate, NotificationDelegate):
            # Inspect the actual type pill rather than just its enclosing row.
            assert image.pixelColor(18, 90).name() == c['highlight']
            assert any(image.pixelColor(x, y).name() == c['highlighted_text']
                       for x in range(22, 65) for y in range(83, 100))
    inactive = PillDelegate._colors('Inactive', theme_app.palette())
    assert inactive[:2] != (c['positive_bg'], c['positive_fg'])


def test_open_review_compose_and_sms_keep_user_state_on_switch(theme_app, qtbot):
    worker = Worker()
    review = ReviewDialog(worker, ACTION, DEVICES, {'send_ready': True})
    compose = ComposeDialog(worker, DEVICES)
    sms = SmsPage(worker)
    for widget in (review, compose, sms):
        qtbot.addWidget(widget)
        widget.show()
    compose.body.setPlainText('Unsaved fictional draft')
    sms.composer.setPlainText('Inline unsaved draft')
    row, bubble, layout = sms._card('smsOutgoing', True)
    body = MessageText('Selectable fictional phone history', bubble)
    layout.addWidget(body)
    sms.timeline_layout.insertWidget(0, row)
    cursor = review.preview.textCursor()
    cursor.setPosition(10)
    cursor.setPosition(40, QTextCursor.MoveMode.KeepAnchor)
    review.preview.setTextCursor(cursor)
    selected = cursor.selectedText()
    sms_cursor = body.textCursor()
    sms_cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    body.setTextCursor(sms_cursor)
    sms_selected = sms_cursor.selectedText()
    review.ack.setChecked(True)
    qtbot.wait(10)
    review.preview.verticalScrollBar().setValue(80)
    scroll = review.preview.verticalScrollBar().value()
    identities = (row, bubble, body)
    for scheme in ('light', 'dark', 'system'):
        set_color_scheme(theme_app, scheme)
        theme_app.processEvents()
        assert review.preview.textCursor().selectedText() == selected
        assert review.preview.verticalScrollBar().value() == scroll
        assert review.ack.isChecked() and review.approve_button.isEnabled()
        assert compose.body.toPlainText() == 'Unsaved fictional draft'
        assert sms.composer.toPlainText() == 'Inline unsaved draft'
        assert body.textCursor().selectedText() == sms_selected
        assert identities == (row, bubble, body)
        assert colors()['text'] in review.preview.toHtml()
    assert not worker.requests
    review.reject()
    compose.reject()


@pytest.mark.parametrize('scheme', ['system', 'light', 'dark'])
@pytest.mark.parametrize('point_size', [None, 15])
def test_pill_columns_resize_to_contents_and_render_full_labels(theme_app, qtbot, monkeypatch, scheme, point_size):
    apply_theme(theme_app, scheme)
    view = QTableView()
    qtbot.addWidget(view)
    if point_size is not None:
        font = QFont(view.font())
        font.setPointSize(point_size)
        view.setFont(font)
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(['Kind', 'Push', 'Features'])
    model.appendRow([QStandardItem('Sms'), QStandardItem('Supported'), QStandardItem('SMS, Push')])
    view.setModel(model)
    delegate = PillDelegate({0, 1}, multi_columns={2}, parent=view)
    view.setItemDelegate(delegate)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    view.verticalHeader().setDefaultSectionSize(48)
    view.resize(700, 180)
    view.show()
    theme_app.processEvents()
    view.resizeColumnsToContents()
    painted = []
    original = QPainter.drawText

    def record_text(painter, *args):
        if args and isinstance(args[-1], str):
            painted.append(args[-1])
        return original(painter, *args)

    monkeypatch.setattr(QPainter, 'drawText', record_text)
    rendered = QImage(view.viewport().size(), QImage.Format.Format_ARGB32)
    rendered.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rendered)
    try:
        view.viewport().render(painter)
    finally:
        painter.end()
    # These strings come from actual drawText calls during the table render,
    # covering both formerly invisible Kind and truncated capability pills.
    assert all(text in painted for text in ('Sms', 'Supported', 'SMS', 'Push')), painted
    assert not any('…' in text for text in painted), painted
    for column in range(3):
        assert view.columnWidth(column) <= view.viewport().width()
    # A manually constrained cell still elides instead of drawing past its edge.
    view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    view.setColumnWidth(1, 55)
    painted.clear()
    painter = QPainter(rendered)
    try:
        view.viewport().render(painter)
    finally:
        painter.end()
    assert any('…' in text for text in painted), painted


def test_stylesheet_gc_keeps_widget_snapshot_alive_then_releases(tmp_path):
    """Run the historical native crash out of process, with cyclic GC enabled."""
    import os
    import subprocess
    import sys
    import textwrap
    script = textwrap.dedent("""
        import gc
        import weakref
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication, QWidget
        from pbdesk.desktop.theme import apply_theme
        app = QApplication([])
        apply_theme(app, 'light')
        armed = False
        collected = False
        during = []
        class Trigger(QWidget):
            def event(self, event):
                global collected
                if armed and not collected and event.type() == QEvent.Type.StyleChange:
                    collected = True
                    gc.collect()
                    during.append(sum(reference() is not None for reference in references))
                return super().event(event)
        trigger = Trigger()
        windows = [QWidget() for _ in range(128)]
        for window in windows:
            window.cycle = window
        references = [weakref.ref(window) for window in windows]
        trigger.ensurePolished()
        for window in windows:
            window.ensurePolished()
        del window
        windows = None
        armed = True
        assert gc.isenabled()
        apply_theme(app, 'dark')
        assert during == [128], during
        assert gc.isenabled()
        gc.collect()
        assert all(reference() is None for reference in references)
        print('snapshot survived collection; wrappers released after repolish')
    """)
    env = dict(os.environ, PBDESK_APPLICATION_DIR=str(tmp_path / 'app'),
               PBDESK_STATE_DIR=str(tmp_path / 'state'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(tmp_path / 'no-dbus'))
    result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', script],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'wrappers released after repolish' in result.stdout
