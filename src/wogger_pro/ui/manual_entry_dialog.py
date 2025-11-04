"""Manual work-log entry dialog."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Sequence

from PySide6.QtCore import QDateTime, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.exceptions import PersistenceError, SegmentConflictError
from ..core.prompt_manager import PromptManager
from ..core.time_segments import minutes_between
from .task_inputs import SuggestionComboBox, TaskSuggestion


class WatchingDateTimeEdit(QDateTimeEdit):
    focus_in = Signal()

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        self.focus_in.emit()
        super().focusInEvent(event)


class ManualEntryDialog(QDialog):
    def __init__(
        self,
        manager: PromptManager,
        task_suggestions: list[TaskSuggestion],
        task_suggestions_loader: Callable[[], Sequence[TaskSuggestion]] | None,
        default_task: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Log manual work segment")
        self.setModal(True)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._manager = manager
        self._task_suggestions = list(task_suggestions)
        self._task_suggestions_loader = task_suggestions_loader
        self._default_task = default_task or ""

        self._current_time_timer = QTimer(self)
        self._current_time_timer.setInterval(60_000)
        self._current_time_timer.timeout.connect(self._apply_current_time)

        self._build_ui()
        self._apply_defaults()
        self._validate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        description = QLabel("Add a custom segment outside the scheduler")
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QFormLayout()
        form.setSpacing(10)

        self._task_combo = SuggestionComboBox(self)
        self._task_combo.setEditable(True)
        self._task_combo.setInsertPolicy(SuggestionComboBox.InsertPolicy.NoInsert)
        self._task_combo.currentTextChanged.connect(self._validate)
        self._task_combo.editTextChanged.connect(self._validate)
        self._task_combo.popup_about_to_show.connect(self._refresh_task_suggestions)
        form.addRow("Task", self._task_combo)

        self._start_edit = QDateTimeEdit(self)
        self._start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._start_edit.setCalendarPopup(True)
        self._start_edit.dateTimeChanged.connect(self._on_start_changed)

        start_row = QHBoxLayout()
        start_row.setContentsMargins(0, 0, 0, 0)
        start_row.setSpacing(8)
        start_row.addWidget(self._start_edit, 1)

        start_container = QWidget(self)
        start_container.setLayout(start_row)
        form.addRow("Start", start_container)

        end_row = QHBoxLayout()
        end_row.setContentsMargins(0, 0, 0, 0)
        end_row.setSpacing(8)

        self._end_edit = WatchingDateTimeEdit(self)
        self._end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._end_edit.setCalendarPopup(True)
        self._end_edit.dateTimeChanged.connect(self._on_end_changed)
        self._end_edit.focus_in.connect(self._on_end_focus)
        end_row.addWidget(self._end_edit, 1)

        self._use_current_button = QPushButton("Use current time", self)
        self._use_current_button.setCheckable(True)
        self._use_current_button.setChecked(True)
        self._use_current_button.setDefault(False)
        self._use_current_button.setAutoDefault(False)
        self._use_current_button.toggled.connect(self._on_use_current_toggled)
        end_row.addWidget(self._use_current_button)

        end_container = QWidget(self)
        end_container.setLayout(end_row)
        form.addRow("End", end_container)

        layout.addLayout(form)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        palette = self._status_label.palette()
        palette.setColor(self._status_label.foregroundRole(), Qt.red)
        self._status_label.setPalette(palette)
        layout.addWidget(self._status_label)

        self._button_box = QDialogButtonBox(self)
        self._log_button = QPushButton("Log Entry", self)
        self._log_button.setDefault(True)
        self._button_box.addButton(self._log_button, QDialogButtonBox.AcceptRole)

        cancel_button = QPushButton("Cancel", self)
        self._button_box.addButton(cancel_button, QDialogButtonBox.RejectRole)

        self._log_button.clicked.connect(self._on_log_clicked)
        cancel_button.clicked.connect(self.reject)

        layout.addWidget(self._button_box)

        self.resize(420, 0)

    def _apply_defaults(self) -> None:
        defaults = self._manager.manual_entry_defaults()
        start, end = defaults
        self._start_edit.setDateTime(QDateTime(start))
        self._end_edit.setDateTime(QDateTime(end))
        self._populate_tasks()
        if self._default_task:
            self._task_combo.setEditText(self._default_task)
        else:
            self._task_combo.setEditText(self._task_suggestions[0].task if self._task_suggestions else "")
        self._apply_current_time()
        self._current_time_timer.start()

    def _populate_tasks(self) -> None:
        self._task_combo.blockSignals(True)
        self._task_combo.clear()
        for suggestion in self._task_suggestions:
            self._task_combo.addItem(suggestion.task, suggestion.task)
        self._task_combo.blockSignals(False)

    def _refresh_task_suggestions(self) -> None:
        if self._task_suggestions_loader is None:
            return
        try:
            suggestions = list(self._task_suggestions_loader())
        except Exception:  # pragma: no cover - defensive
            return
        self._task_suggestions = suggestions
        self._populate_tasks()

    def _on_use_current_toggled(self, checked: bool) -> None:
        if checked:
            self._apply_current_time(reset=True)
            self._current_time_timer.start()
        else:
            self._current_time_timer.stop()
        self._validate()

    def _apply_current_time(self, reset: bool = False) -> None:
        if not self._use_current_button.isChecked() and not reset:
            return
        now = datetime.now().replace(second=0, microsecond=0)
        self._end_edit.blockSignals(True)
        self._end_edit.setDateTime(QDateTime(now))
        self._end_edit.blockSignals(False)
        if reset:
            self._current_time_timer.start()
        self._validate()

    def _on_start_changed(self, _: QDateTime) -> None:
        self._validate()

    def _on_end_changed(self, _: QDateTime) -> None:
        if self._use_current_button.isChecked():
            self._use_current_button.blockSignals(True)
            self._use_current_button.setChecked(False)
            self._use_current_button.blockSignals(False)
            self._current_time_timer.stop()
        self._validate()

    def _on_end_focus(self) -> None:
        if self._use_current_button.isChecked():
            self._use_current_button.setChecked(False)

    def _combo_value(self) -> str:
        text = self._task_combo.currentText().strip()
        if text:
            return text
        data = self._task_combo.currentData()
        if isinstance(data, str):
            return data.strip()
        return ""

    def _collect_times(self) -> tuple[datetime, datetime]:
        return self._start_edit.dateTime().toPython(), self._end_edit.dateTime().toPython()

    def _validate(self) -> bool:
        task = self._combo_value()
        start, end = self._collect_times()
        message = ""
        valid = True

        minutes = minutes_between(start, end)
        if not task:
            message = "Task is required."
            valid = False
        elif start >= end:
            message = "Start must be before end."
            valid = False
        elif minutes < 1:
            message = "Duration must be at least one minute."
            valid = False
        else:
            conflicts = self._manager.range_conflicts(start, end)
            if conflicts:
                conflict = conflicts[0]
                conflict_start = conflict.conflicting.start.strftime("%Y-%m-%d %H:%M")
                conflict_end = conflict.conflicting.end.strftime("%Y-%m-%d %H:%M")
                message = f"Conflicts with existing entry from {conflict_start} to {conflict_end}."
                valid = False

        self._log_button.setEnabled(valid)
        self._status_label.setText(message)
        return valid

    def _on_log_clicked(self) -> None:
        if not self._validate():
            return
        task = self._combo_value()
        start, end = self._collect_times()

        try:
            self._manager.record_manual_entry(task, start, end)
        except SegmentConflictError as exc:
            QMessageBox.warning(self, "Overlap detected", str(exc))
            self._status_label.setText(str(exc))
            self._log_button.setEnabled(False)
            return
        except PersistenceError as exc:
            QMessageBox.critical(self, "Unable to save", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid duration", str(exc))
            return

        self.accept()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._current_time_timer.stop()
        super().closeEvent(event)