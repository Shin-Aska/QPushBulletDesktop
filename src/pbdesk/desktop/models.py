"""Small native table models for cached devices and persistent outbox states."""
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from .actions import STATE_LABELS


class RecordTableModel(QAbstractTableModel):
    columns = ()

    def __init__(self):
        super().__init__()
        self.records = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.records):
            return None
        item = self.records[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self.values(item)[index.column()]
        return None

    def replace(self, records):
        if records != self.records:
            self.beginResetModel()
            self.records = list(records)
            self.endResetModel()


class DeviceModel(RecordTableModel):
    columns = ("Device", "Registration", "Push", "SMS")

    def values(self, item):
        return ((item.get("nickname") or "(unnamed)") + "\n" + item["id"], "Active" if item.get("active") else "Inactive", "Supported" if item.get("can_push") else "—", "Supported" if item.get("can_sms") else "—")


class ActionModel(RecordTableModel):
    columns = ("ID", "Kind", "Destination", "State", "Requested by", "Updated")

    def values(self, item):
        return (str(item["id"]), item["kind"], item.get("recipient") or item["device_id"], STATE_LABELS.get(item["state"], item["state"]), item.get("requested_by", "unknown"), item["updated_at"])
