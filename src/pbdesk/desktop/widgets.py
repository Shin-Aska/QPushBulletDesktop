"""Shared PyQt6 icons, card containers, and custom item delegates for pbdesk."""
from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QFrame, QStyle, QStyleOptionFocusRect, QStyledItemDelegate

from .theme import colors


def make_icon(kind, color=None, size=18):
    """Draw crisp geometric vector icons via QPainter with no external assets."""
    color = color or colors()["text"]
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    s = size / 18.0
    if kind == 'notifications':
        path = QPainterPath()
        path.moveTo(9 * s, 2.5 * s)
        path.cubicTo(6.2 * s, 2.5 * s, 4.5 * s, 4.5 * s, 4.5 * s, 7.5 * s)
        path.lineTo(4.5 * s, 11.5 * s)
        path.lineTo(3 * s, 13.5 * s)
        path.lineTo(15 * s, 13.5 * s)
        path.lineTo(13.5 * s, 11.5 * s)
        path.lineTo(13.5 * s, 7.5 * s)
        path.cubicTo(13.5 * s, 4.5 * s, 11.8 * s, 2.5 * s, 9 * s, 2.5 * s)
        painter.drawPath(path)
        painter.drawArc(QRectF(7.2 * s, 13.5 * s, 3.6 * s, 2.5 * s), 0, -180 * 16)
    elif kind == 'sms':
        path = QPainterPath()
        path.addRoundedRect(QRectF(2.5 * s, 3 * s, 13 * s, 9.5 * s), 2.2 * s, 2.2 * s)
        painter.drawPath(path)
        painter.drawLine(int(5.5 * s), int(6.5 * s), int(12.5 * s), int(6.5 * s))
        painter.drawLine(int(5.5 * s), int(9.2 * s), int(10 * s), int(9.2 * s))
        painter.drawLine(int(5.5 * s), int(12.5 * s), int(4 * s), int(15.2 * s))
        painter.drawLine(int(4 * s), int(15.2 * s), int(8.2 * s), int(12.5 * s))
    elif kind == 'devices':
        painter.drawRoundedRect(QRectF(5 * s, 2 * s, 8 * s, 14 * s), 1.8 * s, 1.8 * s)
        painter.drawLine(int(8 * s), int(13.5 * s), int(10 * s), int(13.5 * s))
    elif kind == 'settings':
        painter.drawEllipse(QRectF(6.5 * s, 6.5 * s, 5 * s, 5 * s))
        for angle in range(0, 360, 45):
            painter.save()
            painter.translate(9 * s, 9 * s)
            painter.rotate(angle)
            painter.drawLine(0, int(-6.5 * s), 0, int(-4.8 * s))
            painter.restore()
    elif kind == 'refresh':
        painter.drawArc(QRectF(3.5 * s, 3.5 * s, 11 * s, 11 * s), 35 * 16, 290 * 16)
        painter.drawLine(int(13.5 * s), int(3.5 * s), int(14 * s), int(7 * s))
        painter.drawLine(int(14 * s), int(7 * s), int(10.6 * s), int(6.6 * s))
    elif kind == 'brand':
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(2.5 * s, 8.8 * s)
        path.lineTo(15.5 * s, 3 * s)
        path.lineTo(12.2 * s, 15 * s)
        path.lineTo(8.5 * s, 10.6 * s)
        path.closeSubpath()
        painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def card_frame(name='card'):
    frame = QFrame()
    frame.setObjectName(name)
    frame.setFrameShape(QFrame.Shape.NoFrame)
    return frame


def divider_line():
    line = QFrame()
    line.setObjectName('divider')
    line.setFixedHeight(1)
    return line


def _small_font(source):
    font = QFont(source)
    if font.pointSizeF() > 0:
        font.setPointSizeF(max(8, font.pointSizeF() - 1))
    elif font.pixelSize() > 0:
        font.setPixelSize(max(10, font.pixelSize() - 1))
    return font


def _pill_font(source):
    font = _small_font(source)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def _draw_pill(painter, rect, text, bg, fg, border=None):
    font = _pill_font(painter.font())
    painter.save()
    painter.setFont(font)
    fm = QFontMetrics(font)
    width = fm.horizontalAdvance(text) + 16
    pill_rect = QRectF(rect.x(), rect.y(), min(width, rect.width()), rect.height())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(bg))
    painter.setPen(QPen(QColor(border), 1) if border else Qt.PenStyle.NoPen)
    radius = pill_rect.height() / 2.0
    painter.drawRoundedRect(pill_rect, radius, radius)
    painter.setPen(QColor(fg))
    painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, fm.elidedText(text, Qt.TextElideMode.ElideRight, int(pill_rect.width() - 8)))
    painter.restore()
    return pill_rect.width()


def _row_colors(option):
    palette = QPalette(option.palette)
    if not option.state & QStyle.StateFlag.State_Enabled:
        palette.setCurrentColorGroup(QPalette.ColorGroup.Disabled)
    c = colors(palette)
    if option.state & QStyle.StateFlag.State_Selected:
        c['text'] = c['muted'] = c['highlighted_text']
    return c


def _draw_row(painter, option, c):
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
    painter.fillRect(option.rect, QColor(c['highlight'] if selected else c['alternate']
                     if hovered or option.features & option.ViewItemFeature.Alternate else c['base']))
    painter.setPen(QColor(c['border']))
    painter.drawLine(option.rect.left() + 12, option.rect.bottom(), option.rect.right() - 12, option.rect.bottom())
    if option.state & QStyle.StateFlag.State_HasFocus:
        focus = QStyleOptionFocusRect()
        focus.rect = option.rect.adjusted(2, 2, -2, -2)
        focus.state = option.state
        focus.palette = option.palette
        focus.backgroundColor = QColor(c['highlight'] if selected else c['base'])
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, focus, painter, option.widget)


class NotificationDelegate(QStyledItemDelegate):
    """Paints notification list rows with bold title, timestamp, 2-line preview, and type pill."""

    def __init__(self, stamp_fn, parent=None):
        super().__init__(parent)
        self._stamp = stamp_fn

    def sizeHint(self, option, index):
        return QSize(max(option.rect.width(), 240), 104)

    def paint(self, painter, option, index):
        item = index.data(Qt.ItemDataRole.UserRole)
        if not item:
            super().paint(painter, option, index)
            return
        painter.save()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        c = _row_colors(option)
        _draw_row(painter, option, c)

        pad_x, pad_y = 14, 11
        inner_w = rect.width() - pad_x * 2
        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold)
        meta_font = _small_font(option.font)
        body_font = QFont(option.font)

        time_text = self._stamp(item.get('created_at'))
        if ',' in time_text:
            time_text = time_text.split(',', 1)[1].strip()
        painter.setFont(meta_font)
        time_fm = QFontMetrics(meta_font)
        time_w = time_fm.horizontalAdvance(time_text) + 8
        painter.setPen(QColor(c['muted']))
        painter.drawText(QRect(rect.right() - pad_x - time_w, rect.top() + pad_y, time_w, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, time_text)

        title = item.get('title') or '(Untitled)'
        painter.setFont(title_font)
        title_fm = QFontMetrics(title_font)
        painter.setPen(QColor(c['text']))
        title_elided = title_fm.elidedText(title, Qt.TextElideMode.ElideRight, max(40, inner_w - time_w - 6))
        painter.drawText(QRect(rect.left() + pad_x, rect.top() + pad_y, max(40, inner_w - time_w - 6), 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_elided)

        body = ' '.join((item.get('body') or item.get('url') or '').split())
        painter.setFont(body_font)
        painter.setPen(QColor(c['muted']))
        body_rect = QRect(rect.left() + pad_x, rect.top() + pad_y + 23, inner_w, 36)
        painter.drawText(body_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, body)

        kind = (item.get('type') or 'note').replace('_', ' ').title()
        bg, fg = ((c['highlight'], c['highlighted_text']) if selected
                  else (c['alternate'], c['text']))
        _draw_pill(painter, QRectF(rect.left() + pad_x, rect.bottom() - 28, 120, 20), kind, bg, fg)
        painter.restore()


class ConversationDelegate(QStyledItemDelegate):
    """Paints SMS conversation rows with bold contact name, right-aligned time, and 2-line preview."""

    def __init__(self, time_fn, parent=None):
        super().__init__(parent)
        self._time = time_fn

    def sizeHint(self, option, index):
        return QSize(max(option.rect.width(), 220), 82)

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole + 1)
        if not isinstance(row, dict):
            row = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, dict):
            super().paint(painter, option, index)
            return
        painter.save()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        c = _row_colors(option)
        _draw_row(painter, option, c)

        pad_x, pad_y = 14, 12
        inner_w = rect.width() - pad_x * 2
        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold)
        meta_font = _small_font(option.font)
        body_font = QFont(option.font)

        raw_time = self._time(row.get('last_timestamp'))
        time_text = raw_time.strftime('%H:%M') if hasattr(raw_time, 'strftime') else str(raw_time or '')
        painter.setFont(meta_font)
        time_fm = QFontMetrics(meta_font)
        time_w = time_fm.horizontalAdvance(time_text) + 8
        painter.setPen(QColor(c['muted']))
        painter.drawText(QRect(rect.right() - pad_x - time_w, rect.top() + pad_y, time_w, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, time_text)

        title = row.get('title') or row.get('id') or 'Conversation'
        painter.setFont(title_font)
        title_fm = QFontMetrics(title_font)
        painter.setPen(QColor(c['text']))
        title_elided = title_fm.elidedText(title, Qt.TextElideMode.ElideRight, max(40, inner_w - time_w - 6))
        painter.drawText(QRect(rect.left() + pad_x, rect.top() + pad_y, max(40, inner_w - time_w - 6), 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_elided)

        preview = ' '.join((row.get('preview') or '').split())
        painter.setFont(body_font)
        painter.setPen(QColor(c['muted']))
        painter.drawText(QRect(rect.left() + pad_x, rect.top() + pad_y + 24, inner_w, 36), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, preview)
        painter.restore()


class PillDelegate(QStyledItemDelegate):
    """Paints status, kind, or capability pills inside QTableView cells."""

    def __init__(self, pill_columns, multi_columns=(), parent=None):
        super().__init__(parent)
        self._pill_columns = set(pill_columns)
        self._multi_columns = set(multi_columns)

    def sizeHint(self, option, index):
        native = super().sizeHint(option, index)
        column = index.column()
        if column not in self._pill_columns and column not in self._multi_columns:
            return native
        raw = str(index.data(Qt.ItemDataRole.DisplayRole) or '').strip()
        if not raw or raw == '—':
            return native
        tokens = [part.strip() for part in raw.split(',')] if column in self._multi_columns else [raw]
        tokens = [token for token in tokens if token]
        if not tokens:
            return native
        metrics = QFontMetrics(_pill_font(option.font))
        # Match paint's 8px left inset, 6px right inset, and pill text padding.
        # QRect.right() is inclusive, hence the extra pixel for available width.
        width = 8 + sum(max(28, metrics.horizontalAdvance(token) + 16) for token in tokens)
        width += 6 * (len(tokens) - 1) + 6 + 1
        return QSize(max(native.width(), width), max(native.height(), 22))

    @staticmethod
    def _colors(text, palette=None):
        c = colors(palette)
        lower = text.casefold()
        if any(k in lower for k in ('inactive', 'not active', 'unavailable', 'not ready', 'not verified', 'unverified', 'unsupported', 'not supported')):
            return c['alternate'], c['muted'], None
        if any(k in lower for k in ('failed', 'expired', 'error', 'locked')):
            return c['negative_bg'], c['negative_fg'], None
        if any(k in lower for k in ('pending', 'uncertain', 'queued', 'submitting')):
            return c['warning_bg'], c['warning_fg'], None
        if any(k in lower for k in ('active', 'accepted', 'supported', 'ready', 'verified')):
            return c['positive_bg'], c['positive_fg'], None
        return c['alternate'], c['text'], c['border']

    def paint(self, painter, option, index):
        col = index.column()
        if col not in self._pill_columns and col not in self._multi_columns:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setFont(option.font)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        c = _row_colors(option)
        _draw_row(painter, option, c)

        raw = str(index.data(Qt.ItemDataRole.DisplayRole) or '').strip()
        if not raw or raw == '—':
            painter.setPen(QColor(c['muted']))
            painter.drawText(option.rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, raw)
            painter.restore()
            return

        tokens = [t.strip() for t in raw.split(',')] if col in self._multi_columns else [raw]
        x = option.rect.left() + 8
        y = option.rect.top() + (option.rect.height() - 22) // 2
        for token in tokens:
            if not token:
                continue
            bg, fg, border = self._colors(token, option.palette)
            if selected:
                bg, fg, border = c['highlight'], c['highlighted_text'], c['highlighted_text']
            avail = option.rect.right() - x - 6
            if avail < 28:
                break
            w = _draw_pill(painter, QRectF(x, y, avail, 22), token, bg, fg, border)
            x += w + 6
        painter.restore()
