"""Small native table models for cached devices and persistent outbox states."""
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from .actions import STATE_LABELS


class RecordTableModel(QAbstractTableModel):
    """Base read-only table model backed by a list of record dictionaries."""
    columns = ()

    def __init__(self):
        """Initialize an empty record table model."""
        super().__init__()
        self.records = []

    def rowCount(self, parent=QModelIndex()):
        """Return the number of top-level rows in the table model.

        :param parent: Parent model index; valid indices have no child rows.
        :type parent: QModelIndex
        :return: Number of top-level records, or 0 for valid parent indices.
        :rtype: int
        """
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        """Return the number of defined columns for top-level items.

        :param parent: Parent model index; valid indices have no child columns.
        :type parent: QModelIndex
        :return: Number of table columns, or 0 for valid parent indices.
        :rtype: int
        """
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return horizontal column header labels for display roles.

        :param section: Zero-based column or row index.
        :type section: int
        :param orientation: Header orientation (horizontal or vertical).
        :type orientation: Qt.Orientation
        :param role: Qt item data role being queried.
        :type role: int
        :return: Column header label when horizontal display data is requested, else None.
        :rtype: str | None
        """
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """Return the raw record for UserRole or formatted column text.

        :param index: Model index specifying row and column.
        :type index: QModelIndex
        :param role: Qt item data role being queried.
        :type role: int
        :return: Raw record dict for UserRole, formatted cell text, or None.
        :rtype: dict | str | None
        """
        if not index.isValid() or index.row() >= len(self.records):
            return None
        item = self.records[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self.values(item)[index.column()]
        return None

    def replace(self, records):
        """Reset the model when the supplied record list differs from current rows.

        :param records: Replacement list of record dictionaries.
        :type records: collections.abc.Sequence[dict]
        """
        if records != self.records:
            self.beginResetModel()
            self.records = list(records)
            self.endResetModel()


class DeviceModel(RecordTableModel):
    """Table model displaying cached Pushbullet devices and their capabilities."""
    columns = ("Device", "Registration", "Push", "SMS")

    def values(self, item):
        """Format display tuple for a single device record.

        :param item: Cached device dictionary.
        :type item: dict
        :return: Display values for Device, Registration, Push, and SMS columns.
        :rtype: tuple[str, str, str, str]
        """
        return ((item.get("nickname") or "(unnamed)") + "\n" + item["id"], "Active" if item.get("active") else "Inactive", "Supported" if item.get("can_push") else "—", "Supported" if item.get("can_sms") else "—")


class ActionModel(RecordTableModel):
    """Table model displaying persistent outbox action records and states."""
    columns = ("ID", "Kind", "Destination", "State", "Requested by", "Updated")

    def values(self, item):
        """Format display tuple for a single saved action record.

        :param item: Saved action record dictionary.
        :type item: dict
        :return: Display values for ID, Kind, Destination, State, Requested by, and Updated.
        :rtype: tuple[str, str, str, str, str, str]
        """
        return (str(item["id"]), item["kind"], item.get("recipient") or item["device_id"], STATE_LABELS.get(item["state"], item["state"]), item.get("requested_by", "unknown"), item["updated_at"])
