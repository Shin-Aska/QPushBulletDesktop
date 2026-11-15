"""Bounded native raster previews. Remote images load only after an explicit click."""
import base64
import binascii
import hashlib
from collections import OrderedDict
import uuid

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QImageReader, QPainter, QPixmap
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget, QSizePolicy
from pbdesk.media_errors import IMAGE_ERRORS

from .theme import colors
from .widgets import make_icon

MAX_INLINE_CHARS = 32768
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
RASTER_FORMATS = {b'jpeg', b'jpg', b'png', b'webp', b'gif', b'bmp'}


def decode_image(data, extent=1024, max_dimension=8192, max_pixels=MAX_PIXELS):
    """Inspect dimensions and format before allocating a scaled native raster."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES:
        return None
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    reader.setAllocationLimit(64)
    if bytes(reader.format()).lower() not in RASTER_FORMATS:
        return None
    size = reader.size()
    if (not size.isValid() or size.width() > max_dimension or size.height() > max_dimension or
            size.width() * size.height() > max_pixels):
        return None
    if max(size.width(), size.height()) > extent:
        reader.setScaledSize(size.scaled(extent, extent, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    return image if not image.isNull() else None


def inline_image(value, extent=64):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_INLINE_CHARS:
        return None
    # Historical contact thumbnails may include a raster data URI prefix.
    if value.startswith('data:'):
        prefix, separator, value = value.partition(',')
        if not separator or prefix.lower() not in {'data:image/jpeg;base64', 'data:image/png;base64', 'data:image/webp;base64'}:
            return None
    try:
        return decode_image(base64.b64decode(value, validate=True), extent, max_dimension=1024, max_pixels=1024 * 1024)
    except (ValueError, binascii.Error):
        return None


def attachment_revision(url):
    """Invalidate an in-memory preview when its retained reference changes."""
    return hashlib.sha256(str(url).encode()).hexdigest()[:20]


def media_icon(item, size=32):
    image = inline_image(item.get('icon'), size)
    return QIcon(QPixmap.fromImage(image)) if image is not None else make_icon('notifications', size=size)


def contact_icon(recipients, title='', size=36):
    recipients = recipients if isinstance(recipients, list) else []
    if len(recipients) == 1 and isinstance(recipients[0], dict):
        image = inline_image(recipients[0].get('thumbnail'), size)
        if image is not None:
            return QIcon(QPixmap.fromImage(image))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors()['alternate']))
    painter.drawEllipse(0, 0, size, size)
    painter.setPen(QColor(colors()['text']))
    font = QFont()
    font.setPixelSize(max(10, size // 3))
    font.setBold(True)
    painter.setFont(font)
    words = str(title).split()
    initials = ''.join(word[0] for word in words[:2]).upper() if words else '?'
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, '•••' if len(recipients) > 1 else initials)
    painter.end()
    return QIcon(pixmap)


class RemoteImage(QWidget):
    """One retained attachment. The core resolves its URL inside the active account."""
    image_loaded = pyqtSignal(object)
    geometry_changing = pyqtSignal()
    geometry_changed = pyqtSignal()

    def __init__(self, worker, reference, parent=None, valid=None):
        super().__init__(parent)
        self.worker = worker
        self.reference = dict(reference)
        self.valid = valid or (lambda: True)
        self.request_id = None
        self.image = None
        self.preview_bounds = QSize(420, 280)
        self._dialog = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        self.preview = QPushButton()
        self.preview.setAccessibleName('Open loaded image in a larger view')
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.preview.setMinimumWidth(0)
        self.preview.hide()
        self.preview.clicked.connect(self.enlarge)
        layout.addWidget(self.preview)
        self.status = QLabel('Image attachment · load to preview')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.load_button = QPushButton('Load image')
        self.load_button.setAccessibleName('Load this image attachment')
        self.load_button.clicked.connect(self.load)
        layout.addWidget(self.load_button)
        worker.result.connect(self._result)
        # Each account gets a bounded memory-only cache. Changing accounts clears it.
        if not hasattr(worker, '_media_cache'):
            worker._media_cache = OrderedDict()
            worker._media_cache_account = self.reference.get('account_id')
            if hasattr(worker, 'snapshot'):
                worker.snapshot.connect(lambda snapshot: _cache_snapshot(worker, snapshot))
        if worker._media_cache_account != self.reference.get('account_id'):
            worker._media_cache.clear()
            worker._media_cache_account = self.reference.get('account_id')
        cached = worker._media_cache.get(self._cache_key())
        if cached is not None:
            self._show_image(cached)
        if not self.reference:
            self.hide()

    def set_reference(self, reference):
        """Replace a notification attachment and invalidate any previous response."""
        updated = dict(reference or {})
        if updated == self.reference:
            self.setVisible(bool(updated))
            return
        self.request_id = None
        self.reference = updated
        self.image = None
        self.preview.hide()
        self.preview.setIcon(QIcon())
        self.load_button.show()
        self.load_button.setEnabled(True)
        self.load_button.setText('Load image')
        self.status.setText('Image attachment · load to preview')
        if self._dialog:
            self._dialog.close()
        self.setVisible(bool(updated))
        if updated:
            if self.worker._media_cache_account != updated.get('account_id'):
                self.worker._media_cache.clear()
                self.worker._media_cache_account = updated.get('account_id')
            cached = self.worker._media_cache.get(self._cache_key())
            if cached is not None:
                self._show_image(cached)

    def set_preview_bounds(self, width, height):
        self.preview_bounds = QSize(max(48, min(1024, width)), max(48, min(720, height)))
        if self.image is not None:
            self._show_image(self.image)

    def _cache_key(self):
        return tuple(sorted(self.reference.items()))

    def load(self):
        if self.request_id or not self.valid():
            return
        self.request_id = str(uuid.uuid4())
        self.status.setText('Loading image…')
        self.load_button.setEnabled(False)
        self.worker.request('media_image', {**self.reference, 'request_id': self.request_id})

    def _result(self, result):
        if (result.get('command') != 'media_image' or result.get('request_id') != self.request_id or
                result.get('account_id') != self.reference.get('account_id') or not self.valid()):
            return
        self.request_id = None
        self.load_button.setEnabled(True)
        data = result.get('data')
        image = None
        if isinstance(data, dict) and data.get('account_id') == self.reference.get('account_id'):
            decoded = data.get('image')
            if isinstance(decoded, QImage) and not decoded.isNull() and decoded.sizeInBytes() <= 4 * 1024 * 1024:
                image = decoded
            encoded = data.get('data')
            if image is None and isinstance(encoded, str) and len(encoded) <= ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                try:
                    image = decode_image(base64.b64decode(encoded, validate=True))
                except (ValueError, binascii.Error):
                    pass
        if image is None:
            code = result.get('error_code')
            message = IMAGE_ERRORS.get(code, IMAGE_ERRORS['unknown']) if isinstance(code, str) else IMAGE_ERRORS['unknown']
            self.status.setText(message)
            self.load_button.setText('Retry image')
            return
        cache = self.worker._media_cache
        cache[self._cache_key()] = image
        cache.move_to_end(self._cache_key())
        while len(cache) > 8 or sum(entry.sizeInBytes() for entry in cache.values()) > 16 * 1024 * 1024:
            cache.popitem(last=False)
        self._show_image(image)

    def _show_image(self, image):
        self.geometry_changing.emit()
        self.image = image
        pixmap = QPixmap.fromImage(image).scaled(max(80, min(self.preview_bounds.width(), self.width() - 12)), self.preview_bounds.height(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview.setIcon(QIcon(pixmap))
        self.preview.setIconSize(pixmap.size())
        self.preview.setMinimumSize(QSize(0, 0))
        self.preview.setFixedHeight(pixmap.height() + 12)
        self.preview.show()
        self.status.setText('Image attachment · click preview to enlarge')
        self.load_button.hide()
        self.geometry_changed.emit()
        self.image_loaded.emit(image)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.image is not None:
            pixmap = QPixmap.fromImage(self.image).scaled(max(80, min(self.preview_bounds.width(), event.size().width() - 12)), self.preview_bounds.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.preview.setIcon(QIcon(pixmap))
            self.preview.setIconSize(pixmap.size())
            self.preview.setFixedHeight(pixmap.height() + 12)

    def enlarge(self):
        if self.image is None or not self.valid():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Image attachment')
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setPixmap(QPixmap.fromImage(self.image).scaled(960, 720, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(label)
        close = QPushButton('Close')
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        self._dialog = dialog
        dialog.show()


def _cache_snapshot(worker, snapshot):
    capabilities = snapshot.get('capabilities')
    if isinstance(capabilities, dict) and 'account_id' in capabilities and capabilities['account_id'] != worker._media_cache_account:
        worker._media_cache.clear()
        worker._media_cache_account = capabilities['account_id']
