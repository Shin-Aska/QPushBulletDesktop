"""Account-scoped saved message activity, independent of reading filters."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
    QHeaderView, QPushButton, QSplitter, QTableView, QTextBrowser, QVBoxLayout)
from .actions import action_preview, plain_label
from .models import ActionModel
from .theme import primary
from .widgets import PillDelegate, card_frame


class ActivityDialog(QDialog):
    """Dialog listing saved message actions with preview details and review launch."""
    review_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        """Initialize the saved message activity table and detail preview pane.

        :param parent: Optional parent widget owning this dialog.
        :type parent: PyQt6.QtWidgets.QWidget | None
        """
        super().__init__(parent)
        self.devices = []
        self.setWindowTitle('Message activity')
        self.resize(1040, 660)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = plain_label('Message activity')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        sub = plain_label('All saved Note, Link and SMS actions. Review the exact destination and content before approving. Pushbullet acceptance is not a delivery receipt.')
        sub.setObjectName('subtitle')
        layout.addWidget(sub)
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
        self.view.setItemDelegate(PillDelegate({1, 3}, parent=self.view))
        left_card = card_frame()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.addWidget(self.view)

        self.detail = QTextBrowser()
        self.detail.setObjectName('plainDetail')
        self.detail.setOpenExternalLinks(False)
        self.detail.setAccessibleName('Saved message action details')
        right_card = card_frame()
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(18, 16, 18, 16)
        right_layout.addWidget(self.detail)

        split = QSplitter()
        split.addWidget(left_card)
        split.addWidget(right_card)
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
        """Replace displayed actions and devices while preserving selection and scroll.

        :param actions: Saved action records to display in the table.
        :type actions: collections.abc.Sequence[dict]
        :param devices: Cached Pushbullet device records used for destination labels.
        :type devices: collections.abc.Sequence[dict]
        """
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
        """Render details for the currently selected action or show empty-state guidance.

        :param current: Model index of the newly selected table row.
        :type current: PyQt6.QtCore.QModelIndex
        :param previous: Model index of the previously selected table row, if any.
        :type previous: PyQt6.QtCore.QModelIndex | None
        """
        action = current.data(Qt.ItemDataRole.UserRole)
        self.review_button.setEnabled(bool(action))
        self.detail.setPlainText(action_preview(action, self.devices) if action else 'No saved actions. Save a message for review to add it here. Saving sends nothing.')

    def review_selected(self):
        """Emit review_requested for the currently selected action, if any."""
        action = self.view.currentIndex().data(Qt.ItemDataRole.UserRole)
        if action:
            self.review_requested.emit(dict(action))
