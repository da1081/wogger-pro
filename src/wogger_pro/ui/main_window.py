"""Main dashboard window for Wogger Pro."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Callable, Sequence

from PySide6.QtCore import Qt, Signal, QSize, QTimer, QModelIndex
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QTableView,
    QDialog,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.categories import CategoryManager
from ..core.exceptions import PersistenceError
from ..core.models import Entry, TaskSummary
from ..core.missing_timeslots import MissingTimeslot, MissingTimeslotStore, detect_missing_timeslots
from ..core.prompt_manager import PromptManager
from ..core.repository import EntriesRepository
from ..core.settings import Settings
from .date_range_dialog import DateRangeDialog
from .icons import (
    add_palette_listener,
    app_icon,
    calendar_icon,
    plus_icon,
    remove_palette_listener,
    settings_icon,
)
from .category_picker import CategoryTreePicker
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


class CategoryDelegate(QStyledItemDelegate):
    def __init__(
        self,
        category_provider: Callable[[], Sequence[str]],
        apply_callback: Callable[[QModelIndex, str | None, str | None], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._category_provider = category_provider
        self._apply_callback = apply_callback

    def createEditor(self, parent: QWidget, option, index):  # type: ignore[override]
        editor = CategoryTreePicker(parent=parent, auto_popup=True)
        editor.category_changed.connect(lambda _value: self._commit_editor(editor))
        editor.setFocus(Qt.FocusReason.PopupFocusReason)
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:  # type: ignore[override]
        if not isinstance(editor, CategoryTreePicker):
            return
        categories = list(self._category_provider())
        editor.set_categories(categories)
        current = index.data(Qt.EditRole)
        editor.set_current_category((current or "").strip() or None)

    def setModelData(self, editor: QWidget, model, index: QModelIndex) -> None:  # type: ignore[override]
        if not isinstance(editor, CategoryTreePicker):
            return
        new_category = editor.current_category()
        previous = index.data(Qt.EditRole)
        previous_category = (previous or "").strip() or None
        self._apply_callback(index, new_category, previous_category)
        model.setData(index, new_category or "", Qt.EditRole)

    def _commit_editor(self, editor: CategoryTreePicker) -> None:
        self.commitData.emit(editor)
        self.closeEditor.emit(editor, QStyledItemDelegate.NoHint)

    def updateEditorGeometry(self, editor: QWidget, option, index) -> None:  # type: ignore[override]
        editor.setGeometry(option.rect)


class MainWindow(QMainWindow):
    settings_requested = Signal()
    manual_entry_requested = Signal()

    def __init__(
        self,
        repository: EntriesRepository,
        prompt_manager: PromptManager,
        prompt_service: PromptService,
        settings: Settings,
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
        self._settings = settings

        self._missing_store = MissingTimeslotStore()
        self._missing_panel: QWidget | None = None
        self._missing_rows_layout: QVBoxLayout | None = None
        self._missing_count_label: QLabel | None = None
        self._active_missing_timeslots: list[MissingTimeslot] = []
        self._latest_entries: list[Entry] = []

        self._category_manager = CategoryManager()

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
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        vheader = self._table.verticalHeader()
        vheader.setFixedWidth(12)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        self._category_delegate = CategoryDelegate(
            category_provider=self._category_manager.list_categories,
            apply_callback=self._handle_category_commit,
            parent=self._table,
        )
        self._table.setItemDelegateForColumn(1, self._category_delegate)
        layout.addWidget(self._table, 1)
        missing_panel = self._build_missing_panel()
        layout.addWidget(missing_panel)

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

    def _build_missing_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setObjectName("missingTimeslotsPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        title = QLabel("Short gaps detected", panel)
        header_layout.addWidget(title)

        header_layout.addStretch(1)
        self._missing_count_label = QLabel("", panel)
        self._missing_count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header_layout.addWidget(self._missing_count_label)
        layout.addLayout(header_layout)

        description = QLabel(
            "Log a task for each gap or dismiss it if the break was intentional.",
            panel,
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        container = QWidget(panel)
        rows_layout = QVBoxLayout(container)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(6)
        self._missing_rows_layout = rows_layout
        layout.addWidget(container)

        panel.setVisible(False)
        self._missing_panel = panel
        return panel

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

    def _refresh_totals(self, preserve_task: str | None = None) -> None:
        if preserve_task is None:
            preserve_task = self._current_selected_task()
        entries: list[Entry] = []
        if self._filter_state.mode == FilterMode.TODAY:
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1) - timedelta(seconds=1)
            entries = self._repository.get_entries_by_range(start, end)
        elif self._filter_state.mode == FilterMode.ALL:
            entries = self._repository.get_all_entries()
        else:
            if not self._filter_state.start or not self._filter_state.end:
                QMessageBox.warning(self, "Invalid range", "Please select a valid start and end time.")
                self._latest_entries = []
                self._update_missing_timeslots([])
                return
            entries = self._repository.get_entries_by_range(self._filter_state.start, self._filter_state.end)

        entries = list(entries)
        self._latest_entries = entries
        self._update_missing_timeslots(entries)

        summaries = self._build_summaries(entries)
        self._model.update_rows(summaries)
        self._update_summary_totals(summaries)
        self._update_status_bar()
        self._log_filter_change(len(entries))
        self._select_task(preserve_task)

    def _build_summaries(self, entries: list[Entry]) -> list[TaskSummary]:
        totals: dict[str, int] = {}
        category_counts: dict[str, dict[str, int]] = {}
        for entry in entries:
            totals[entry.task] = totals.get(entry.task, 0) + entry.minutes
            category = (entry.category or "").strip()
            if category:
                bucket = category_counts.setdefault(entry.task, {})
                bucket[category] = bucket.get(category, 0) + 1
        summaries: list[TaskSummary] = []
        for task_name, total_minutes in totals.items():
            category = None
            counts = category_counts.get(task_name)
            if counts:
                category = sorted(
                    counts.items(),
                    key=lambda item: (-item[1], item[0].lower(), item[0]),
                )[0][0]
            summaries.append(TaskSummary(task=task_name, total_minutes=total_minutes, category=category))
        summaries.sort(key=lambda summary: (-summary.total_minutes, summary.task.lower()))
        return summaries

    def _update_missing_timeslots(self, entries: list[Entry]) -> None:
        if self._missing_panel is None or self._missing_rows_layout is None:
            return

        threshold = self._settings.missing_timeslot_threshold_minutes
        if threshold <= 0:
            self._clear_missing_rows()
            self._missing_panel.setVisible(False)
            self._active_missing_timeslots = []
            if self._missing_count_label is not None:
                self._missing_count_label.setText("")
            return

        ignored = self._missing_store.ignored_keys()
        gaps = detect_missing_timeslots(entries, threshold, ignored)
        self._active_missing_timeslots = gaps

        self._clear_missing_rows()
        if not gaps:
            if self._missing_count_label is not None:
                self._missing_count_label.setText("")
            self._missing_panel.setVisible(False)
            return

        for gap in gaps:
            row = self._build_missing_row(gap)
            self._missing_rows_layout.addWidget(row)

        if self._missing_count_label is not None:
            suffix = "gap" if len(gaps) == 1 else "gaps"
            self._missing_count_label.setText(f"{len(gaps)} {suffix} ≤ {threshold} min")
        self._missing_panel.setVisible(True)

    def _clear_missing_rows(self) -> None:
        if self._missing_rows_layout is None:
            return
        while self._missing_rows_layout.count():
            item = self._missing_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_missing_row(self, timeslot: MissingTimeslot) -> QWidget:
        parent = self._missing_panel or self
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        same_day = timeslot.start.date() == timeslot.end.date()
        if same_day:
            day = timeslot.start.strftime("%Y-%m-%d")
            range_label = f"{timeslot.start.strftime('%H:%M')} – {timeslot.end.strftime('%H:%M')}"
            text = f"{day} • {range_label} ({timeslot.minutes} min gap)"
        else:
            text = (
                f"{timeslot.start.strftime('%Y-%m-%d %H:%M')} → "
                f"{timeslot.end.strftime('%Y-%m-%d %H:%M')} ({timeslot.minutes} min gap)"
            )

        label = QLabel(text, row)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(label, 1)

        fix_button = QPushButton("Fix", row)
        fix_button.clicked.connect(
            lambda _checked=False, slot=timeslot: self._handle_fix_missing(slot)
        )
        layout.addWidget(fix_button)

        dismiss_button = QPushButton("Dismiss", row)
        dismiss_button.clicked.connect(
            lambda _checked=False, slot=timeslot: self._handle_dismiss_missing(slot)
        )
        layout.addWidget(dismiss_button)

        return row

    def _handle_fix_missing(self, timeslot: MissingTimeslot) -> None:
        try:
            self._prompt_service.prompt_missing_timeslot(timeslot, parent=self)
        except Exception:  # pragma: no cover - Qt dialog failures are environment specific
            LOGGER.exception(
                "Unable to open prompt for missing gap",
                extra={
                    "event": "missing_timeslot_prompt_failed",
                    "start": timeslot.start.isoformat(),
                    "end": timeslot.end.isoformat(),
                },
            )
            QMessageBox.critical(
                self,
                "Prompt unavailable",
                "The prompt window could not be opened for this gap. Please try again.",
            )
            self.statusBar().showMessage("Failed to open prompt for missing gap.", 6000)
            return

        LOGGER.info(
            "Missing timeslot prompt opened",
            extra={
                "event": "missing_timeslot_prompt_opened",
                "start": timeslot.start.isoformat(),
                "end": timeslot.end.isoformat(),
            },
        )

    def _handle_dismiss_missing(self, timeslot: MissingTimeslot) -> None:
        key = timeslot.key()
        if not any(slot.key() == key for slot in self._active_missing_timeslots):
            return

        self._missing_store.dismiss(timeslot)
        LOGGER.info(
            "Missing timeslot dismissed",
            extra={
                "event": "missing_timeslot_dismissed",
                "start": timeslot.start.isoformat(),
                "end": timeslot.end.isoformat(),
            },
        )

        if timeslot.start.date() == timeslot.end.date():
            label = (
                f"{timeslot.start.strftime('%Y-%m-%d')} "
                f"{timeslot.start.strftime('%H:%M')} → {timeslot.end.strftime('%H:%M')}"
            )
        else:
            label = (
                f"{timeslot.start.strftime('%Y-%m-%d %H:%M')} → "
                f"{timeslot.end.strftime('%Y-%m-%d %H:%M')}"
            )

        self.statusBar().showMessage(f"Dismissed gap {label}.", 5000)
        self._update_missing_timeslots(self._latest_entries)

    # ------------------------------------------------------------------
    def update_repository(self, repository: EntriesRepository) -> None:
        self._repository = repository
        self._missing_store.refresh_path()
        self._refresh_totals()

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._missing_store.refresh_path()
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
        if index.column() == 1:
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

    def _handle_category_commit(
        self,
        index: QModelIndex,
        new_category: str | None,
        previous_category: str | None,
    ) -> None:
        summary = self._model.summary_for_row(index.row())
        if summary is None:
            return

        previous_key = (previous_category or "").lower()
        new_key = (new_category or "").lower() if new_category else ""
        if previous_key == new_key:
            return

        try:
            updated = self._prompt_manager.set_task_category(summary.task, new_category)
        except ValueError as exc:
            QMessageBox.warning(self, "Unable to update category", str(exc))
            self._refresh_totals(preserve_task=summary.task)
            return
        except PersistenceError as exc:
            QMessageBox.critical(self, "Unable to update category", str(exc))
            self._refresh_totals(preserve_task=summary.task)
            return

        if updated <= 0:
            self.statusBar().showMessage("No category changes applied.", 4000)
        else:
            suffix = "entry" if updated == 1 else "entries"
            self.statusBar().showMessage(f"Updated category for {updated} {suffix}.", 5000)
        self._refresh_totals(preserve_task=summary.task)

    def _current_selected_task(self) -> str | None:
        selection_model = self._table.selectionModel()
        if selection_model is None:
            return None
        selected_rows = selection_model.selectedRows()
        if not selected_rows:
            return None
        summary = self._model.summary_for_row(selected_rows[0].row())
        return summary.task if summary else None

    def _select_task(self, task_name: str | None) -> None:
        if not task_name:
            return
        for row in range(self._model.rowCount()):
            summary = self._model.summary_for_row(row)
            if summary and summary.task == task_name:
                self._table.selectRow(row)
                self._table.scrollTo(self._model.index(row, 0), QAbstractItemView.PositionAtCenter)
                break
