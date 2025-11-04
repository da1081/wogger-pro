"""Prompt dialog for logging work segments."""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.models import ScheduledSegment, SplitPart
from .icons import app_icon
from .task_inputs import SuggestionComboBox, TaskSuggestion

LOGGER = logging.getLogger("wogger.ui.prompt")


class PromptDialog(QDialog):
    submitted = Signal(str)
    split_saved = Signal(object)
    dismissed = Signal(str)
    split_started = Signal(object)
    split_canceled = Signal()

    def __init__(
        self,
        segment: ScheduledSegment,
        task_suggestions: Sequence[TaskSuggestion],
        task_suggestions_loader: Callable[[], Sequence[TaskSuggestion]] | None = None,
        default_task: str | None = None,
        range_hint: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Log work segment")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._segment = segment
        self._default_task = default_task or ""
        self._baseline_task = ""
        self._split_mode = False
        self._completed = False
        self._dismiss_reason = "closed"
        self._single_mode_width = 0
        self._split_mode_width = 0
        self._single_mode_height = 0
        self._split_mode_height = 0

        self._task_suggestions = list(task_suggestions)
        self._task_suggestions_loader = task_suggestions_loader
        self._busy = False
        self._range_hint = range_hint

        self._build_ui()
        self._populate_tasks()
        self._apply_defaults()
        self._configure_mode_sizes()

        LOGGER.info(
            "Prompt dialog opened",
            extra={
                "event": "prompt_dialog_opened",
                "segment_id": segment.segment_id,
                "start": segment.segment_start.isoformat(),
                "end": segment.segment_end.isoformat(),
                "minutes": segment.minutes,
            },
        )

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        container = QVBoxLayout(self)
        container.setContentsMargins(12, 12, 12, 12)
        container.setSpacing(8)

        self._header_label = QLabel(self._build_header_text())
        self._header_label.setWordWrap(True)
        container.addWidget(self._header_label)

        if self._range_hint:
            self._range_hint_label = QLabel(self._range_hint)
            self._range_hint_label.setWordWrap(True)
            font = self._range_hint_label.font()
            font.setBold(True)
            self._range_hint_label.setFont(font)
            container.addWidget(self._range_hint_label)
        else:
            self._range_hint_label = None

        self._stack = QStackedWidget(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._single_widget = self._build_single_form()
        self._split_widget = self._build_split_form()
        self._stack.addWidget(self._single_widget)
        self._stack.addWidget(self._split_widget)
        container.addWidget(self._stack)

        self._button_box = QDialogButtonBox(self)
        container.addWidget(self._button_box)

        self._primary_button = QPushButton("Continue", self)
        self._primary_button.setDefault(True)
        self._button_box.addButton(self._primary_button, QDialogButtonBox.AcceptRole)

        self._split_button = QPushButton("Split", self)
        self._button_box.addButton(self._split_button, QDialogButtonBox.ActionRole)

        self._cancel_split_button = QPushButton("Cancel Split", self)
        self._button_box.addButton(self._cancel_split_button, QDialogButtonBox.ActionRole)
        self._cancel_split_button.hide()

        self._close_button = QPushButton("Close", self)
        self._button_box.addButton(self._close_button, QDialogButtonBox.RejectRole)

        self._primary_button.clicked.connect(self._on_primary_clicked)
        self._split_button.clicked.connect(self._enter_split_mode)
        self._cancel_split_button.clicked.connect(self._exit_split_mode)
        self._close_button.clicked.connect(self._on_close_clicked)

        box_layout = self._button_box.layout()
        if box_layout is not None:
            box_layout.setContentsMargins(0, 6, 0, 0)
            box_layout.setSpacing(6)

    def _build_single_form(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        self._task_combo = SuggestionComboBox(widget)
        self._task_combo.setEditable(True)
        self._task_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._task_combo.editTextChanged.connect(self._update_primary_label)
        self._task_combo.currentTextChanged.connect(self._update_primary_label)
        self._task_combo.popup_about_to_show.connect(self._on_task_combo_popup)
        # layout.addWidget(QLabel("Task"))
        layout.addWidget(self._task_combo)
        return widget

    def _build_split_form(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)

        # Slider controls
        slider_row = QHBoxLayout()
        slider_row.setSpacing(12)
        self._split_slider = QSlider(Qt.Horizontal, widget)
        self._split_slider.setFocusPolicy(Qt.StrongFocus)
        slider_row.addWidget(self._split_slider)

        self._split_hint_label = QLabel("Split minutes")
        self._split_hint_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        slider_row.addWidget(self._split_hint_label)
        layout.addLayout(slider_row)

        # Spin boxes with two columns
        grid = QGridLayout()
        grid.setSpacing(8)

        self._split_a_minutes = QSpinBox(widget)
        self._split_b_minutes = QSpinBox(widget)
        self._split_a_minutes.setRange(1, max(1, self._segment.minutes - 1))
        self._split_b_minutes.setRange(1, max(1, self._segment.minutes - 1))
        self._split_b_minutes.setReadOnly(True)

        self._split_slider.valueChanged.connect(self._on_split_slider_changed)
        self._split_a_minutes.valueChanged.connect(self._on_split_spin_changed)

        grid.addWidget(QLabel("Segment A minutes"), 0, 0)
        grid.addWidget(self._split_a_minutes, 0, 1)
        grid.addWidget(QLabel("Segment B minutes"), 0, 2)
        grid.addWidget(self._split_b_minutes, 0, 3)

        self._task_combo_a = SuggestionComboBox(widget)
        self._task_combo_a.setEditable(True)
        self._task_combo_a.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._task_combo_a.editTextChanged.connect(self._update_primary_label)
        self._task_combo_a.popup_about_to_show.connect(self._refresh_task_suggestions)

        self._task_combo_b = SuggestionComboBox(widget)
        self._task_combo_b.setEditable(True)
        self._task_combo_b.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._task_combo_b.editTextChanged.connect(self._update_primary_label)
        self._task_combo_b.popup_about_to_show.connect(self._refresh_task_suggestions)

        grid.addWidget(QLabel("Task A"), 1, 0)
        grid.addWidget(self._task_combo_a, 1, 1)
        grid.addWidget(QLabel("Task B"), 1, 2)
        grid.addWidget(self._task_combo_b, 1, 3)

        layout.addLayout(grid)
        return widget

    def _configure_mode_sizes(self) -> None:
        current = self._stack.currentWidget()

        self._stack.setCurrentWidget(self._single_widget)
        self._stack.updateGeometry()
        single_hint = self.sizeHint()

        self._stack.setCurrentWidget(self._split_widget)
        self._stack.updateGeometry()
        split_hint = self.sizeHint()

        base_split_width = max(split_hint.width(), single_hint.width())
        self._split_mode_width = max(base_split_width, 560)
        self._single_mode_width = max(320, self._split_mode_width // 2)
        self._single_mode_height = single_hint.height()
        self._split_mode_height = max(split_hint.height(), self._single_mode_height + 80)

        self._stack.setCurrentWidget(current or self._single_widget)
        self._apply_mode_size(split=False)

    def _apply_mode_size(self, *, split: bool) -> None:
        if self._split_mode_width <= 0 or self._single_mode_width <= 0:
            return
        target_width = self._split_mode_width if split else self._single_mode_width
        target_height = self._split_mode_height if split else self._single_mode_height
        self.setMinimumWidth(target_width)
        self.setMaximumWidth(target_width)
        self.setMinimumHeight(target_height)
        self.resize(target_width, target_height)
        self._stack.updateGeometry()
        self.updateGeometry()

    def _build_header_text(self) -> str:
        start = self._segment.segment_start.strftime("%Y-%m-%d %H:%M")
        end = self._segment.segment_end.strftime("%Y-%m-%d %H:%M")
        return f"{start} → {end} ({self._segment.minutes} minutes)"

    # ------------------------------------------------------------------
    def _deduplicated_suggestions(self) -> list[TaskSuggestion]:
        seen: set[str] = set()
        unique: list[TaskSuggestion] = []
        for suggestion in self._task_suggestions:
            key = suggestion.task.strip().lower()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            unique.append(suggestion)
        unique.sort(key=lambda item: (-item.count, item.task.lower()))
        return unique

    def _populate_tasks(self, preserve_existing: bool = False) -> None:
        combos = (self._task_combo, self._task_combo_a, self._task_combo_b)
        previous_texts = [combo.currentText() for combo in combos] if preserve_existing else None
        suggestions = self._deduplicated_suggestions()

        for index, combo in enumerate(combos):
            combo.blockSignals(True)
            combo.clear()
            for suggestion in suggestions:
                display = suggestion.task
                combo.addItem(display, suggestion.task)
                if suggestion.count:
                    tooltip = f"Previously used {suggestion.count} time"
                    if suggestion.count != 1:
                        tooltip += "s"
                    combo.setItemData(combo.count() - 1, tooltip, Qt.ItemDataRole.ToolTipRole)
            combo.blockSignals(False)
            if previous_texts is not None:
                combo.setEditText(previous_texts[index])

    def _apply_defaults(self) -> None:
        default_task = (self._default_task or "").strip()
        if not default_task and self._task_suggestions:
            default_task = self._task_suggestions[0].task

        self._default_task = default_task
        self._baseline_task = (default_task or "").strip()

        for combo in (self._task_combo, self._task_combo_a, self._task_combo_b):
            combo.blockSignals(True)
            combo.setEditText(default_task)
            combo.blockSignals(False)

        half = self._segment.minutes // 2
        first_half = half
        second_half = self._segment.minutes - half
        if first_half == 0:
            first_half = 1
            second_half = max(1, self._segment.minutes - 1)

        self._split_slider.setRange(1, max(1, self._segment.minutes - 1))
        self._split_slider.setValue(first_half)
        self._split_a_minutes.setValue(first_half)
        self._split_b_minutes.setValue(second_half)
        self._update_primary_label(default_task)

    def _refresh_task_suggestions(self) -> None:
        if self._task_suggestions_loader is None or self._busy:
            return
        try:
            suggestions = list(self._task_suggestions_loader())
        except Exception:  # pragma: no cover - defensive UI refresh
            LOGGER.exception("Failed to refresh task suggestions")
            return

        self._task_suggestions = suggestions
        if not self._default_task and self._task_suggestions:
            self._default_task = self._task_suggestions[0].task
        self._populate_tasks(preserve_existing=True)
        self._update_primary_label(self._task_combo.currentText())

    def _on_task_combo_popup(self) -> None:
        self._refresh_task_suggestions()
        self._update_primary_label(self._task_combo.currentText())

    # ------------------------------------------------------------------
    def _on_primary_clicked(self) -> None:
        if not self._split_mode:
            task_text = self._combo_value(self._task_combo)
            if not task_text:
                QMessageBox.warning(self, "Task required", "Please enter a task description before continuing.")
                return
            self.set_busy(True)
            self.submitted.emit(task_text)
        else:
            parts = self._collect_split_parts()
            if parts is None:
                return
            self.set_busy(True)
            self.split_saved.emit(parts)

    def _collect_split_parts(self) -> list[SplitPart] | None:
        minutes_a = self._split_a_minutes.value()
        minutes_b = self._segment.minutes - minutes_a
        task_a = self._combo_value(self._task_combo_a)
        task_b = self._combo_value(self._task_combo_b)

        if not task_a or not task_b:
            QMessageBox.warning(self, "Task required", "Both split tasks must be provided.")
            return None
        if minutes_a < 1 or minutes_b < 1:
            QMessageBox.warning(self, "Invalid split", "Each split must be at least one minute.")
            return None

        parts = [SplitPart(task=task_a, minutes=minutes_a), SplitPart(task=task_b, minutes=minutes_b)]
        return parts

    def _enter_split_mode(self) -> None:
        if self._split_mode:
            return
        self._split_mode = True
        self._stack.setCurrentWidget(self._split_widget)
        self._stack.updateGeometry()
        self._apply_mode_size(split=True)
        self._split_button.setVisible(False)
        self._cancel_split_button.show()
        self._primary_button.setText("Save Split")
        self.split_started.emit({
            "segment_id": self._segment.segment_id,
            "minutes_total": self._segment.minutes,
        })
        LOGGER.info(
            "Split mode started",
            extra={
                "event": "prompt_split_started",
                "segment_id": self._segment.segment_id,
                "minutes": self._segment.minutes,
                "default_split": self._split_a_minutes.value(),
            },
        )

    def _exit_split_mode(self) -> None:
        if not self._split_mode:
            return
        self._split_mode = False
        self._stack.setCurrentWidget(self._single_widget)
        self._stack.updateGeometry()
        self._apply_mode_size(split=False)
        self._split_button.setVisible(True)
        self._cancel_split_button.hide()
        self._primary_button.setText("Continue")
        self.split_canceled.emit()
        LOGGER.info(
            "Split mode canceled",
            extra={"event": "prompt_split_canceled", "segment_id": self._segment.segment_id},
        )

    def _on_close_clicked(self) -> None:
        self._dismiss_reason = "closed"
        self.reject()

    def _update_primary_label(self, task_text: str) -> None:
        effective_text = (task_text or "").strip()
        if not effective_text:
            effective_text = self._combo_value(self._task_combo)
        if self._split_mode:
            self._primary_button.setText("Save Split")
            return
        baseline = (self._baseline_task or "").strip()
        if effective_text.lower() == baseline.lower():
            self._primary_button.setText("Continue")
        else:
            self._primary_button.setText("Submit")

    def _on_split_slider_changed(self, value: int) -> None:
        self._split_a_minutes.blockSignals(True)
        self._split_a_minutes.setValue(value)
        self._split_a_minutes.blockSignals(False)
        self._split_b_minutes.setValue(self._segment.minutes - value)

    def _on_split_spin_changed(self, value: int) -> None:
        if self._split_slider.value() != value:
            self._split_slider.blockSignals(True)
            self._split_slider.setValue(value)
            self._split_slider.blockSignals(False)
        self._split_b_minutes.setValue(self._segment.minutes - value)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            self._on_primary_clicked()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self._dismiss_reason = "escape"
            self._on_close_clicked()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._completed:
            self.dismissed.emit(self._dismiss_reason)
            LOGGER.info(
                "Prompt dialog closed",
                extra={
                    "event": "prompt_closed",
                    "segment_id": self._segment.segment_id,
                    "reason": self._dismiss_reason,
                },
            )
        super().closeEvent(event)

    def split_distribution(self) -> tuple[int, int]:
        return self._split_a_minutes.value(), self._segment.minutes - self._split_a_minutes.value()

    def notify_success(self) -> None:
        self._completed = True
        self.set_busy(False)
        self.accept()

    def notify_failure(self) -> None:
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._primary_button.setEnabled(not busy)
        self._close_button.setEnabled(not busy)
        if not self._split_mode:
            self._split_button.setEnabled(not busy)
            self._task_combo.setEnabled(not busy)
        else:
            self._cancel_split_button.setEnabled(not busy)
            self._task_combo_a.setEnabled(not busy)
            self._task_combo_b.setEnabled(not busy)
            self._split_slider.setEnabled(not busy)
            self._split_a_minutes.setEnabled(not busy)
            self._split_b_minutes.setEnabled(not busy)

    def _combo_value(self, combo: QComboBox) -> str:
        line_edit = combo.lineEdit()
        if line_edit is not None:
            text = line_edit.text().strip()
            if text:
                return text
        text = combo.currentText().strip()
        if text:
            return text
        data = combo.currentData()
        if isinstance(data, str):
            return data.strip()
        return ""
