"""Table model for task totals."""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..core.models import TaskSummary


class TaskTotalsModel(QAbstractTableModel):
    HEADERS = ("Task", "Minutes", "Total")

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
                return str(row.total_minutes)
            if index.column() == 2:
                return row.pretty_total
        if role == Qt.TextAlignmentRole and index.column() != 0:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # type: ignore[override]
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

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
