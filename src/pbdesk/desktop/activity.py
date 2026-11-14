"""Account-scoped saved message activity, independent of reading filters."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
    QHeaderView, QPushButton, QSplitter, QTableView, QTextBrowser, QVBoxLayout)
from .actions import action_preview, plain_label
from .models import ActionModel
from .theme import primary


class ActivityDialog(QDialog):
    review_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.devices = []
        self.setWindowTitle('Message activity')
        self.resize(1010, 650)
        layout = QVBoxLayout(self)
        title = plain_label('Message activity')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        layout.addWidget(plain_label('All saved Note, Link and SMS actions. Review the exact destination and content before approving. Pushbullet acceptance is not a delivery receipt.'))
        self.model = ActionModel()
        self.view = QTableView()
        self.view.setAccessibleName('All saved message actions')
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(48)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.view.setColumnHidden(4, True)
        self.view.setColumnHidden(5, True)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.setAccessibleName('Saved message action details')
        split = QSplitter()
        split.addWidget(self.view)
        split.addWidget(self.detail)
        split.setSizes([600, 400])
        layout.addWidget(split, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.review_button = primary(QPushButton('Review selected action…'))
        self.review_button.clicked.connect(self.review_selected)
        buttons.addButton(self.review_button, QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)
        self.view.selectionModel().currentChanged.connect(self.show_selected)
        self.view.doubleClicked.connect(lambda index: self.review_selected())
        self.apply_actions([], [])

    def apply_actions(self, actions, devices):
        self.devices = list(devices)
        selected = self.view.currentIndex().data(Qt.ItemDataRole.UserRole)
        identity = selected['id'] if selected else None
        scroll = self.view.verticalScrollBar().value()
        self.model.replace(list(actions))
        row = next((i for i, action in enumerate(actions) if action['id'] == identity), 0 if actions else None)
        if row is not None:
            self.view.setCurrentIndex(self.model.index(row, 0))
            self.view.verticalScrollBar().setValue(scroll)
        self.show_selected(self.view.currentIndex())

    def show_selected(self, current, previous=None):
        action = current.data(Qt.ItemDataRole.UserRole)
        self.review_button.setEnabled(bool(action))
        self.detail.setPlainText(action_preview(action, self.devices) if action else 'No saved actions. Save a message for review to add it here. Saving sends nothing.')

    def review_selected(self):
        action = self.view.currentIndex().data(Qt.ItemDataRole.UserRole)
        if action:
            self.review_requested.emit(dict(action))
