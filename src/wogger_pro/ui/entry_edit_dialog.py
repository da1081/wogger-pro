"""Dialog for editing an existing work-log entry."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCompleter,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.exceptions import PersistenceError, SegmentConflictError
from ..core.models import Entry
from ..core.repository import EntriesRepository
from ..core.time_segments import minutes_between

LOGGER = logging.getLogger("wogger.ui.entry_edit")


class EntryEditDialog(QDialog):
    """Prompt the user to edit an entry and validate time conflicts."""

    def __init__(
        self,
        entry: Entry,
        repository: EntriesRepository,
        category_suggestions: Sequence[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Entry")
        self.setModal(True)

        self._entry = entry
        self._repository = repository
        self._category_suggestions = list(category_suggestions or [])

        self._result_task = entry.task
        self._result_category = entry.category
        self._result_start = entry.segment_start
        self._result_end = entry.segment_end

        self._current_conflicts: list[Entry] = []
        self._updated_entry: Entry | None = None

        self._build_ui()
        self._populate_defaults()
        self._validate()

    # ------------------------------------------------------------------
    @property
    def updated_task(self) -> str:
        return self._result_task

    @property
    def updated_category(self) -> str | None:
        return self._result_category

    @property
    def updated_start(self) -> datetime:
        return self._result_start

    @property
    def updated_end(self) -> datetime:
        return self._result_end

    @property
    def updated_entry(self) -> Entry | None:
        return self._updated_entry

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        description = QLabel("Update the selected entry. Overlapping entries will block the save.", self)
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QFormLayout()
        form.setSpacing(10)

        self._task_edit = QLineEdit(self)
        self._task_edit.textChanged.connect(self._validate)
        form.addRow("Task", self._task_edit)

        self._category_edit = QLineEdit(self)
        self._category_edit.textChanged.connect(self._validate)
        form.addRow("Category", self._category_edit)

        self._start_edit = QDateTimeEdit(self)
        self._start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._start_edit.setCalendarPopup(True)
        self._start_edit.dateTimeChanged.connect(self._validate)
        form.addRow("Start", self._start_edit)

        self._end_edit = QDateTimeEdit(self)
        self._end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._end_edit.setCalendarPopup(True)
        self._end_edit.dateTimeChanged.connect(self._validate)
        form.addRow("End", self._end_edit)

        self._minutes_label = QLabel("", self)
        form.addRow("Minutes", self._minutes_label)

        layout.addLayout(form)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        palette = self._status_label.palette()
        palette.setColor(self._status_label.foregroundRole(), Qt.red)
        self._status_label.setPalette(palette)
        layout.addWidget(self._status_label)

        self._button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

        self._save_button = self._button_box.button(QDialogButtonBox.Save)
        self._save_button.setText("Save")

        self.resize(460, 0)

    def _populate_defaults(self) -> None:
        self._task_edit.setText(self._entry.task)
        self._category_edit.setText(self._entry.category or "")
        if self._category_suggestions:
            completer = QCompleter(sorted(self._category_suggestions, key=str.lower), self)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self._category_edit.setCompleter(completer)
        self._start_edit.setDateTime(QDateTime(self._entry.segment_start))
        self._end_edit.setDateTime(QDateTime(self._entry.segment_end))

    def _collect_times(self) -> tuple[datetime, datetime]:
        return self._start_edit.dateTime().toPython(), self._end_edit.dateTime().toPython()

    def _validate(self) -> bool:
        task = self._task_edit.text().strip()
        category_text = self._category_edit.text().strip()
        start, end = self._collect_times()

        self._minutes_label.setText(self._format_minutes(minutes_between(start, end)))

        message = ""
        conflicts: list[Entry] = []
        valid = True

        if not task:
            message = "Task is required."
            valid = False
        elif start >= end:
            message = "Start must be before end."
            valid = False
        else:
            try:
                conflicts = self._detect_conflicts(start, end)
            except PersistenceError as exc:
                LOGGER.exception("Failed to load entries for conflict check")
                message = str(exc)
                valid = False
            else:
                if conflicts:
                    message = "Conflicts detected:\n" + "\n".join(self._format_conflict_line(entry) for entry in conflicts)
                    valid = False

        self._current_conflicts = conflicts
        self._save_button.setEnabled(valid)
        self._status_label.setText(message)

        if valid:
            self._result_task = task
            self._result_category = category_text or None
            self._result_start = start
            self._result_end = end

        return valid

    def _detect_conflicts(self, start: datetime, end: datetime) -> list[Entry]:
        overlaps = self._repository.get_entries_overlapping(start, end)
        return [entry for entry in overlaps if entry.entry_id != self._entry.entry_id]

    def _format_conflict_line(self, entry: Entry) -> str:
        category = entry.category or "No category"
        start = entry.segment_start.strftime("%Y-%m-%d %H:%M")
        end = entry.segment_end.strftime("%Y-%m-%d %H:%M")
        return f"- {entry.task} ({category}) {start} - {end} [{entry.minutes}m]"

    def _format_minutes(self, minutes: int) -> str:
        if minutes == 1:
            return "1 minute"
        if minutes < 1:
            return "0 minutes"
        return f"{minutes} minutes"

    def _on_accept(self) -> None:
        if not self._validate():
            return
        try:
            updated = self._repository.update_entry(
                self._entry.entry_id,
                task=self._result_task,
                segment_start=self._result_start,
                segment_end=self._result_end,
                category=self._result_category,
            )
        except SegmentConflictError as exc:
            LOGGER.warning("Conflict prevented entry update")
            self._status_label.setText(str(exc))
            self._save_button.setEnabled(False)
            return
        except (PersistenceError, ValueError) as exc:
            LOGGER.exception("Failed to update entry")
            self._status_label.setText(str(exc))
            self._save_button.setEnabled(False)
            return

        self._updated_entry = updated
        self.accept()
