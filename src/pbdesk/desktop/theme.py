"""Local color preferences over the installed Qt style and platform palette.

System leaves font/style integration to Qt (including KDE integration when present).
Only semantic containers are styled; controls keep their native interaction states.
"""
from PyQt6.QtCore import QEvent, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from .preferences import read_preferences, update_preferences

SCHEMES = ('system', 'light', 'dark')


def _mix(first, second, amount):
    """Blend two Qt colors linearly toward second by amount and return a hex string.

    :param first: Starting color value accepted by ``QColor``.
    :type first: QColor | str
    :param second: Target color value accepted by ``QColor``.
    :type second: QColor | str
    :param amount: Interpolation factor between 0.0 (first) and 1.0 (second).
    :type amount: float
    :return: Blended RGB hex color string.
    :rtype: str
    """
    a, b = QColor(first), QColor(second)
    return QColor(*(round(a.getRgb()[i] * (1 - amount) + b.getRgb()[i] * amount)
                    for i in range(3))).name()


def colors(palette=None):
    """Semantic paints from the effective palette, also usable by rich text.

    :param palette: Optional palette to sample; defaults to the application palette.
    :type palette: QPalette | None
    :return: Mapping of semantic color token names to hex color strings.
    :rtype: dict[str, str]
    """
    palette = palette if palette is not None else QApplication.palette()
    role = QPalette.ColorRole
    value = lambda key: palette.color(key).name()
    base, text = value(role.Base), value(role.Text)
    dark = QColor(base).lightnessF() < .5
    values = dict(window=value(role.Window), base=base, alternate=value(role.AlternateBase),
                  text=text, muted=_mix(text, base, .30), border=_mix(text, base, .78),
                  highlight=value(role.Highlight), highlighted_text=value(role.HighlightedText),
                  link=value(role.Link))
    # Status foregrounds have readable contrast against their tinted surfaces.
    for tone, light_fg, dark_fg in (('positive', '#186044', '#9be0bd'),
                                    ('warning', '#78500b', '#f4d18a'),
                                    ('negative', '#a12e35', '#ffb3ba')):
        foreground = dark_fg if dark else light_fg
        values[tone + '_fg'] = foreground
        values[tone + '_bg'] = _mix(base, foreground, .13 if dark else .10)
    return values


def _palette(scheme):
    """Build a light or dark QPalette with active and disabled color roles.

    :param scheme: Explicit scheme name (``'light'`` or ``'dark'``).
    :type scheme: str
    :return: Configured Qt palette for the requested color scheme.
    :rtype: QPalette
    """
    palette = QPalette()
    role = QPalette.ColorRole
    dark = scheme == 'dark'
    values = {
        role.Window: '#242830' if dark else '#f4f5f7',
        role.WindowText: '#edf0f5' if dark else '#20252e',
        role.Base: '#1c2027' if dark else '#ffffff',
        role.AlternateBase: '#282e38' if dark else '#eef1f5',
        role.Text: '#edf0f5' if dark else '#20252e',
        role.Button: '#303641' if dark else '#eef1f5',
        role.ButtonText: '#edf0f5' if dark else '#20252e',
        role.Highlight: '#9bbdff' if dark else '#245eb5',
        role.HighlightedText: '#102441' if dark else '#ffffff',
        role.Link: '#a4c8ff' if dark else '#1957ab',
        role.LinkVisited: '#d5b8ff' if dark else '#6d3ba2',
        role.ToolTipBase: '#303641' if dark else '#ffffff',
        role.ToolTipText: '#edf0f5' if dark else '#20252e',
        role.Light: '#515c6c' if dark else '#ffffff',
        role.Midlight: '#3c4552' if dark else '#e8ecf2',
        role.Mid: '#596271' if dark else '#bbc2ce',
        role.Dark: '#14171c' if dark else '#7e8998',
        role.Shadow: '#0d1015' if dark else '#586271',
        role.BrightText: '#ffffff' if dark else '#ffffff',
        role.Accent: '#9bbdff' if dark else '#245eb5',
        role.PlaceholderText: '#a3aebb' if dark else '#5b6676',
    }
    for key, value in values.items():
        palette.setColor(key, QColor(value))
    for key in (role.Text, role.WindowText, role.ButtonText, role.PlaceholderText):
        palette.setColor(QPalette.ColorGroup.Disabled, key,
                         QColor('#919ba9' if dark else '#707a88'))
    palette.setColor(QPalette.ColorGroup.Disabled, role.Highlight,
                     QColor('#414c5f' if dark else '#d6dfed'))
    palette.setColor(QPalette.ColorGroup.Disabled, role.HighlightedText,
                     QColor('#b0bac8' if dark else '#566276'))
    return palette


def _stylesheet(c):
    """Generate the semantic container stylesheet from resolved color tokens.

    :param c: Mapping of semantic color role names to hex color strings.
    :type c: collections.abc.Mapping[str, str]
    :return: Qt stylesheet text targeting semantic container and pill object names.
    :rtype: str
    """
    # Avoid broad QWidget, input, checkbox, menu, scrollbar or table overrides:
    # these would replace native disabled, focus, selection and platform states.
    return f"""
QLabel#pageTitle {{ font-size: 22pt; font-weight: 700; }}
QLabel#subtitle, QLabel#muted, QLabel#smsMeta {{ color: {c['muted']}; }}
QLabel#brand {{ font-size: 19pt; font-weight: 700; padding: 14px 10px 20px; }}
QWidget#sidebar {{ background: {c['alternate']}; }}
QWidget#sidebar QPushButton {{ text-align: left; padding: 11px 14px; }}
QWidget#sidebar QPushButton:checked {{ background: {c['highlight']}; color: {c['highlighted_text']}; font-weight: 600; }}
QWidget#sidebar QPushButton#sidebarSettings {{ text-align: center; }}
QPushButton {{ padding: 6px 12px; }}
QPushButton[primary="true"]:enabled {{ background: {c['highlight']}; color: {c['highlighted_text']}; border: 1px solid {c['highlight']}; border-radius: 5px; font-weight: 600; }}
QPushButton[primary="true"]:enabled:focus {{ border: 2px solid {c['text']}; }}
QPushButton[danger="true"]:enabled {{ color: {c['negative_fg']}; }}
QPushButton#inlineLink {{ padding: 2px 6px; }}
QFrame#card {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QFrame#tintBox {{ background: {c['alternate']}; border: 1px solid {c['border']}; border-radius: 6px; }}
QFrame#divider {{ background: {c['border']}; border: none; }}
QLabel#badge {{ background: {c['alternate']}; color: {c['text']}; border-radius: 9px; padding: 3px 9px; font-weight: 600; }}
QLabel#statusPill, QLabel#badge[tone="jade"], QLabel#infoBanner {{ background: {c['positive_bg']}; color: {c['positive_fg']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 10px; }}
QLabel#statusPill[tone="warn"], QLabel#badge[tone="amber"], QLabel#errorBanner {{ background: {c['warning_bg']}; color: {c['warning_fg']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 10px; }}
QLabel#statusPill[tone="error"], QLabel#badge[tone="danger"] {{ background: {c['negative_bg']}; color: {c['negative_fg']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 10px; }}
QTextBrowser#plainDetail {{ border: none; background: transparent; padding: 0px; }}
QSplitter::handle {{ background: transparent; width: 10px; height: 10px; }}
QScrollArea#smsTimelineScroll {{ background: {c['alternate']}; border: none; }}
QWidget#smsTimeline {{ background: {c['alternate']}; }}
QFrame#smsIncoming {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame#smsOutgoing {{ background: {c['positive_bg']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame#smsLocal {{ background: {c['warning_bg']}; border: 1px dashed {c['border']}; border-radius: 8px; }}
QLabel#smsDate {{ color: {c['muted']}; background: {c['base']}; border-radius: 10px; padding: 3px 12px; font-weight: 600; }}
QLabel#smsHeading {{ font-size: 15pt; font-weight: 700; }}
"""


class _Theme(QObject):
    """Application event filter and signal emitter coordinating theme palette changes."""
    changed = pyqtSignal()

    def __init__(self, app):
        """Initialize theme state and watch application palette changes.

        :param app: Qt application instance whose palette and stylesheet are managed.
        :type app: QApplication
        """
        super().__init__(app)
        self.app = app
        self.scheme = 'system'
        self._updating = False
        self._scheduled = False
        self._last_colors = None
        app.installEventFilter(self)

    def eventFilter(self, watched, event):
        """Schedule a deferred stylesheet refresh on system application palette changes.

        :param watched: Qt object receiving the intercepted event.
        :type watched: QObject
        :param event: Incoming Qt event being inspected.
        :type event: QEvent
        :return: Always False so the event continues normal Qt propagation.
        :rtype: bool
        """
        if (watched is self.app and event.type() == QEvent.Type.ApplicationPaletteChange
                and not self._updating and not self._scheduled):
            self._scheduled = True
            # Qt updates the palette after colorSchemeChanged. Read it once the
            # ApplicationPaletteChange has finished, never from styleHints.
            QTimer.singleShot(0, self._palette_changed)
        return False

    def _palette_changed(self):
        """Refresh stylesheets when in system mode and effective palette colors changed."""
        self._scheduled = False
        if self.scheme == 'system' and colors(self.app.palette()) != self._last_colors:
            self._refresh()

    def _refresh(self):
        """Reapply the stylesheet from current palette colors and notify listeners."""
        # Qt repolishes a raw widget-pointer snapshot. A Python StyleChange
        # callback can run cyclic GC and otherwise destroy a later widget in
        # that snapshot. Keep its wrappers alive until synchronous repolish
        # and theme callbacks finish; normal collection resumes on return.
        retained_widgets = self.app.allWidgets()
        self._updating = True
        try:
            self._last_colors = colors(self.app.palette())
            self.app.setStyleSheet(_stylesheet(self._last_colors))
        finally:
            self._updating = False
        self.changed.emit()
        del retained_widgets

    def apply(self, scheme):
        """Apply the given color scheme palette to the QApplication and refresh.

        :param scheme: Target scheme name (``'system'``, ``'light'``, or ``'dark'``).
        :type scheme: str
        """
        previous = self.scheme
        self.scheme = scheme
        self._updating = True
        try:
            if scheme != 'system':
                self.app.setPalette(_palette(scheme))
            elif previous != 'system':
                # A zero-resolve palette releases our explicit roles and resolves
                # against Qt's *current* platform palette, not a startup snapshot.
                self.app.setPalette(QPalette())
        finally:
            self._updating = False
        self._refresh()


def _load_scheme():
    """Return the persisted color scheme preference or 'system' by default.

    :return: Validated color scheme name from local UI preferences.
    :rtype: str
    """
    scheme = read_preferences().get('color_scheme')
    return scheme if scheme in SCHEMES else 'system'


def theme_signals(app):
    """Return the application-owned notifier; accessing it does not apply a theme.

    :param app: Qt application instance owning the theme controller.
    :type app: QApplication
    :return: Theme manager instance attached to ``app``.
    :rtype: _Theme
    """
    if not hasattr(app, '_pbdesk_theme'):
        app._pbdesk_theme = _Theme(app)
    return app._pbdesk_theme


def apply_theme(app, scheme=None):
    """Initialize once from local preference, or apply an explicit scheme.

    :param app: Qt application instance to style.
    :type app: QApplication
    :param scheme: Optional explicit scheme name; uses persisted preference when None.
    :type scheme: str | None
    :raises ValueError: If ``scheme`` is not one of the supported color schemes.
    """
    manager = theme_signals(app)
    if scheme is None and manager._last_colors is not None:
        return
    scheme = _load_scheme() if scheme is None else scheme
    if scheme not in SCHEMES:
        raise ValueError('Unknown color scheme: ' + str(scheme))
    manager.apply(scheme)


def current_scheme(app):
    """Return the currently active color scheme name for the application.

    :param app: Qt application instance owning the theme controller.
    :type app: QApplication
    :return: Active scheme name (``'system'``, ``'light'``, or ``'dark'``).
    :rtype: str
    """
    return theme_signals(app).scheme


def set_color_scheme(app, scheme):
    """Apply and atomically persist this local UI preference only.

    :param app: Qt application instance to update.
    :type app: QApplication
    :param scheme: Target scheme name to persist and apply.
    :type scheme: str
    :raises ValueError: If ``scheme`` is not one of the supported color schemes.
    """
    if scheme not in SCHEMES:
        raise ValueError('Unknown color scheme: ' + str(scheme))
    update_preferences({'color_scheme': scheme})
    apply_theme(app, scheme)


def primary(button):
    """Mark a button as primary via Qt dynamic property and return it.

    :param button: Push button widget to mark with ``primary=True``.
    :type button: PyQt6.QtWidgets.QPushButton
    :return: The same push button instance after applying the dynamic property.
    :rtype: PyQt6.QtWidgets.QPushButton
    """
    button.setProperty('primary', True)
    return button
