"""Dialog for resolving task category inconsistencies."""

from __future__ import annotations

from typing import Dict, Sequence, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QGridLayout,
)

from ..core.category_consistency import CategoryConflictSummary

_NO_CATEGORY = object()


class CategoryConflictDialog(QDialog):
    def __init__(
        self,
        conflicts: Sequence[CategoryConflictSummary],
        known_categories: Sequence[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Resolve Category Conflicts")
        self.setModal(True)
        self._conflicts = list(conflicts)
        self._known_categories = sorted(
            {value.strip() for value in known_categories or [] if value and value.strip()},
            key=lambda item: item.lower(),
        )
        self._combos: Dict[str, QComboBox] = {}
        self._build_ui()

    # ------------------------------------------------------------------
    def selected_categories(self) -> dict[str, str | None]:
        selections: dict[str, str | None] = {}
        for conflict in self._conflicts:
            combo = self._combos.get(conflict.task)
            if combo is None:
                continue
            index = combo.currentIndex()
            if index >= 0:
                data = combo.itemData(index)
                if data is _NO_CATEGORY:
                    selections[conflict.task] = None
                    continue
                if isinstance(data, str) and data.strip():
                    selections[conflict.task] = data.strip()
                    continue
            text = combo.currentText().strip()
            selections[conflict.task] = text or None
        return selections

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        intro = QLabel(
            "Some tasks are associated with multiple categories. "
            "Choose a single category for each task to continue.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        container = QWidget(scroll)
        scroll.setWidget(container)

        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        row = 0
        for conflict in self._conflicts:
            row = self._add_conflict_row(grid, row, conflict)

        button_box = QDialogButtonBox(self)
        submit = QPushButton("Submit", self)
        submit.setDefault(True)
        button_box.addButton(submit, QDialogButtonBox.AcceptRole)

        cancel = QPushButton("Cancel", self)
        button_box.addButton(cancel, QDialogButtonBox.RejectRole)

        submit.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        layout.addWidget(button_box)
        self.resize(520, min(480, 120 + 68 * max(1, len(self._conflicts))))

    def _add_conflict_row(self, grid: QGridLayout, row: int, conflict: CategoryConflictSummary) -> int:
        task_label = QLabel(conflict.task, self)
        task_label.setWordWrap(True)
        task_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(task_label, row, 0)

        combo = QComboBox(self)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("Type to enter a new category")
        self._populate_combo(combo, conflict)
        self._combos[conflict.task] = combo
        grid.addWidget(combo, row, 1)

        details = QLabel(self._format_counts(conflict), self)
        details.setWordWrap(True)
        details.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        details.setObjectName("categoryConflictDetails")
        grid.addWidget(details, row + 1, 0, 1, 2)

        return row + 2

    def _populate_combo(self, combo: QComboBox, conflict: CategoryConflictSummary) -> None:
        combo.clear()
        seen: Set[str] = set()

        for category, _ in conflict.ordered_categories():
            if not category:
                continue
            normalized = category.strip()
            if normalized.lower() in seen:
                continue
            seen.add(normalized.lower())
            combo.addItem(category, normalized)

        for category in self._known_categories:
            if category.lower() in seen:
                continue
            seen.add(category.lower())
            combo.addItem(category, category)

        combo.addItem("(No category)", _NO_CATEGORY)

        default = conflict.default_category()
        if default and combo.findData(default.strip()) >= 0:
            combo.setCurrentIndex(combo.findData(default.strip()))
        elif combo.count() > 0:
            none_index = combo.findData(_NO_CATEGORY)
            if none_index >= 0:
                combo.setCurrentIndex(none_index)

    def _format_counts(self, conflict: CategoryConflictSummary) -> str:
        parts: list[str] = []
        for category, count in conflict.ordered_categories():
            label = category if category else "(No category)"
            parts.append(f"{label} ({count})")
        return "Existing assignments: " + ", ".join(parts)
