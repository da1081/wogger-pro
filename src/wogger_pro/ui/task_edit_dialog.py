"""Dialog for renaming a task across all logged entries."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class TaskEditDialog(QDialog):
    def __init__(self, task_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Task")
        self.setModal(True)
        self._original = task_name.strip()
        self._new_name = self._original

        self._build_ui()
        self._input.setFocus()
        self._input.selectAll()
        self._validate(self._original)

    @property
    def new_name(self) -> str:
        return self._new_name

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        message = QLabel(
            f"You are editing the task \"{self._original or 'Unknown'}\". Changes apply to every matching entry.",
            self,
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        self._input = QLineEdit(self)
        self._input.setText(self._original)
        self._input.setPlaceholderText("Task name")
        self._input.textChanged.connect(self._validate)
        self._input.returnPressed.connect(self._on_accept)
        layout.addWidget(self._input)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._save_button = self._buttons.button(QDialogButtonBox.Save)
        self._save_button.setText("Save")

        self.resize(420, 0)

    def _validate(self, text: str) -> None:
        trimmed = text.strip()
        enabled = bool(trimmed) and trimmed != self._original
        self._save_button.setEnabled(enabled)

    def _on_accept(self) -> None:
        trimmed = self._input.text().strip()
        if not trimmed or trimmed == self._original:
            return
        self._new_name = trimmed
        self.accept()
