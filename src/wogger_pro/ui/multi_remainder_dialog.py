"""Dialog for logging multiple remainder segments at once."""

from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.time_segments import TimeRange
from .icons import add_palette_listener, remove_palette_listener, trash_icon
from .task_inputs import SuggestionComboBox, TaskSuggestion


def format_range_label(range_: TimeRange) -> str:
    start = range_.start.strftime("%H:%M")
    end = range_.end.strftime("%H:%M")
    minutes = max(1, int((range_.end - range_.start).total_seconds() // 60))
    return f"{start} – {end} ({minutes}m)"


class MultiRemainderDialog(QDialog):
    def __init__(
        self,
        remainders: Sequence[TimeRange],
        task_suggestions: Sequence[TaskSuggestion],
        task_suggestions_loader: Callable[[], Sequence[TaskSuggestion]] | None,
        default_task: str | None = None,
        parent: QWidget | None = None,
        intro_text: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Log remaining segments")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._remainders = list(remainders)
        self._task_suggestions = list(task_suggestions)
        self._task_suggestions_loader = task_suggestions_loader
        self._default_task = (default_task or "").strip()
        self._assignments: list[str] = []
        self._selected_remainders: list[TimeRange] = []
        self._intro_text = intro_text or (
            "Assign a task to each remaining slice. All entries will be saved together."
        )

        self._combo_boxes: list[SuggestionComboBox] = []
        self._row_labels: list[QLabel] = []
        self._row_buttons: list[QToolButton] = []
        self._disabled_rows: set[int] = set()
        self._active_combo_index: int = 0

        self._build_ui()
        self._populate_tasks()
        self._apply_defaults()
        self._validate()
        self._palette_listener = self._refresh_icons
        add_palette_listener(self._palette_listener)
        self._refresh_icons()

    @property
    def assignments(self) -> list[str]:
        return self._assignments

    @property
    def selected_remainders(self) -> list[TimeRange]:
        return self._selected_remainders

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        intro = QLabel(self._intro_text)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._apply_last_button = QPushButton("Apply last task to all", self)
        self._apply_last_button.clicked.connect(self._apply_last_task)
        controls.addWidget(self._apply_last_button)

        self._fill_down_button = QPushButton("Fill down", self)
        self._fill_down_button.clicked.connect(self._fill_down)
        self._fill_down_button.setToolTip("Copy the current task into all later segments.")
        controls.addWidget(self._fill_down_button)

        controls.addStretch(1)
        layout.addLayout(controls)

        grid = QGridLayout()
        grid.setSpacing(8)

        for row, remainder in enumerate(self._remainders):
            label = QLabel(format_range_label(remainder), self)
            label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            grid.addWidget(label, row, 0)
            self._row_labels.append(label)

            combo = SuggestionComboBox(self)
            combo.setEditable(True)
            combo.setInsertPolicy(SuggestionComboBox.InsertPolicy.NoInsert)
            combo.editTextChanged.connect(self._on_combo_text_changed)
            combo.currentTextChanged.connect(self._on_combo_text_changed)
            combo.popup_about_to_show.connect(self._refresh_task_suggestions)
            combo.installEventFilter(self)
            self._combo_boxes.append(combo)
            grid.addWidget(combo, row, 1)

            trash_button = QToolButton(self)
            trash_button.setCheckable(True)
            trash_button.setAutoRaise(True)
            trash_button.setToolTip("Exclude this segment from saving")
            trash_button.setIconSize(QSize(18, 18))
            trash_button.clicked.connect(lambda checked, index=row: self._toggle_row_disabled(index, checked))
            grid.addWidget(trash_button, row, 2)
            self._row_buttons.append(trash_button)

        layout.addLayout(grid)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        palette = self._status_label.palette()
        palette.setColor(self._status_label.foregroundRole(), Qt.red)
        self._status_label.setPalette(palette)
        layout.addWidget(self._status_label)

        self._button_box = QDialogButtonBox(self)
        self._primary_button = QPushButton(f"Log {len(self._remainders)} subsegments", self)
        self._primary_button.setDefault(True)
        self._button_box.addButton(self._primary_button, QDialogButtonBox.AcceptRole)

        cancel_button = QPushButton("Cancel", self)
        self._button_box.addButton(cancel_button, QDialogButtonBox.RejectRole)

        self._primary_button.clicked.connect(self._on_accept)
        cancel_button.clicked.connect(self.reject)

        layout.addWidget(self._button_box)

        self.resize(520, 0)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        remove_palette_listener(self._palette_listener)
        super().closeEvent(event)

    def _refresh_icons(self) -> None:
        for button in self._row_buttons:
            size = button.iconSize()
            dimension = max(size.width(), size.height(), 18)
            button.setIcon(trash_icon(dimension))

    def _populate_tasks(self) -> None:
        for combo in self._combo_boxes:
            combo.blockSignals(True)
            current_text = ""
            line_edit = combo.lineEdit()
            if line_edit is not None:
                current_text = line_edit.text()
            if not current_text:
                current_text = combo.currentText()
            combo.clear()
            for suggestion in self._task_suggestions:
                combo.addItem(suggestion.task, suggestion.task)
            if current_text:
                combo.setEditText(current_text)
            combo.blockSignals(False)

    def _refresh_task_suggestions(self) -> None:
        if self._task_suggestions_loader is None:
            return
        try:
            suggestions = list(self._task_suggestions_loader())
        except Exception:  # pragma: no cover - defensive
            return
        self._task_suggestions = suggestions
        self._populate_tasks()

    def _apply_defaults(self) -> None:
        if not self._combo_boxes:
            return
        preset = self._default_task or (self._task_suggestions[0].task if self._task_suggestions else "")
        if preset:
            for combo in self._combo_boxes:
                combo.setEditText(preset)

    def _apply_last_task(self) -> None:
        preset = self._default_task or ""
        if not preset:
            if self._task_suggestions:
                preset = self._task_suggestions[0].task
            else:
                preset = ""
        for combo in self._combo_boxes:
            combo.setEditText(preset)
        self._validate()

    def _fill_down(self) -> None:
        if not self._combo_boxes:
            return

        base_index = self._resolve_base_fill_index()
        if base_index is None:
            self._status_label.setText("No available segment to fill.")
            self._primary_button.setEnabled(False)
            return

        base_combo = self._combo_boxes[base_index]
        value = self._combo_value(base_combo)

        if not value:
            self._status_label.setText("Enter a task before using Fill Down.")
            self._validate()
            return

        self._status_label.setText("")

        for index in self._active_indices():
            if index > base_index:
                self._combo_boxes[index].setEditText(value)

        self._validate()

    def _collect_tasks(self) -> list[str]:
        values: list[str] = []
        for combo in self._combo_boxes:
            values.append(self._combo_value(combo))
        return values

    def _validate(self) -> None:
        values = self._collect_tasks()
        active_indices = self._active_indices()

        if not active_indices:
            self._status_label.setText("At least one segment must remain selected.")
            self._primary_button.setEnabled(False)
            self._update_primary_button_label()
            return

        missing = [index for index in active_indices if not values[index]]
        if missing:
            self._status_label.setText("Every remaining subsegment must have a task.")
            self._primary_button.setEnabled(False)
            self._update_primary_button_label()
            return
        self._status_label.setText("")
        self._primary_button.setEnabled(True)
        self._update_primary_button_label()

    def _on_accept(self) -> None:
        values = self._collect_tasks()
        active_indices = self._active_indices()
        if not active_indices:
            self._status_label.setText("At least one segment must remain selected.")
            self._primary_button.setEnabled(False)
            return

        missing = [index for index in active_indices if not values[index]]
        if missing:
            self._status_label.setText("Every remaining subsegment must have a task.")
            self._primary_button.setEnabled(False)
            return

        self._assignments = [values[index] for index in active_indices]
        self._selected_remainders = [self._remainders[index] for index in active_indices]
        self.accept()

    def _combo_value(self, combo: SuggestionComboBox) -> str:
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

    def _on_combo_text_changed(self, _text: str) -> None:
        sender = self.sender()
        if isinstance(sender, SuggestionComboBox) and sender in self._combo_boxes:
            self._active_combo_index = self._combo_boxes.index(sender)
        self._validate()

    def eventFilter(self, watched, event):  # type: ignore[override]
        if event.type() == QEvent.FocusIn and isinstance(watched, SuggestionComboBox):
            try:
                self._active_combo_index = self._combo_boxes.index(watched)
            except ValueError:
                pass
        return super().eventFilter(watched, event)

    def _update_primary_button_label(self) -> None:
        count = len(self._active_indices())
        if count == 1:
            label = "Log 1 subsegment"
        else:
            label = f"Log {count} subsegments"
        self._primary_button.setText(label)

    def _active_indices(self) -> list[int]:
        return [index for index in range(len(self._combo_boxes)) if index not in self._disabled_rows]

    def _resolve_base_fill_index(self) -> int | None:
        active = self._active_indices()
        if not active:
            return None
        for index in active:
            if index >= self._active_combo_index:
                return index
        return active[-1]

    def _toggle_row_disabled(self, index: int, disabled: bool) -> None:
        if disabled:
            self._disabled_rows.add(index)
        else:
            self._disabled_rows.discard(index)

        combo = self._combo_boxes[index]
        label = self._row_labels[index]
        button = self._row_buttons[index]

        combo.setEnabled(not disabled)
        label.setEnabled(not disabled)
        button.setChecked(disabled)

        if disabled and self._active_combo_index == index:
            next_index = self._resolve_base_fill_index()
            if next_index is not None:
                self._active_combo_index = next_index
        self._validate()
