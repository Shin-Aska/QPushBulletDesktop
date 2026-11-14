"""Native Qt Widgets desktop. Provider, credentials and storage belong to core."""
import argparse
from datetime import datetime, timezone
from html import escape
import sys
from PyQt6 import sip
from PyQt6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListView, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget, QComboBox, QTableView, QAbstractItemView, QHeaderView, QStackedWidget, QInputDialog)
from pbdesk.config import Settings
from .worker import CoreWorker
from .actions import ComposeDialog, ReviewDialog, action_preview, plain_label
from .models import DeviceModel
from .activity import ActivityDialog
from .chat import SmsPage
from .theme import apply_theme, primary
from .widgets import NotificationDelegate, PillDelegate, card_frame, divider_line, make_icon
from .devices import AddDeviceDialog, EncryptionDialog, RemoveDeviceDialog, request


def stamp(value):
    if not value:
        return 'not yet synced'
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc).astimezone().strftime('%d %b, %H:%M')
        except (ValueError, OverflowError, OSError):
            return 'Unknown time'
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone().strftime('%d %b, %H:%M')
    except ValueError:
        return str(value)[:80]


class InboxModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        item = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{item.get('title') or '(Untitled)'}\n{item.get('body', '')[:110].replace(chr(10), ' ')}"
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(item.get('created_at', ''))
        return None

    def replace(self, items):
        if self.items != items:
            self.beginResetModel()
            self.items = list(items)
            self.endResetModel()


class MainWindow(QMainWindow):
    def __init__(self, worker):
        super().__init__()
        apply_theme(QApplication.instance())
        self.worker = worker
        self._exiting = False
        self.devices, self.actions, self.items = [], [], []
        self.account_id = None
        self.account_known = False
        self.selected_device_id = None
        self.capabilities = {}
        self.dialogs = []
        self.command_error = False
        self.registration_pending = None
        self.web_state = {'enabled': False, 'url': '', 'host': '127.0.0.1', 'port': 8766, 'error': None}
        self.setWindowTitle('pbdesk — Notifications')
        self.resize(1200, 790)
        self.setMinimumSize(850, 540)
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(200)
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(12, 16, 12, 18)
        rail.setSpacing(4)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(10, 6, 10, 18)
        brand_row.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setPixmap(make_icon('brand', '#ffffff', 22).pixmap(22, 22))
        brand = QLabel('pbdesk')
        brand.setObjectName('brand')
        brand.setStyleSheet('padding: 0px;')
        brand_row.addWidget(brand_icon)
        brand_row.addWidget(brand, 1)
        rail.addLayout(brand_row)
        self.navigation = []
        for index, (icon_kind, text) in enumerate((('notifications', 'Notifications'), ('sms', 'SMS'), ('devices', 'Devices'))):
            button = QPushButton(text)
            button.setIcon(make_icon(icon_kind, '#e4edec', 18))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, index=index: self.navigate(index))
            self.navigation.append(button)
            rail.addWidget(button)
        rail.addStretch()
        self.web_label = plain_label('Local web: off')
        self.web_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rail.addWidget(self.web_label)
        settings_button = QPushButton('Settings')
        settings_button.setObjectName('sidebarSettings')
        settings_button.setIcon(make_icon('settings', '#e4edec', 16))
        settings_button.setIconSize(QSize(16, 16))
        settings_button.clicked.connect(self.show_settings)
        rail.addWidget(settings_button)
        outer.addWidget(sidebar)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)
        self.heading = QLabel('Notifications')
        self.heading.setObjectName('pageTitle')
        self.subtitle = plain_label('Retained on this computer')
        self.subtitle.setObjectName('subtitle')
        self.subtitle.hide()
        self.provider_status = plain_label('Pushbullet: waiting · Incoming decryption: unknown')
        self.provider_status.setWordWrap(False)
        self.provider_status.setObjectName('statusPill')
        self.compose_button = primary(QPushButton('+ Compose'))
        self.compose_button.setEnabled(False)
        self.compose_button.clicked.connect(lambda: self.compose())
        self.new_sms_button = primary(QPushButton('+ New message'))
        self.new_sms_button.clicked.connect(lambda: self.sms_page.start_new())
        self.add_device_button = primary(QPushButton('+ Add device'))
        self.add_device_button.clicked.connect(self.add_device)
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self.heading)
        header.addStretch(1)
        header.addWidget(self.provider_status)
        self.activity_button = QPushButton('Message activity…')
        self.activity_button.clicked.connect(self.show_activity)
        header.addWidget(self.activity_button)
        header.addWidget(self.compose_button)
        header.addWidget(self.new_sms_button)
        header.addWidget(self.add_device_button)
        layout.addLayout(header)
        self.error = plain_label()
        self.error.setObjectName('errorBanner')
        self.error.hide()
        layout.addWidget(self.error)
        self.tabs = QStackedWidget()
        self.build_inbox_tab()
        self.sms_page = SmsPage(worker, self)
        self.sms_page.new_button.hide()
        self.sms_page.review_requested.connect(self.review_sms_action)
        self.sms_page.action_created.connect(self.merge_action)
        self.sms_page.activity_requested.connect(self.show_activity)
        self.tabs.addWidget(self.sms_page)
        self.activity = ActivityDialog(self)
        self.activity.review_requested.connect(self.review)
        self.action_model = self.activity.model
        self.actions_view = self.activity.view
        self.action_detail = self.activity.detail
        self.review_button = self.activity.review_button
        self.build_device_tab()
        layout.addWidget(self.tabs, 1)
        outer.addWidget(content, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage('Starting private core…')
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)
        self.search.textChanged.connect(lambda: self.debounce.start())
        self.debounce.timeout.connect(self.search_changed)
        self.build_menu()
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(QIcon.fromTheme('mail-message-new', self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)), self)
            self.tray.setToolTip('pbdesk')
            tray_menu = QMenu(self)
            tray_menu.addAction('Show pbdesk', self.show_from_tray)
            tray_menu.addAction('Exit desktop', self.exit_desktop)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(lambda reason: self.show_from_tray() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()
        worker.snapshot.connect(self.apply_snapshot)
        worker.connection.connect(self.statusBar().showMessage)
        worker.failed.connect(self.show_error)
        worker.stopped_all.connect(self.exit_desktop)
        if hasattr(worker, 'result'):
            worker.result.connect(self.command_result)
        self.navigate(0)

    def build_menu(self):
        menu = self.menuBar().addMenu('Application')
        menu.addAction('Web interface settings…', self.show_settings)
        refresh = menu.addAction('Refresh current view', self.refresh_current)
        refresh.setShortcut(QKeySequence.StandardKey.Refresh)
        find = QAction(self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self.focus_search)
        self.addAction(find)
        menu.addAction('Message activity…', self.show_activity)
        compose = menu.addAction('New message…', lambda: self.compose())
        compose.setShortcut(QKeySequence('Ctrl+N'))
        menu.addAction('Reload provider credentials', lambda: request(self.worker, 'reload_provider'))
        menu.addSeparator()
        menu.addAction('Stop all services…', self.stop_all)
        exit_action = menu.addAction('Exit desktop', self.exit_desktop)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)

    def navigate(self, index):
        self.tabs.setCurrentIndex(index)
        for i, button in enumerate(self.navigation):
            button.setChecked(i == index)
        title, subtitle = [('Notifications', 'Pushbullet messages and mirrored notifications'), ('SMS', 'Conversations from your selected phone'), ('Devices', 'Registrations, capabilities and local encryption')][index]
        self.heading.setText(title)
        self.subtitle.setText(subtitle)
        self.setWindowTitle('pbdesk — ' + title)
        self.compose_button.setVisible(index == 0)
        self.new_sms_button.setVisible(index == 1)
        self.add_device_button.setVisible(index == 2)
        self.compose_button.setEnabled(index == 1 or bool(self.capabilities.get("features", {}).get("send")))

    def build_inbox_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        sync_row = QHBoxLayout()
        sync_row.setContentsMargins(0, 0, 0, 0)
        sync_row.setSpacing(8)
        self.inbox_sync = plain_label('Stored on this computer')
        self.inbox_sync.setWordWrap(False)
        self.inbox_sync.setObjectName('muted')
        refresh_btn = QPushButton('Refresh')
        refresh_btn.setObjectName('inlineLink')
        refresh_btn.setIcon(make_icon('refresh', '#4a5960', 14))
        refresh_btn.setIconSize(QSize(14, 14))
        refresh_btn.clicked.connect(self.refresh_current)
        sync_row.addWidget(self.inbox_sync)
        sync_row.addWidget(refresh_btn)
        sync_row.addStretch()
        layout.addLayout(sync_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText('Search notifications…')
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('Search notifications')
        self.inbox_device_label = plain_label('Source device')
        self.inbox_device = QComboBox()
        self.inbox_device.setAccessibleName('Notifications source device')
        self.inbox_device.addItem('All devices', None)
        self.inbox_device.currentIndexChanged.connect(lambda: self.select_device(self.inbox_device.currentData()))

        self.model = InboxModel()
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setItemDelegate(NotificationDelegate(stamp, self.list))
        self.list.setAccessibleName('Notifications')

        left = card_frame()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 10)
        left_layout.setSpacing(8)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.inbox_device)
        left_layout.addWidget(divider_line())
        self.inbox_empty = plain_label('No retained items\n\nNew messages appear here after synchronization.')
        self.inbox_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.inbox_empty.setObjectName('muted')
        left_layout.addWidget(self.inbox_empty, 1)
        self.inbox_empty.hide()
        left_layout.addWidget(self.list, 1)

        right = card_frame()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(24, 20, 24, 16)
        right_layout.setSpacing(10)
        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        self.item_type_badge = QLabel('NOTE')
        self.item_type_badge.setObjectName('badge')
        self.item_decrypt_badge = QLabel('Decrypted locally')
        self.item_decrypt_badge.setObjectName('badge')
        self.item_decrypt_badge.setProperty('tone', 'jade')
        self.item_decrypt_badge.hide()
        badge_row.addWidget(self.item_type_badge)
        badge_row.addWidget(self.item_decrypt_badge)
        badge_row.addStretch()
        right_layout.addLayout(badge_row)
        self.detail = QTextBrowser()
        self.detail.setObjectName('plainDetail')
        self.detail.setOpenExternalLinks(False)
        self.detail.setAccessibleName('Selected item details')
        right_layout.addWidget(self.detail, 1)
        right_layout.addWidget(divider_line())
        footer_row = QHBoxLayout()
        self.item_source_label = plain_label('Source device: Unassociated')
        self.item_source_label.setObjectName('muted')
        self.copy_item_button = QPushButton('Copy text')
        self.copy_item_button.clicked.connect(self.copy_selected_item)
        footer_row.addWidget(self.item_source_label, 1)
        footer_row.addWidget(self.copy_item_button)
        right_layout.addLayout(footer_row)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([360, 640])
        layout.addWidget(split, 1)
        self.list.selectionModel().currentChanged.connect(self.show_item)
        self.tabs.addWidget(page)

    def copy_selected_item(self):
        item = self.list.currentIndex().data(Qt.ItemDataRole.UserRole)
        if not item:
            return
        parts = [item.get('title') or '', item.get('body') or '', item.get('url') or '']
        text = '\n\n'.join(p for p in parts if p)
        if text:
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage('Notification text copied to clipboard.')

    @staticmethod
    def table_view(model):
        view = QTableView()
        view.setModel(model)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setAlternatingRowColors(True)
        view.verticalHeader().hide()
        view.verticalHeader().setDefaultSectionSize(48)
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        view.horizontalHeader().setStretchLastSection(True)
        return view

    def build_device_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        sync_row = QHBoxLayout()
        sync_row.setContentsMargins(0, 0, 0, 0)
        sync_row.setSpacing(8)
        self.devices_status = plain_label('Device cache has not been loaded.')
        self.devices_status.setWordWrap(False)
        self.devices_status.setObjectName('muted')
        self.refresh_devices_button = QPushButton('Refresh from Pushbullet')
        self.refresh_devices_button.setObjectName('inlineLink')
        self.refresh_devices_button.setIcon(make_icon('refresh', '#4a5960', 14))
        self.refresh_devices_button.setIconSize(QSize(14, 14))
        self.refresh_devices_button.clicked.connect(self.refresh_devices)
        sync_row.addWidget(self.devices_status)
        sync_row.addWidget(self.refresh_devices_button)
        sync_row.addStretch()
        layout.addLayout(sync_row)
        self.registration_recovery = QPushButton('Review uncertain registration…')
        self.registration_recovery.clicked.connect(self.resolve_registration)
        self.registration_recovery.hide()
        layout.addWidget(self.registration_recovery)

        left_card = card_frame()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(12, 12, 12, 8)
        left_layout.setSpacing(8)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_lbl = plain_label('Filter:')
        filter_lbl.setObjectName('muted')
        self.device_filter = QComboBox()
        self.device_filter.addItems(['All devices', 'Active push devices', 'Active SMS devices', 'Inactive devices'])
        self.device_filter.currentIndexChanged.connect(self.update_devices)
        filter_row.addWidget(filter_lbl)
        filter_row.addWidget(self.device_filter)
        filter_row.addStretch()
        left_layout.addLayout(filter_row)
        self.device_model = DeviceModel()
        self.devices_view = self.table_view(self.device_model)
        self.devices_view.verticalHeader().setDefaultSectionSize(62)
        self.devices_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.devices_view.setItemDelegate(PillDelegate({1}, {2, 3}, self.devices_view))
        self.devices_view.setAccessibleName('Cached devices')
        self.devices_view.selectionModel().currentChanged.connect(self.show_device)
        left_layout.addWidget(self.devices_view, 1)

        inspector = card_frame()
        info = QVBoxLayout(inspector)
        info.setContentsMargins(20, 18, 20, 16)
        info.setSpacing(10)
        self.device_detail = QTextBrowser()
        self.device_detail.setObjectName('plainDetail')
        self.device_detail.document().setDefaultStyleSheet("body {font-size:13px; color:#24343b;} h2 {font-size:20px; color:#152a30; margin-top:0px; margin-bottom:4px;} h3 {font-size:14px; color:#152a30; margin-top:10px; margin-bottom:4px;} p {margin-top:4px; margin-bottom:4px;} td {padding:3px 2px;}")
        self.device_detail.setOpenExternalLinks(False)
        info.addWidget(self.device_detail, 1)
        info.addWidget(divider_line())
        self.device_history = plain_label('Local history · Choose a device')
        self.device_history.setObjectName('muted')
        info.addWidget(self.device_history)
        self.encryption_button = QPushButton('Manage encryption…')
        self.encryption_button.clicked.connect(self.manage_encryption)
        info.addWidget(self.encryption_button)
        row = QHBoxLayout()
        self.rename_button = QPushButton('Rename…')
        self.rename_button.clicked.connect(self.rename_device)
        self.remove_button = QPushButton('Remove device…')
        self.remove_button.setProperty('danger', True)
        self.remove_button.clicked.connect(self.remove_device)
        row.addWidget(self.rename_button)
        row.addWidget(self.remove_button)
        info.addLayout(row)
        split = QSplitter()
        split.addWidget(left_card)
        split.addWidget(inspector)
        split.setSizes([580, 420])
        layout.addWidget(split, 1)
        note = plain_label('Active describes registration. Current connectivity is unknown. Phones and browsers register through their Pushbullet client.')
        note.setObjectName('muted')
        layout.addWidget(note)
        self.tabs.addWidget(page)

    def selected_device(self):
        return next((device for device in self.devices if device['id'] == self.selected_device_id), None)

    def sync_context_controls(self):
        selector = self.inbox_device
        selector.blockSignals(True)
        selector.clear()
        selector.addItem('All / unassociated devices', None)
        for device in self.devices:
            selector.addItem((device.get('nickname') or device['id']) + (' · inactive' if not device.get('active') else ''), device['id'])
        index = selector.findData(self.selected_device_id)
        if self.selected_device_id is not None and index < 0:
            selector.addItem(self.selected_device_id + ' · unavailable', self.selected_device_id)
            index = selector.count() - 1
        selector.setCurrentIndex(max(index, 0))
        selector.blockSignals(False)

    def select_device(self, device_id):
        # Notifications filtering never changes the SMS phone context.
        self.selected_device_id = device_id
        self.sync_context_controls()
        self.update_messages()

    def focus_search(self):
        if self.tabs.currentIndex() == 1:
            self.sms_page.focus_search()
        elif self.tabs.currentIndex() == 2:
            self.device_filter.setFocus()
        else:
            self.search.setFocus()

    def refresh_current(self):
        if self.tabs.currentIndex() == 2:
            self.refresh_devices()
        elif self.tabs.currentIndex() == 1:
            self.sms_page.refresh()
        else:
            self.worker.request('search', self.search.text())

    def search_changed(self):
        self.update_messages()
        self.worker.request('search', self.search.text())

    def message_rows(self):
        return [item for item in self.items if self.selected_device_id is None or (item.get('source_device_id') or item.get('device_id')) == self.selected_device_id]

    def sync_text(self):
        sync = self.capabilities.get('push_sync')
        if not sync:
            return 'Stored on this computer · Refresh reads service state'
        text = 'Last synced ' + stamp(sync.get('last_success_at'))
        if sync.get('state') in {'refreshing', 'queued', 'syncing'}:
            text += '  ·  ↻ Refreshing…'
        if sync.get('error'):
            text += '  ·  ' + str(sync['error']) + ' · Stored records remain available.'
        return text

    def replace_list(self, view, model, rows, render):
        selected = view.currentIndex().data(Qt.ItemDataRole.UserRole)
        selected_id = selected.get('id') if selected else None
        scroll = view.verticalScrollBar().value()
        detail = self.detail
        detail_scroll = detail.verticalScrollBar().value()
        model.replace(rows)
        row = next((i for i, item in enumerate(rows) if item.get('id') == selected_id), 0 if rows else None)
        if row is not None:
            view.setCurrentIndex(model.index(row))
            render(model.index(row))
            view.verticalScrollBar().setValue(scroll)
            if selected_id == rows[row].get('id'):
                detail.verticalScrollBar().setValue(detail_scroll)
        else:
            detail.setPlainText('No stored messages in this view. New messages appear here after synchronization.')

    def update_messages(self):
        query = self.search.text().casefold()
        rows = [item for item in self.message_rows() if not query or query in (item.get('title', '') + ' ' + item.get('body', '') + ' ' + ' '.join(message.get('body', '') for message in item.get('history', []))).casefold()]
        self.replace_list(self.list, self.model, rows, self.show_item)
        self.inbox_empty.setText('No matching messages' if query else 'No retained items\n\nNew messages appear here after synchronization.')
        self.inbox_empty.setVisible(not rows)
        self.list.setVisible(bool(rows))
        self.inbox_sync.setText(self.sync_text())

    def show_item(self, current, previous=QModelIndex()):
        item = current.data(Qt.ItemDataRole.UserRole)
        if not item:
            return
        kind = item.get('type', 'message').replace('_', ' ').upper()
        self.item_type_badge.setText(kind)
        self.item_decrypt_badge.setVisible(bool(item.get('decrypted')))
        document = (
            '<h2 style="margin:0 0 4px 0; font-size:21px; color:#152a30;">' + escape(item.get('title') or '(Untitled)') + '</h2>'
            + '<p style="color:#657176; margin:0 0 12px 0; font-size:13px;">' + escape(stamp(item.get('created_at'))) + '</p>'
            + '<p style="line-height:1.55; color:#24343b; font-size:14px; margin-top:10px;">' + escape(item.get('body', '')).replace('\n', '<br>') + '</p>'
        )
        if item.get('url'):
            document += '<p style="margin-top:10px; color:#00765f;">' + escape(item['url']) + '</p>'
        self.detail.setHtml(document)
        self.item_source_label.setText('Source device: ' + str(item.get('source_device_id') or item.get('device_id') or 'Unassociated'))
        self.copy_item_button.setEnabled(bool(item.get('title') or item.get('body') or item.get('url')))

    def update_devices(self):
        mode = self.device_filter.currentIndex()
        selected = self.devices_view.currentIndex().data(Qt.ItemDataRole.UserRole)
        selected_id = selected['id'] if selected else None
        rows = [device for device in self.devices if mode == 0 or (mode == 1 and device.get('active') and device.get('can_push')) or (mode == 2 and device.get('active') and device.get('can_sms')) or (mode == 3 and not device.get('active'))]
        self.device_model.replace(rows)
        row = next((i for i, device in enumerate(rows) if device['id'] == selected_id), 0 if rows else None)
        if row is not None:
            self.devices_view.setCurrentIndex(self.device_model.index(row, 0))
        self.show_device(self.devices_view.currentIndex())

    def show_device(self, current, previous=QModelIndex()):
        device = current.data(Qt.ItemDataRole.UserRole)
        for button in (self.encryption_button, self.rename_button, self.remove_button):
            button.setEnabled(bool(device and device.get('active')))
        if not device:
            self.device_detail.setPlainText('Choose a device to inspect its registration, capabilities and local encryption.')
            self.device_history.setText('Local history · Choose a device')
            return
        encryption, history = device.get('encryption', {}), device.get('history', {})
        state = {'verified': 'Verified · sample decrypted locally', 'key_ready': 'Key ready · not yet verified', 'locked': 'Key unavailable or could not decrypt', 'unconfigured': 'Not configured', 'error': 'Decryption needs attention'}.get(encryption.get('state'), 'Not configured')
        rows = [('Registration', 'Active' if device.get('active') else 'Inactive'), ('Connectivity', 'Unknown'), ('Push', 'Supported' if device.get('can_push') else '—'), ('SMS', 'Supported' if device.get('can_sms') else '—')]
        document = '<h2>' + escape(device.get('nickname') or '(unnamed)') + '</h2><p style="color:#657176">' + escape(' '.join(str(device.get(key) or '') for key in ('manufacturer', 'model')).strip()) + '<br>' + escape(device['id']) + '</p><hr><table width="100%" cellpadding="1">'
        for label, value in rows:
            document += '<tr><td>' + label + '</td><td>' + value + '</td></tr>'
        document += '</table><hr><h3>Incoming encryption</h3><p>' + escape(state) + '</p><p>Key source: ' + escape('Shared default · KWallet' if encryption.get('mode', 'shared') == 'shared' else str(encryption.get('entry', 'Device-specific · KWallet'))) + '</p>'
        self.device_history.setText('LOCAL HISTORY\n' + str(history.get('count', 0)) + ' SMS stored on this computer\nLast synced ' + stamp(history.get('last_success_at')))
        self.device_detail.setHtml(document)

    def add_device(self):
        dialog = AddDeviceDialog(self.worker, self)
        dialog.registered.connect(self.registered_device)
        self.track_dialog(dialog)

    def registered_device(self, device_id):
        self.navigate(2)
        row = next((i for i, device in enumerate(self.device_model.records) if device['id'] == device_id), None)
        if row is not None:
            self.devices_view.setCurrentIndex(self.device_model.index(row, 0))
        device = next((device for device in self.devices if device['id'] == device_id), None)
        if device:
            QTimer.singleShot(0, lambda: self.track_dialog(EncryptionDialog(self.worker, device, self)))

    def resolve_registration(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Review uncertain registration')
        dialog.resize(570, 370)
        layout = QVBoxLayout(dialog)
        layout.addWidget(plain_label('Pushbullet may have registered this computer. Refresh devices first, then choose its exact observed registration. No automatic retry is performed.'))
        layout.addWidget(plain_label('Pending name: ' + str((self.registration_pending or {}).get('nickname', 'Unknown'))))
        refresh = QPushButton('Refresh devices first')
        refresh.clicked.connect(self.refresh_devices)
        layout.addWidget(refresh)
        selector = QComboBox()
        def update_choices(data=None):
            selected = selector.currentData()
            selector.clear()
            selector.addItem('Choose a reviewed result…', 'unselected')
            for device in self.devices:
                if device.get('active') and device.get('model') == 'pbdesk Linux':
                    selector.addItem((device.get('nickname') or '(unnamed)') + ' · ' + device['id'], device['id'])
            selector.addItem('No matching registration · abandon this uncertain attempt', None)
            selector.setCurrentIndex(max(selector.findData(selected), 0))
        update_choices()
        self.worker.snapshot.connect(update_choices)
        dialog.finished.connect(lambda: self.worker.snapshot.disconnect(update_choices))
        layout.addWidget(selector)
        ack = QCheckBox('I reviewed the refreshed list and acknowledge the unknown outcome.')
        layout.addWidget(ack)
        feedback = plain_label()
        layout.addWidget(feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        resolve = primary(buttons.addButton('Resolve reviewed result', QDialogButtonBox.ButtonRole.ActionRole))
        resolve.setEnabled(False)
        selector.currentIndexChanged.connect(lambda: resolve.setEnabled(ack.isChecked() and selector.currentData() != 'unselected'))
        ack.toggled.connect(lambda checked: resolve.setEnabled(checked and selector.currentData() != 'unselected'))
        buttons.rejected.connect(dialog.reject)
        def submit():
            request(self.worker, 'resolve_registration', device_id=selector.currentData())
            resolve.setEnabled(False)
            feedback.setText('Resolving the reviewed result. Check Devices for its current state.')
        resolve.clicked.connect(submit)
        layout.addWidget(buttons)
        self.track_dialog(dialog)

    def manage_encryption(self):
        device = self.devices_view.currentIndex().data(Qt.ItemDataRole.UserRole)
        if device:
            self.track_dialog(EncryptionDialog(self.worker, device, self))

    def rename_device(self):
        device = self.devices_view.currentIndex().data(Qt.ItemDataRole.UserRole)
        if device:
            name, ok = QInputDialog.getText(self, 'Rename device', 'Device name · ' + device['id'], text=device.get('nickname') or '')
            if ok and name.strip():
                request(self.worker, 'rename_device', device_id=device['id'], nickname=name.strip())

    def remove_device(self):
        device = self.devices_view.currentIndex().data(Qt.ItemDataRole.UserRole)
        if device:
            self.track_dialog(RemoveDeviceDialog(self.worker, device, self))

    def update_activity(self):
        self.activity.apply_actions(self.actions, self.devices)

    def show_activity(self):
        self.update_activity()
        self.activity.show()
        self.activity.raise_()
        self.activity.activateWindow()

    def track_dialog(self, dialog):
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda: self.dialogs.remove(dialog) if dialog in self.dialogs else None)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()

    def compose(self, action=None):
        if action is None and self.tabs.currentIndex() == 1:
            self.sms_page.start_new()
            return
        if not self.capabilities.get('features', {}).get('send'):
            self.show_error('Message actions are unavailable in this core version. Restart the updated core and refresh capabilities.')
            return
        dialog = ComposeDialog(self.worker, self.devices, self, action=action, account_id=self.account_id if self.account_known else None, kinds=("note", "link") if action is None else None)
        dialog.saved.connect(self.action_saved)
        self.track_dialog(dialog)

    def merge_action(self, action):
        self.actions = [action] + [item for item in self.actions if item['id'] != action['id']]
        self.update_activity()

    def action_saved(self, action):
        self.merge_action(action)
        # Editing a saved SMS leaves the current chat and its draft intact.
        self.defer_current_account(lambda: self.review(action))

    def defer_current_account(self, callback):
        origin = (self.account_known, self.account_id)
        def invoke():
            if not sip.isdeleted(self) and not self._exiting and origin == (self.account_known, self.account_id):
                callback()
        QTimer.singleShot(0, invoke)

    def review_sms_action(self, action):
        if not sip.isdeleted(self) and not self._exiting and self.isVisible() and self.tabs.currentIndex() == 1:
            self.review(action)

    def review(self, action):
        if self.account_known and "account_id" in action and action["account_id"] != self.account_id:
            return
        dialog = ReviewDialog(self.worker, action, self.devices, self.capabilities, self)
        dialog.edit_requested.connect(lambda latest: self.defer_current_account(lambda: self.compose(latest)))
        self.track_dialog(dialog)

    def refresh_devices(self):
        self.refresh_devices_button.setEnabled(False)
        request(self.worker, 'refresh_devices')

    def command_result(self, result):
        command = result['command']
        if command == 'refresh_devices':
            self.refresh_devices_button.setEnabled(True)
        if result.get('error') and command not in {'create_action', 'edit_action', 'approve_action', 'cancel_action'}:
            self.command_error = True
            self.show_error(result['error'])
        elif not result.get('error'):
            self.command_error = False
            data = result.get('data', {})
            matching_account = not self.account_known or data.get('account_id') == self.account_id
            if isinstance(data, dict) and 'devices' in data and matching_account:
                self.devices = data['devices']
                self.update_devices()
                self.sync_context_controls()
            if command == 'refresh_devices' and data.get('provider_state') == 'device_refresh_failed':
                self.command_error = True
                self.show_error('Device refresh did not finish. Cached registrations remain visible. Check the provider connection and refresh again.')
            if command == 'reload_provider':
                self.statusBar().showMessage('Provider credentials reloaded; encrypted backlog is refreshing.')

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()

    def apply_snapshot(self, data):
        # The component sees the original full or partial snapshot once.
        self.sms_page.apply_snapshot(data)
        # Only an authoritative general snapshot can change account context.
        account_source = data.get('capabilities', {})
        if 'account_id' not in account_source:
            account_source = data.get('devices', {})
        if 'account_id' in account_source:
            account_id = account_source['account_id']
            if not self.account_known or self.account_id != account_id:
                if self.account_known:
                    for dialog in list(self.dialogs):
                        dialog.reject()
                self.activity.reject()
                self.selected_device_id = None
                self.items, self.actions, self.devices = [], [], []
                self.account_id, self.account_known = account_id, True
        if 'web' in data:
            self.web_state = data['web']
        self.capabilities = data.get('capabilities', self.capabilities)
        self.devices = data.get('devices', {}).get('devices', self.devices)
        if 'devices' in data:
            self.registration_pending = data['devices'].get('registration_pending')
            self.registration_recovery.setVisible(bool(self.registration_pending))
        self.actions = data.get('actions', self.actions)
        self.items = data.get('items', self.items)
        self.sync_context_controls()
        self.update_devices()
        self.update_activity()
        self.update_messages()
        cap = self.capabilities
        self.compose_button.setEnabled(self.tabs.currentIndex() == 1 or bool(cap.get('features', {}).get('send')))
        p_state = str(cap.get('provider_state', 'unknown'))
        e_state = str(cap.get('encryption_state', 'unknown'))
        pending = int(cap.get('encrypted_pending', 0) or 0)
        if p_state == 'connected' and e_state in {'ready', 'key_ready', 'verified'} and pending == 0:
            label = 'Decryption ready' if e_state == 'ready' else 'Key ready' if e_state == 'key_ready' else 'Decryption verified'
            self.provider_status.setText(f'●  Connected · {label}')
            self.provider_status.setProperty('tone', '')
        else:
            self.provider_status.setText(f'Pushbullet: {p_state}  ·  Decryption: {e_state}  ·  {pending} encrypted items waiting')
            self.provider_status.setProperty('tone', 'warn')
        self.provider_status.style().unpolish(self.provider_status)
        self.provider_status.style().polish(self.provider_status)
        if 'devices' in data:
            self.devices_status.setText('Last refreshed ' + stamp(data['devices'].get('synced_at')))
        self.web_label.setText('Local web: ' + ('on' if self.web_state.get('enabled') else 'off'))
        if 'items' in data and not self.command_error:
            self.error.hide()

    def show_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Local web interface")
        dialog.resize(520, 360)
        layout = QVBoxLayout(dialog)
        title = QLabel("Local status page")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        layout.addWidget(plain_label("A service-status companion. Use the desktop app for notifications, conversations and message activity."))
        enabled = QCheckBox("Enable local web status page")
        enabled.setChecked(bool(self.web_state["enabled"]))
        state = QLabel()
        state.setWordWrap(True)
        open_button = primary(QPushButton("Open web interface"))
        def update(data=None):
            value = self.web_state
            enabled.blockSignals(True)
            enabled.setChecked(bool(value["enabled"]))
            enabled.blockSignals(False)
            state.setText(f"{'Enabled' if value['enabled'] else 'Disabled'} · {value['host']}:{value['port']}\n{value.get('error') or 'Loopback only · this computer'}")
            open_button.setEnabled(bool(value["enabled"]) and not value.get("error"))
        update()
        enabled.toggled.connect(lambda checked: self.worker.request("web", checked))
        self.worker.snapshot.connect(update)
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.web_state["url"])))
        layout.addWidget(enabled)
        layout.addWidget(state)
        layout.addWidget(open_button)
        layout.addWidget(plain_label("Credentials are read locally from kdewallet → Pushbullet. api-key and the shared e2e-password stay in KWallet."))
        notice = QLabel("Login autostart is not enabled. Closing this window leaves the core and any enabled web host running.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        self.worker.snapshot.disconnect(update)

    def stop_all(self):
        box = QMessageBox(self)
        box.setWindowTitle('Stop all pbdesk services?')
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText('Stop the shared services?')
        box.setInformativeText('This stops the private core and web host. Other desktop windows and MCP clients will disconnect.\n\nTo leave services running, choose Exit desktop instead.')
        keep = box.addButton('Keep running', QMessageBox.ButtonRole.RejectRole)
        stop = box.addButton('Stop all services', QMessageBox.ButtonRole.DestructiveRole)
        stop.setProperty('danger', True)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() is stop:
            self.worker.request('shutdown')

    def show_from_tray(self):
        self.navigate(0)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def exit_desktop(self):
        self._exiting = True
        self.close()

    def closeEvent(self, event):
        if not self._exiting and self.tray and self.tray.isVisible():
            self.hide()
            event.ignore()
            return
        self._exiting = True
        if self.tray:
            self.tray.hide()
        for dialog in list(self.dialogs):
            dialog.reject()
        self.activity.close()
        self.worker.stop()
        if not self.worker.isRunning():
            QApplication.instance().quit()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="Native pbdesk notifications and SMS; starts or connects to the private core.")
    parser.add_argument("--demo", action="store_true", help="Seed one sanitized stored-note fixture in a newly started core.")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("pbdesk")
    app.setQuitOnLastWindowClosed(False)
    worker = CoreWorker(Settings.from_env(), demo=args.demo)
    window = MainWindow(worker)
    # On startup failure keep the actionable error window open until the user exits.
    worker.finished.connect(lambda: app.quit() if window._exiting else None)
    window.show()
    worker.start()
    exit_code = app.exec()
    worker.stop()
    worker.wait()
    raise SystemExit(exit_code)
