"""Table model for task totals."""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..core.models import TaskSummary


class TaskTotalsModel(QAbstractTableModel):
    HEADERS = ("Task", "Category", "Minutes", "Total")

    def __init__(self) -> None:
        super().__init__()
        self._rows: List[TaskSummary] = []

    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        if parent and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]

        if role == Qt.DisplayRole:
            if index.column() == 0:
                return row.task
            if index.column() == 1:
                return row.category or ""
            if index.column() == 2:
                return str(row.total_minutes)
            if index.column() == 3:
                return row.pretty_total
        if role == Qt.EditRole and index.column() == 1:
            return row.category or ""
        if role == Qt.TextAlignmentRole and index.column() not in (0, 1):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # type: ignore[override]
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex):  # type: ignore[override]
        base = super().flags(index)
        if not index.isValid():
            return base
        if index.column() == 1:
            return base | Qt.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole):  # type: ignore[override]
        if role != Qt.EditRole or not index.isValid() or index.column() != 1:
            return False
        row = self.summary_for_row(index.row())
        if row is None:
            return False
        new_value = (value or "").strip()
        current = row.category or ""
        if new_value == current:
            return True
        row.category = new_value or None
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def update_rows(self, rows: List[TaskSummary]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def summary(self) -> list[TaskSummary]:
        return list(self._rows)

    def summary_for_row(self, row: int) -> TaskSummary | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None
