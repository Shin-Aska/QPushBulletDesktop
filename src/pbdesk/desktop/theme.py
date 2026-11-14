"""A restrained native Widgets palette shared by windows and dialogs."""
from PyQt6.QtGui import QColor, QPalette

STYLE = """
QWidget { color: #24343b; font-size: 14px; }
QMainWindow, QDialog { background: #faf9f6; }
QLabel#pageTitle { font-size: 27px; font-weight: 650; color: #152a30; }
QLabel#subtitle, QLabel#muted { color: #657176; }
QWidget#sidebar { background: #20383e; }
QWidget#sidebar QLabel { color: #dae8e7; }
QLabel#brand { font-size: 25px; font-weight: 700; padding: 18px 12px 24px; color: #ffffff; }
QWidget#sidebar QPushButton { background: transparent; border: none; color: #e4edec; text-align: left; padding: 13px 18px; border-radius: 5px; }
QWidget#sidebar QPushButton:checked { background: #008d76; color: white; font-weight: 600; }
QWidget#sidebar QPushButton:hover { background: #315056; }
QPushButton { background: #ffffff; border: 1px solid #b9c4c3; border-radius: 4px; padding: 8px 13px; }
QPushButton:hover { background: #edf4f0; border-color: #008d76; }
QPushButton:pressed { background: #d6e9e0; }
QPushButton:disabled { color: #8b9697; background: #eceeeb; border-color: #d9dfdb; }
QPushButton[primary="true"] { background: #008b73; border-color: #008b73; color: white; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #00765f; }
QPushButton[primary="true"]:disabled { color: #87928e; background: #e2e7e1; border-color: #d1d9d0; }
QPushButton[danger="true"] { color: #ad3939; border-color: #c99393; }
QLineEdit, QComboBox, QTextEdit, QTextBrowser { background: #ffffff; border: 1px solid #d0d8d3; border-radius: 4px; padding: 7px; selection-background-color: #cce8dd; selection-color: #142c2b; }
QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color: #008b73; }
QListView, QTableView { background: #fcfcfa; alternate-background-color: #f5f7f3; border: 1px solid #d7dfda; border-radius: 4px; outline: 0; selection-background-color: #d5ebe1; selection-color: #153c35; }
QListView::item { padding: 15px 13px; border-bottom: 1px solid #e6eae4; }
QListView::item:selected { background: #d5ebe1; border-left: 3px solid #008b73; }
QTableView::item { padding: 10px 8px; border-bottom: 1px solid #e8ece7; }
QHeaderView::section { background: #f0f2ee; color: #526167; padding: 10px 8px; border: none; border-bottom: 1px solid #d7dfda; font-weight: 600; }
QTabWidget::pane { border: 0; }
QTabBar::tab { padding: 9px 19px; background: #eef2ed; border: 1px solid #d3ddd6; }
QTabBar::tab:selected { background: #008b73; color: white; }
QStatusBar { background: #f0f3ee; border-top: 1px solid #d9e0d9; padding: 4px; }
QMenuBar, QMenu { background: #faf9f6; }
QMenu::item { padding: 8px 24px; }
QMenu::item:selected { background: #d5ebe1; }
QSplitter::handle { background: #e5eae3; width: 2px; height: 2px; }
QLabel#errorBanner { background: #fff1d8; color: #785117; padding: 10px; border: 1px solid #e5c58b; border-radius: 4px; }
QGroupBox { border: 1px solid #d7dfda; border-radius: 5px; margin-top: 16px; padding-top: 15px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; font-weight: 600; }
QCheckBox { spacing: 8px; padding: 5px 0; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #80948a; border-radius: 2px; background: white; }
QCheckBox::indicator:checked { background: #008b73; border-color: #008b73; }
QCheckBox::indicator:disabled { background: #e8ece5; border-color: #c5d0c5; }
"""


def apply_theme(app):
    app.setStyle("Fusion")
    palette = QPalette()
    for role, value in ((QPalette.ColorRole.Window, '#faf9f6'), (QPalette.ColorRole.Base, '#ffffff'), (QPalette.ColorRole.Text, '#24343b'), (QPalette.ColorRole.WindowText, '#24343b'), (QPalette.ColorRole.Highlight, '#d5ebe1'), (QPalette.ColorRole.HighlightedText, '#153c35'), (QPalette.ColorRole.Button, '#ffffff'), (QPalette.ColorRole.ButtonText, '#24343b')):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


def primary(button):
    button.setProperty('primary', True)
    return button
