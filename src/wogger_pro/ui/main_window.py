"""Main dashboard window for Wogger Pro."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStatusBar,
    QTableView,
    QDialog,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.exceptions import PersistenceError
from ..core.models import Entry, TaskSummary
from ..core.prompt_manager import PromptManager
from ..core.repository import EntriesRepository
from .date_range_dialog import DateRangeDialog
from .icons import (
    add_palette_listener,
    app_icon,
    calendar_icon,
    plus_icon,
    remove_palette_listener,
    settings_icon,
)
from .prompt_service import PromptService
from .task_totals_model import TaskTotalsModel
from .task_edit_dialog import TaskEditDialog

LOGGER = logging.getLogger("wogger.ui.main")


class FilterMode(Enum):
    TODAY = "today"
    ALL = "all"
    RANGE = "range"


@dataclass(slots=True)
class FilterState:
    mode: FilterMode
    start: Optional[datetime] = None
    end: Optional[datetime] = None


class MainWindow(QMainWindow):
    settings_requested = Signal()
    manual_entry_requested = Signal()

    def __init__(
        self,
        repository: EntriesRepository,
        prompt_manager: PromptManager,
        prompt_service: PromptService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Wogger (Pro)")
        self.resize(700, 500)
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)

        self._repository = repository
        self._prompt_manager = prompt_manager
        self._prompt_service = prompt_service  # keep reference
        self._filter_state = FilterState(FilterMode.TODAY)

        self._model = TaskTotalsModel()

        self._build_ui()
        self._connect_signals()
        self._palette_listener = self._refresh_icons
        add_palette_listener(self._palette_listener)
        self._refresh_icons()
        self._refresh_totals()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        controls_bar = self._build_controls_bar()
        layout.addWidget(controls_bar)

        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        vheader = self._table.verticalHeader()
        vheader.setFixedWidth(12)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self._table, 1)

        self.setCentralWidget(central)

        status = QStatusBar(self)
        self.setStatusBar(status)
        self._summary_label = QLabel("", self)
        self._summary_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._summary_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        status.addPermanentWidget(self._summary_label)
        self._update_status_bar()

    def _build_controls_bar(self) -> QWidget:
        container = QWidget(self)
        container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        left_layout = QHBoxLayout()
        left_layout.setSpacing(8)

        self._today_button = QToolButton(container)
        self._today_button.setText("Today")
        self._today_button.setCheckable(True)

        self._all_button = QToolButton(container)
        self._all_button.setText("All time")
        self._all_button.setCheckable(True)

        self._button_group = QButtonGroup(container)
        self._button_group.setExclusive(True)
        self._button_group.addButton(self._today_button, id=1)
        self._button_group.addButton(self._all_button, id=2)

        self._today_button.setChecked(True)

        self._calendar_button = QToolButton(container)
        self._calendar_button.setAutoRaise(True)
        self._calendar_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._calendar_button.setIconSize(QSize(22, 22))
        self._calendar_button.setToolTip("Select date range…")

        left_layout.addWidget(self._today_button)
        left_layout.addWidget(self._all_button)
        left_layout.addWidget(self._calendar_button)

        right_layout = QHBoxLayout()
        right_layout.setSpacing(8)

        self._manual_entry_button = QToolButton(container)
        self._manual_entry_button.setToolTip("Add a manual custom time segment")
        self._manual_entry_button.setAutoRaise(True)
        self._manual_entry_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._manual_entry_button.setIconSize(QSize(24, 24))

        self._settings_button = QToolButton(container)
        self._settings_button.setAutoRaise(True)
        self._settings_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._settings_button.setIconSize(QSize(22, 22))
        self._settings_button.setToolTip("Settings")

        right_layout.addWidget(self._manual_entry_button)
        right_layout.addWidget(self._settings_button)

        layout.addLayout(left_layout)
        layout.addStretch(1)
        layout.addLayout(right_layout)
        return container

    def _connect_signals(self) -> None:
        self._button_group.idToggled.connect(self._on_filter_toggled)
        self._calendar_button.clicked.connect(self._open_range_dialog)
        self._settings_button.clicked.connect(lambda: self.settings_requested.emit())
        self._manual_entry_button.clicked.connect(self._open_manual_entry_dialog)

        self._prompt_manager.segment_completed.connect(lambda _segment_id, _entry: self._refresh_totals())
        self._prompt_manager.segment_split.connect(lambda _segment_id, _entries: self._refresh_totals())
        self._prompt_manager.manual_entry_saved.connect(lambda _entry: self._refresh_totals())
        self._prompt_manager.entries_replaced.connect(self._refresh_totals)

    # ------------------------------------------------------------------
    def _on_filter_toggled(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        if button_id == 1:
            self._filter_state = FilterState(FilterMode.TODAY)
        elif button_id == 2:
            self._filter_state = FilterState(FilterMode.ALL)
        self._refresh_totals()

    def _open_range_dialog(self) -> None:
        start = self._filter_state.start
        end = self._filter_state.end
        dialog = DateRangeDialog(start=start, end=end, parent=self)
        if dialog.exec() == QDialog.Accepted:
            start_dt, end_dt = dialog.selected_range()
            self._filter_state = FilterState(FilterMode.RANGE, start=start_dt, end=end_dt)
            self._today_button.setChecked(False)
            self._all_button.setChecked(False)
            self._refresh_totals()

    def _open_manual_entry_dialog(self) -> None:
        self.manual_entry_requested.emit()
        self._prompt_service.show_manual_entry_dialog(self)

    def _refresh_totals(self) -> None:
        entries: list[Entry]
        if self._filter_state.mode == FilterMode.TODAY:
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1) - timedelta(seconds=1)
            entries = self._repository.get_entries_by_range(start, end)
        elif self._filter_state.mode == FilterMode.ALL:
            entries = self._repository.get_all_entries()
        else:
            if not self._filter_state.start or not self._filter_state.end:
                QMessageBox.warning(self, "Invalid range", "Please select a valid start and end time.")
                return
            entries = self._repository.get_entries_by_range(self._filter_state.start, self._filter_state.end)

        summaries = self._build_summaries(entries)
        self._model.update_rows(summaries)
        self._update_summary_totals(summaries)
        self._update_status_bar()
        self._log_filter_change(len(entries))

    def _build_summaries(self, entries: list[Entry]) -> list[TaskSummary]:
        totals: dict[str, int] = {}
        for entry in entries:
            totals[entry.task] = totals.get(entry.task, 0) + entry.minutes
        summaries = [TaskSummary(task=name, total_minutes=minutes) for name, minutes in totals.items()]
        summaries.sort(key=lambda summary: (-summary.total_minutes, summary.task.lower()))
        return summaries

    # ------------------------------------------------------------------
    def update_repository(self, repository: EntriesRepository) -> None:
        self._repository = repository
        self._refresh_totals()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        remove_palette_listener(self._palette_listener)
        super().closeEvent(event)

    def _refresh_icons(self) -> None:
        button = getattr(self, "_manual_entry_button", None)
        if button is None:
            return
        icon_targets = [
            ("_manual_entry_button", plus_icon, 24),
            ("_calendar_button", calendar_icon, 22),
            ("_settings_button", settings_icon, 22),
        ]
        for attr, factory, fallback_size in icon_targets:
            btn = getattr(self, attr, None)
            if btn is None:
                continue
            size = btn.iconSize()
            dimension = max(size.width(), size.height(), fallback_size)
            btn.setIcon(factory(dimension))

    def _update_status_bar(self) -> None:
        if self._filter_state.mode == FilterMode.TODAY:
            text = "Filter: Today"
        elif self._filter_state.mode == FilterMode.ALL:
            text = "Filter: All time"
        else:
            start = self._filter_state.start.strftime("%Y-%m-%d %H:%M") if self._filter_state.start else "?"
            end = self._filter_state.end.strftime("%Y-%m-%d %H:%M") if self._filter_state.end else "?"
            text = f"Filter: {start} → {end}"
        self.statusBar().showMessage(text)

    def _log_filter_change(self, entry_count: int) -> None:
        payload = {
            "event": "dashboard_filter_applied",
            "mode": self._filter_state.mode.value,
            "entry_count": entry_count,
        }
        if self._filter_state.start:
            payload["start"] = self._filter_state.start.isoformat()
        if self._filter_state.end:
            payload["end"] = self._filter_state.end.isoformat()
        LOGGER.info("Dashboard filter applied", extra=payload)

    def _update_summary_totals(self, summaries: list[TaskSummary]) -> None:
        total_minutes = sum(item.total_minutes for item in summaries)
        pretty_total = TaskSummary(task="", total_minutes=total_minutes).pretty_total
        self._summary_label.setText(f"Total: {total_minutes} min • {pretty_total}")

    def _on_row_double_clicked(self, index) -> None:
        if not index.isValid():
            return
        summary = self._model.summary_for_row(index.row())
        if summary is None:
            return

        dialog = TaskEditDialog(summary.task, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        new_name = dialog.new_name
        if new_name == summary.task:
            return

        try:
            updated = self._prompt_manager.rename_task(summary.task, new_name)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid task name", str(exc))
            return
        except PersistenceError as exc:
            QMessageBox.critical(self, "Rename failed", str(exc))
            return

        if updated <= 0:
            return

        self._refresh_totals()
        status = self.statusBar()
        status.showMessage(f"Renamed '{summary.task}' to '{new_name}' ({updated} entries)", 5000)
        QTimer.singleShot(5100, self._update_status_bar)
