"""Dialog for selecting a date/time range."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QWidget,
)

from .icons import app_icon


class DateRangeDialog(QDialog):
    def __init__(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select date and time range")
        self.setModal(True)
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)

        layout = QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        now = datetime.now()
        self._start_edit = QDateTimeEdit(self)
        self._start_edit.setCalendarPopup(True)
        self._start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._start_edit.setDateTime(QDateTime.fromSecsSinceEpoch(int((start or now).timestamp())))

        self._end_edit = QDateTimeEdit(self)
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._end_edit.setDateTime(QDateTime.fromSecsSinceEpoch(int((end or now).timestamp())))

        layout.addRow("Start", self._start_edit)
        layout.addRow("End", self._end_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_range(self) -> tuple[datetime, datetime]:
        start_dt = self._start_edit.dateTime().toPython()
        end_dt = self._end_edit.dateTime().toPython()
        return start_dt, end_dt

    def _validate_and_accept(self) -> None:
        start_dt, end_dt = self.selected_range()
        if start_dt > end_dt:
            self._end_edit.setDateTime(self._start_edit.dateTime())
            return
        self.accept()
