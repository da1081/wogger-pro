"""Dedicated dialog for resolving missing timeslots."""

from __future__ import annotations

from datetime import timedelta
from typing import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Entry, ScheduledSegment
from .icons import app_icon
from .task_inputs import SuggestionComboBox, TaskSuggestion


def _format_minutes(value: int) -> str:
    hours, minutes = divmod(max(0, value), 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _format_entry(entry: Entry) -> str:
    start_day = entry.segment_start.strftime("%Y-%m-%d")
    start_time = entry.segment_start.strftime("%H:%M")
    end_day = entry.segment_end.strftime("%Y-%m-%d")
    end_time = entry.segment_end.strftime("%H:%M")
    pretty = _format_minutes(entry.minutes)
    if start_day == end_day:
        range_text = f"{start_day} {start_time} – {end_time}"
    else:
        range_text = f"{start_day} {start_time} → {end_day} {end_time}"
    task_text = entry.task.strip() or "(unnamed task)"
    return f"{range_text} • {pretty} • {task_text}"


class MissingTimeslotDialog(QDialog):
    submitted = Signal(str, int)
    dismissed = Signal(str)

    def __init__(
        self,
        segment: ScheduledSegment,
        *,
        before_entries: Sequence[Entry],
        after_entries: Sequence[Entry],
        task_suggestions: Sequence[TaskSuggestion],
        task_suggestions_loader: Callable[[], Sequence[TaskSuggestion]] | None = None,
        default_task: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fix missing timeslot")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setSizeGripEnabled(True)

        self._segment = segment
        self._before_entries = list(before_entries)
        self._after_entries = list(after_entries)
        self._task_suggestions = list(task_suggestions)
        self._task_suggestions_loader = task_suggestions_loader
        self._default_task = (default_task or "").strip()
        self._selected_minutes = max(1, segment.minutes)
        self._dismiss_reason = "closed"
        self._busy = False
        self._completed = False

        self._build_ui()
        self._populate_tasks()
        self._apply_default_task()
        self._update_selection_label()
        self._apply_initial_size(parent)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel(self._build_header_text(), self)
        header.setWordWrap(True)
        layout.addWidget(header)

        before_section = self._create_context_section("Before this gap", self._before_entries)
        if before_section is not None:
            layout.addWidget(before_section)

        task_label = QLabel("Task", self)
        layout.addWidget(task_label)

        self._task_combo = SuggestionComboBox(self)
        self._task_combo.setEditable(True)
        self._task_combo.setInsertPolicy(SuggestionComboBox.InsertPolicy.NoInsert)
        self._task_combo.editTextChanged.connect(self._update_primary_button_state)
        self._task_combo.currentTextChanged.connect(self._update_primary_button_state)
        self._task_combo.popup_about_to_show.connect(self._refresh_task_suggestions)
        layout.addWidget(self._task_combo)

        after_section = self._create_context_section("After this gap", self._after_entries)
        if after_section is not None:
            layout.addWidget(after_section)

        self._selection_label = QLabel("", self)
        self._selection_label.setWordWrap(True)
        layout.addWidget(self._selection_label)

        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.setSpacing(8)

        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setRange(1, max(1, self._segment.minutes))
        self._slider.setValue(max(1, self._segment.minutes))
        self._slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self._slider)

        self._minutes_label = QLabel(_format_minutes(self._slider.value()), self)
        slider_row.addWidget(self._minutes_label)

        slider_container = QWidget(self)
        slider_container.setLayout(slider_row)
        layout.addWidget(slider_container)

        hint = QLabel(
            "Adjust the slider to log only part of the gap. You can reopen the gap to log the rest.",
            self,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._button_box = QDialogButtonBox(self)
        self._primary_button = QPushButton("Log entry", self)
        self._primary_button.setDefault(True)
        self._button_box.addButton(self._primary_button, QDialogButtonBox.AcceptRole)

        self._cancel_button = QPushButton("Cancel", self)
        self._button_box.addButton(self._cancel_button, QDialogButtonBox.RejectRole)

        self._primary_button.clicked.connect(self._on_submit_clicked)
        self._cancel_button.clicked.connect(self._on_cancel_clicked)

        layout.addWidget(self._button_box)

        self.setMinimumWidth(530)

    def _create_context_section(self, title: str, entries: Sequence[Entry]) -> QWidget | None:
        if not entries:
            return None
        frame = QFrame(self)
        frame.setFrameShape(QFrame.NoFrame)
        section_layout = QVBoxLayout(frame)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(4)

        header = QLabel(title, frame)
        header.setStyleSheet("font-weight: bold;")
        section_layout.addWidget(header)

        for entry in entries:
            label = QLabel(_format_entry(entry), frame)
            label.setWordWrap(True)
            section_layout.addWidget(label)

        return frame

    def _build_header_text(self) -> str:
        start = self._segment.segment_start.strftime("%Y-%m-%d %H:%M")
        end = self._segment.segment_end.strftime("%Y-%m-%d %H:%M")
        return f"Missing segment from {start} → {end}"

    def _populate_tasks(self) -> None:
        suggestions = self._deduplicated_suggestions()
        self._task_combo.blockSignals(True)
        self._task_combo.clear()
        for suggestion in suggestions:
            self._task_combo.addItem(suggestion.task, suggestion.task)
            if suggestion.count:
                tooltip = f"Previously used {suggestion.count} time"
                if suggestion.count != 1:
                    tooltip += "s"
                index = self._task_combo.count() - 1
                self._task_combo.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)
        self._task_combo.blockSignals(False)
        self._update_primary_button_state()

    def _apply_default_task(self) -> None:
        preset = self._default_task
        if not preset and self._task_suggestions:
            preset = self._task_suggestions[0].task
        if preset:
            self._task_combo.setEditText(preset)

    def _deduplicated_suggestions(self) -> list[TaskSuggestion]:
        seen: set[str] = set()
        unique: list[TaskSuggestion] = []
        for suggestion in self._task_suggestions:
            key = suggestion.task.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(suggestion)
        unique.sort(key=lambda item: (-item.count, item.task.lower()))
        return unique

    def _refresh_task_suggestions(self) -> None:
        if self._task_suggestions_loader is None or self._busy:
            return
        try:
            suggestions = list(self._task_suggestions_loader())
        except Exception:  # pragma: no cover - defensive
            return
        self._task_suggestions = suggestions
        self._populate_tasks()

    def _on_slider_changed(self, value: int) -> None:
        self._selected_minutes = max(1, value)
        self._minutes_label.setText(_format_minutes(self._selected_minutes))
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        minutes = max(1, self._slider.value())
        end_time = self._segment.segment_start + timedelta(minutes=minutes)
        start_text = self._segment.segment_start.strftime("%Y-%m-%d %H:%M")
        same_day = self._segment.segment_start.date() == end_time.date()
        if same_day:
            end_label = end_time.strftime("%H:%M")
        else:
            end_label = end_time.strftime("%Y-%m-%d %H:%M")
        pretty = _format_minutes(minutes)
        self._selection_label.setText(f"Logging: {start_text} – {end_label} • {pretty}")

    def _apply_initial_size(self, parent: QWidget | None) -> None:
        min_width = 620
        target_width = max(min_width, 780)
        if parent is not None:
            target_width = max(min_width, parent.width())
        height_hint = self.sizeHint().height()
        if height_hint <= 0:
            height_hint = max(self.height(), 420)
        self.resize(target_width, height_hint)

    def _combo_value(self) -> str:
        line_edit = self._task_combo.lineEdit()
        if line_edit is not None:
            text = line_edit.text().strip()
            if text:
                return text
        text = self._task_combo.currentText().strip()
        if text:
            return text
        data = self._task_combo.currentData()
        if isinstance(data, str):
            return data.strip()
        return ""

    def _on_submit_clicked(self) -> None:
        task = self._combo_value()
        if not task:
            QMessageBox.warning(self, "Task required", "Enter or select a task before logging the gap.")
            return
        self.set_busy(True)
        self.submitted.emit(task, max(1, self._slider.value()))

    def _on_cancel_clicked(self) -> None:
        self._dismiss_reason = "canceled"
        self.reject()

    def _update_primary_button_state(self, _text: str | None = None) -> None:
        self._primary_button.setText("Log entry")

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._primary_button.setEnabled(not busy)
        self._cancel_button.setEnabled(not busy)
        self._task_combo.setEnabled(not busy)
        self._slider.setEnabled(not busy)

    def notify_success(self) -> None:
        self._completed = True
        self.set_busy(False)
        self.accept()

    def notify_failure(self) -> None:
        self.set_busy(False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._completed:
            self.dismissed.emit(self._dismiss_reason)
        super().closeEvent(event)