"""A restrained native Widgets palette shared by windows and dialogs."""
from PyQt6.QtGui import QColor, QPalette

STYLE = """
QWidget { color: #24343b; font-size: 14px; }
QMainWindow, QDialog { background: #faf9f6; }
QLabel#pageTitle { font-size: 27px; font-weight: 700; color: #152a30; }
QLabel#subtitle, QLabel#muted { color: #657176; font-size: 13px; }
QLabel#statusPill { background: #e8f1ec; color: #1d4d40; border: 1px solid #cce0d6; border-radius: 13px; padding: 5px 12px; font-size: 12px; font-weight: 600; }
QLabel#statusPill[tone="warn"] { background: #fff3da; color: #785117; border-color: #e5c58b; }
QLabel#statusPill[tone="error"] { background: #fde8e6; color: #922b21; border-color: #e6b0aa; }
QLabel#badge { background: #eef2ef; color: #44575d; border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600; }
QLabel#badge[tone="jade"] { background: #dff1e9; color: #155244; }
QLabel#badge[tone="amber"] { background: #fdecc8; color: #7d500a; }
QLabel#badge[tone="danger"] { background: #fadbd8; color: #922b21; }
QFrame#card { background: #ffffff; border: 1px solid #e2e8e4; border-radius: 8px; }
QFrame#tintBox { background: #f7f8f5; border: 1px solid #e6eae4; border-radius: 6px; }
QFrame#divider { background: #e6eae4; border: none; }
QFrame#segmented { background: #eef2ef; border: 1px solid #d7dfda; border-radius: 16px; padding: 2px; }
QFrame#segmented QPushButton { background: transparent; border: none; border-radius: 13px; padding: 5px 16px; color: #4a5960; font-weight: 600; }
QFrame#segmented QPushButton:checked { background: #008b73; color: #ffffff; }
QWidget#sidebar { background: #20383e; }
QWidget#sidebar QLabel { color: #9cb5b4; font-size: 12px; }
QLabel#brand { font-size: 23px; font-weight: 700; padding: 14px 10px 20px; color: #ffffff; }
QWidget#sidebar QPushButton { background: transparent; border: none; color: #e4edec; text-align: left; padding: 11px 14px; border-radius: 7px; font-size: 14px; }
QWidget#sidebar QPushButton:checked { background: #008d76; color: white; font-weight: 600; }
QWidget#sidebar QPushButton:hover { background: #2d4c53; }
QWidget#sidebar QPushButton:checked:hover { background: #008d76; }
QWidget#sidebar QPushButton#sidebarSettings { border: 1px solid #3c5c63; text-align: center; padding: 9px 12px; color: #e4edec; }
QWidget#sidebar QPushButton#sidebarSettings:hover { background: #2d4c53; border-color: #557980; }
QPushButton { background: #ffffff; border: 1px solid #cfd8d3; border-radius: 6px; padding: 7px 14px; color: #24343b; }
QPushButton:hover { background: #edf4f0; border-color: #008d76; }
QPushButton:pressed { background: #d6e9e0; }
QPushButton:disabled { color: #8b9697; background: #eceeeb; border-color: #d9dfdb; }
QPushButton[primary="true"] { background: #008b73; border-color: #008b73; color: white; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #00765f; }
QPushButton[primary="true"]:disabled { color: #87928e; background: #e2e7e1; border-color: #d1d9d0; }
QPushButton[danger="true"] { color: #ad3939; border-color: #d5a3a3; }
QPushButton[danger="true"]:hover { background: #fdf2f2; border-color: #ad3939; }
QPushButton#inlineLink { background: transparent; border: none; padding: 2px 6px; color: #4a5960; font-size: 13px; }
QPushButton#inlineLink:hover { background: #edf4f0; color: #008b73; border-radius: 4px; }
QLineEdit, QComboBox, QTextEdit, QTextBrowser { background: #ffffff; border: 1px solid #d5ddd8; border-radius: 6px; padding: 7px 10px; selection-background-color: #cce8dd; selection-color: #142c2b; }
QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color: #008b73; }
QTextBrowser#plainDetail { border: none; background: transparent; padding: 0px; }
QListView { background: #ffffff; border: none; outline: 0; }
QTableView { background: #ffffff; alternate-background-color: #f8faf8; border: none; outline: 0; selection-background-color: #e3f2ec; selection-color: #153c35; gridline-color: transparent; }
QTableView::item { padding: 10px 10px; border-bottom: 1px solid #eceee9; }
QTableView::item:selected { background: #e3f2ec; color: #153c35; }
QHeaderView::section { background: #ffffff; color: #526167; padding: 10px 10px; border: none; border-bottom: 1px solid #e2e8e4; font-weight: 600; font-size: 12px; }
QTabWidget::pane { border: 0; }
QTabBar::tab { padding: 9px 19px; background: #eef2ed; border: 1px solid #d3ddd6; }
QTabBar::tab:selected { background: #008b73; color: white; }
QStatusBar { background: #f0f3ee; border-top: 1px solid #d9e0d9; padding: 4px 12px; font-size: 12px; color: #526167; }
QMenuBar, QMenu { background: #faf9f6; }
QMenu::item { padding: 8px 24px; }
QMenu::item:selected { background: #d5ebe1; }
QSplitter::handle { background: transparent; width: 10px; height: 10px; }
QLabel#errorBanner { background: #fff4df; color: #785117; padding: 10px 14px; border: 1px solid #e5c58b; border-radius: 6px; }
QLabel#infoBanner { background: #ebf5f0; color: #1b4d3e; padding: 10px 14px; border: 1px solid #c5dfd3; border-radius: 6px; }
QGroupBox { border: 1px solid #d7dfda; border-radius: 8px; margin-top: 16px; padding-top: 15px; background: #ffffff; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; font-weight: 600; }
QCheckBox { spacing: 8px; padding: 5px 0; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #80948a; border-radius: 3px; background: white; }
QCheckBox::indicator:checked { background: #008b73; border-color: #008b73; }
QCheckBox::indicator:disabled { background: #e8ece5; border-color: #c5d0c5; }
"""


def apply_theme(app):
    app.setStyle("Fusion")
    palette = QPalette()
    for role, value in ((QPalette.ColorRole.Window, '#faf9f6'), (QPalette.ColorRole.Base, '#ffffff'), (QPalette.ColorRole.Text, '#24343b'), (QPalette.ColorRole.WindowText, '#24343b'), (QPalette.ColorRole.Highlight, '#e3f2ec'), (QPalette.ColorRole.HighlightedText, '#153c35'), (QPalette.ColorRole.Button, '#ffffff'), (QPalette.ColorRole.ButtonText, '#24343b')):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


def primary(button):
    button.setProperty('primary', True)
    return button
