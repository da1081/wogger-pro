"""Shared task suggestion widgets."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox


@dataclass(slots=True)
class TaskSuggestion:
    task: str
    count: int


class SuggestionComboBox(QComboBox):
    popup_about_to_show = Signal()

    def showPopup(self) -> None:  # type: ignore[override]
        self.popup_about_to_show.emit()
        super().showPopup()
