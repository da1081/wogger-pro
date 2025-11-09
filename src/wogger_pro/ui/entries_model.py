"""Table model for displaying editable work-log entries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..core.models import Entry


@dataclass(slots=True)
class EntryRow:
    entry: Entry
    conflicts: list[Entry]
    conflict_summary: str

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


class EntriesTableModel(QAbstractTableModel):
    HEADERS = ("Task", "Category", "Start", "End", "Minutes", "Conflicts", "Delete")

    def __init__(self) -> None:
        super().__init__()
        self._rows: List[EntryRow] = []

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
        entry = row.entry

        if role == Qt.DisplayRole:
            column = index.column()
            if column == 0:
                return entry.task
            if column == 1:
                return entry.category or ""
            if column == 2:
                return self._format_datetime(entry.segment_start)
            if column == 3:
                return self._format_datetime(entry.segment_end)
            if column == 4:
                return str(entry.minutes)
            if column == 5:
                return row.conflict_summary
            if column == 6:
                return "Delete"
        if role == Qt.TextAlignmentRole:
            if index.column() == 4:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if index.column() == 6:
                return int(Qt.AlignCenter | Qt.AlignVCenter)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # type: ignore[override]
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex):  # type: ignore[override]
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled

    # ------------------------------------------------------------------
    def update_entries(self, entries: List[Entry]) -> None:
        rows = self._build_rows(entries)
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def entry_for_row(self, row: int) -> EntryRow | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def row_index_for_entry(self, entry_id: str) -> int | None:
        for index, row in enumerate(self._rows):
            if row.entry.entry_id == entry_id:
                return index
        return None

    # ------------------------------------------------------------------
    def _build_rows(self, entries: List[Entry]) -> List[EntryRow]:
        conflict_map = self._compute_conflicts(entries)
        ordered = sorted(entries, key=lambda entry: (entry.segment_start, entry.segment_end), reverse=True)
        rows: List[EntryRow] = []
        for entry in ordered:
            conflicts = conflict_map.get(entry.entry_id, [])
            summary = self._format_conflict_summary(conflicts)
            rows.append(EntryRow(entry=entry, conflicts=conflicts, conflict_summary=summary))
        return rows

    def _compute_conflicts(self, entries: List[Entry]) -> dict[str, list[Entry]]:
        conflict_map: dict[str, list[Entry]] = {entry.entry_id: [] for entry in entries}
        sorted_entries = sorted(entries, key=lambda entry: entry.segment_start)
        for i, current in enumerate(sorted_entries):
            current_range = current.as_range()
            for other in sorted_entries[i + 1 :]:
                if other.segment_start >= current_range.end:
                    break
                other_range = other.as_range()
                if current_range.overlaps(other_range):
                    conflict_map[current.entry_id].append(other)
                    conflict_map[other.entry_id].append(current)
        return conflict_map

    def _format_conflict_summary(self, conflicts: list[Entry]) -> str:
        if not conflicts:
            return ""
        parts: list[str] = []
        for entry in conflicts:
            parts.append(
                f"{entry.task} ({entry.category or 'No category'} | "
                f"{self._format_datetime(entry.segment_start)} - {self._format_datetime(entry.segment_end)} | "
                f"{entry.minutes}m)"
            )
        return "\n".join(parts)

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M")
