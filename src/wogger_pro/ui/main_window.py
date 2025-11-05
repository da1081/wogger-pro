"""Main dashboard window for Wogger Pro."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import partial
from importlib import metadata
from typing import Optional, Callable, Sequence

from PySide6.QtCore import Qt, Signal, QSize, QTimer, QModelIndex, QUrl
from PySide6.QtGui import QPalette, QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
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
    QTabWidget,
    QDialog,
    QStyledItemDelegate,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
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
    update_available_icon,
)
from .category_picker import CATEGORY_PATH_SEPARATOR, CategoryTreePicker
from .prompt_service import PromptService
from .task_totals_model import TaskTotalsModel
from .task_edit_dialog import TaskEditDialog

LOGGER = logging.getLogger("wogger.ui.main")

LATEST_RELEASE_API_URL = "https://api.github.com/repos/da1081/wogger-pro/releases/latest"
LATEST_RELEASE_WEB_URL = "https://github.com/da1081/wogger-pro/releases/latest"
_VERSION_SEGMENT_RE = re.compile(r"^(\d+)")


def _normalize_version_tag(tag: str) -> str:
    tag = tag.strip()
    if tag.lower().startswith("v"):
        tag = tag[1:]
    return tag


def _version_tuple(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    parts: list[int] = []
    for segment in value.split("."):
        match = _VERSION_SEGMENT_RE.match(segment)
        if not match:
            return None
        parts.append(int(match.group(1)))
    return tuple(parts)


class FilterMode(Enum):
    TODAY = "today"
    ALL = "all"
    RANGE = "range"


@dataclass(slots=True)
class FilterState:
    mode: FilterMode
    start: Optional[datetime] = None
    end: Optional[datetime] = None


NO_CATEGORY_LABEL = "(No category)"


class _CategoryTreeNode:
    __slots__ = ("name", "minutes", "children", "path", "path_lower")

    def __init__(self, name: str, path: tuple[str, ...], path_lower: tuple[str, ...]) -> None:
        self.name = name
        self.path = path
        self.path_lower = path_lower
        self.minutes = 0
        self.children: dict[str, "_CategoryTreeNode"] = {}


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
        self.resize(800, 500)
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
        self._tabs: QTabWidget | None = None
        self._category_tree: QTreeWidget | None = None
        self._category_order_map: dict[tuple[str, ...], int] = {}
        self._category_order_fallback_base: int = 0
        self._category_order_names: dict[tuple[str, ...], str] = {}
        self._network_manager = QNetworkAccessManager(self)
        self._update_button: QToolButton | None = None
        self._latest_release_url = LATEST_RELEASE_WEB_URL
        self._current_version = self._resolve_current_version()
        self._current_version_tuple = _version_tuple(self._current_version) if self._current_version else None

        self._build_ui()
        self._connect_signals()
        self._palette_listener = self._refresh_icons
        add_palette_listener(self._palette_listener)
        self._refresh_icons()
        self._refresh_totals()
        QTimer.singleShot(750, self._check_for_updates)

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
        self._table.setTextElideMode(Qt.ElideRight)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        vheader = self._table.verticalHeader()
        vheader.setFixedWidth(12)
        header = self._table.horizontalHeader()
        self._task_column_min_width = 230
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.sectionResized.connect(self._enforce_task_column_min_width)
        header.resizeSection(0, max(header.sectionSize(0), self._task_column_min_width))
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        self._category_delegate = CategoryDelegate(
            category_provider=self._category_manager.list_categories,
            apply_callback=self._handle_category_commit,
            parent=self._table,
        )
        self._table.setItemDelegateForColumn(1, self._category_delegate)

        tasks_tab = QWidget(self)
        tasks_layout = QVBoxLayout(tasks_tab)
        tasks_layout.setContentsMargins(0, 0, 0, 0)
        tasks_layout.setSpacing(0)
        tasks_layout.addWidget(self._table)

        self._category_tree = QTreeWidget(self)
        self._category_tree.setObjectName("categoryTotalsTree")
        self._category_tree.setColumnCount(3)
        self._category_tree.setHeaderLabels(["Category", "Minutes", "Duration"])
        header = self._category_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setStretchLastSection(False)
        self._category_tree.setUniformRowHeights(True)
        self._category_tree.setRootIsDecorated(True)
        self._category_tree.setAlternatingRowColors(True)
        self._category_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._category_tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._category_tree.setAllColumnsShowFocus(True)

        categories_tab = QWidget(self)
        categories_layout = QVBoxLayout(categories_tab)
        categories_layout.setContentsMargins(0, 0, 0, 0)
        categories_layout.setSpacing(0)
        categories_layout.addWidget(self._category_tree)

        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(tasks_tab, "Tasks")
        self._tabs.addTab(categories_tab, "Categories")
        layout.addWidget(self._tabs, 1)
        self._tabs.currentChanged.connect(self._apply_active_tab_style)
        self._apply_active_tab_style()
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

    def _apply_active_tab_style(self, _index: int | None = None) -> None:
        if self._tabs is None:
            return
        tab_bar = self._tabs.tabBar()
        palette = tab_bar.palette()
        base_bg = palette.color(QPalette.Button)
        base_fg = palette.color(QPalette.ButtonText)
        highlight_bg = palette.color(QPalette.Highlight)
        highlight_fg = palette.color(QPalette.HighlightedText)
        stylesheet = (
            f"QTabBar::tab {{ background-color: {base_bg.name()}; color: {base_fg.name()}; padding: 0px 12px; min-height: 5px; }}"
            f"QTabBar::tab:selected, QTabBar::tab:hover {{ background-color: {highlight_bg.name()}; color: {highlight_fg.name()}; }}"
        )
        tab_bar.setStyleSheet(stylesheet)

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

        self._update_button = QToolButton(container)
        self._update_button.setAutoRaise(True)
        self._update_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._update_button.setIconSize(QSize(22, 22))
        self._update_button.setToolTip("Update ready")
        self._update_button.setVisible(False)
        self._update_button.clicked.connect(self._open_latest_release)

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

        right_layout.addWidget(self._update_button)
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

    def _check_for_updates(self) -> None:
        if self._network_manager is None:
            return
        if self._update_button is not None and self._update_button.isVisible():
            return
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API_URL))
        user_agent = f"WoggerPro/{self._current_version}" if self._current_version else "WoggerPro/unknown"
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, user_agent)
        follow_attr = getattr(QNetworkRequest, "FollowRedirectsAttribute", None)
        if follow_attr is None:
            follow_attr = getattr(getattr(QNetworkRequest, "Attribute", object), "FollowRedirectsAttribute", None)
        if follow_attr is not None:
            request.setAttribute(follow_attr, True)
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        reply = self._network_manager.get(request)
        reply.finished.connect(partial(self._on_update_reply, reply))

    def _on_update_reply(self, reply: QNetworkReply) -> None:
        if reply is None:
            return
        error = reply.error()
        data = bytes(reply.readAll())
        reply.deleteLater()
        if error != QNetworkReply.NetworkError.NoError:
            LOGGER.debug("Update check failed: %s", reply.errorString())
            return
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            LOGGER.debug("Unable to parse update response: %s", exc)
            return
        if payload.get("draft") or payload.get("prerelease"):
            LOGGER.debug("Latest release is draft or prerelease; skipping notification.")
            return
        tag = str(payload.get("tag_name") or "").strip()
        normalized_tag = _normalize_version_tag(tag)
        latest_tuple = _version_tuple(normalized_tag)
        current_tuple = self._current_version_tuple
        if current_tuple is None and not self._current_version:
            LOGGER.debug("Current version unknown; skipping update notification")
            return
        update_available = False
        if latest_tuple and current_tuple:
            update_available = latest_tuple > current_tuple
        elif latest_tuple and current_tuple is None and self._current_version:
            update_available = True
        elif normalized_tag and self._current_version:
            update_available = normalized_tag != self._current_version
        if not update_available:
            LOGGER.debug(
                "No update available (current=%s, latest=%s)",
                self._current_version,
                normalized_tag,
            )
            return
        release_url = str(payload.get("html_url") or LATEST_RELEASE_WEB_URL)
        version_label = tag or normalized_tag
        LOGGER.info("Update available", extra={"event": "update_available", "version": version_label})
        self._show_update_available(version_label, release_url)

    def _show_update_available(self, version_label: str, release_url: str) -> None:
        if self._update_button is None:
            return
        self._latest_release_url = release_url or LATEST_RELEASE_WEB_URL
        tooltip = "Update ready"
        if version_label:
            tooltip = f"Update ready ({version_label})"
        self._update_button.setToolTip(tooltip)
        self._update_button.setAutoRaise(True)
        self._update_button.setStyleSheet("")
        self._update_button.setVisible(True)
        self._refresh_icons()

    def _open_latest_release(self) -> None:
        target = QUrl(self._latest_release_url)
        if not target.isValid():
            target = QUrl(LATEST_RELEASE_WEB_URL)
        QDesktopServices.openUrl(target)

    def _resolve_current_version(self) -> str | None:
        env_version = os.environ.get("WOGGER_PRO_VERSION")
        if env_version:
            return env_version.strip()
        try:
            from .. import __version__ as package_version  # type: ignore
        except ImportError:
            package_version = None
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Unable to import package version")
            package_version = None
        if package_version:
            return str(package_version).strip()
        try:
            return metadata.version("wogger-pro")
        except metadata.PackageNotFoundError:
            LOGGER.debug("Package version not found; running from source.")
        except Exception:  # pragma: no cover - defensive guard
            LOGGER.exception("Unable to determine application version")
        return None

    # ------------------------------------------------------------------
    def _enforce_task_column_min_width(self, logical_index: int, _old_size: int, new_size: int) -> None:
        if logical_index != 0 or new_size >= self._task_column_min_width:
            return
        header = self._table.horizontalHeader()
        if header is None:
            return
        header.blockSignals(True)
        try:
            header.resizeSection(0, self._task_column_min_width)
        finally:
            header.blockSignals(False)

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
                self._update_category_tree([])
                return
            entries = self._repository.get_entries_by_range(self._filter_state.start, self._filter_state.end)

        entries = list(entries)
        self._latest_entries = entries
        self._update_missing_timeslots(entries)

        summaries = self._build_summaries(entries)
        self._model.update_rows(summaries)
        self._update_category_tree(entries)
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

    def _update_category_tree(self, entries: list[Entry]) -> None:
        if self._category_tree is None:
            return

        tree = self._category_tree
        tree.blockSignals(True)
        tree.clear()

        self._rebuild_category_order()

        if not entries:
            placeholder = QTreeWidgetItem(["No entries in range", "", ""])
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setFirstColumnSpanned(True)
            tree.addTopLevelItem(placeholder)
            tree.blockSignals(False)
            return

        root = _CategoryTreeNode("<root>", (), ())
        nodes_by_lower: dict[tuple[str, ...], _CategoryTreeNode] = {(): root}

        for entry in entries:
            minutes = max(int(entry.minutes), 0)
            raw_category = (entry.category or "").strip()
            parts = [segment.strip() for segment in raw_category.split(CATEGORY_PATH_SEPARATOR)] if raw_category else []
            parts = [segment for segment in parts if segment]
            if not parts:
                parts = [NO_CATEGORY_LABEL]

            parent_lower: tuple[str, ...] = ()
            parent_node = root
            parent_node.minutes += minutes
            for part in parts:
                current_lower = parent_lower + (part.lower(),)
                node = nodes_by_lower.get(current_lower)
                if node is None:
                    canonical = self._category_order_names.get(current_lower, part)
                    current_path = parent_node.path + (canonical,)
                    node = _CategoryTreeNode(canonical, current_path, current_lower)
                    nodes_by_lower[current_lower] = node
                    parent_node.children[canonical] = node
                node.minutes += minutes
                parent_node = node
                parent_lower = current_lower

        top_nodes = self._ordered_children(root)
        if not top_nodes:
            placeholder = QTreeWidgetItem(["No categories found", "", ""])
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setFirstColumnSpanned(True)
            tree.addTopLevelItem(placeholder)
            tree.blockSignals(False)
            return

        for node in top_nodes:
            tree.addTopLevelItem(self._build_category_tree_item(node))

        tree.expandAll()
        tree.blockSignals(False)

    def _ordered_children(self, node: _CategoryTreeNode) -> list[_CategoryTreeNode]:
        children = list(node.children.values())
        children.sort(key=self._category_node_sort_key)
        return children

    def _category_node_sort_key(self, node: _CategoryTreeNode) -> tuple[int, str, str]:
        order = self._category_order_map.get(node.path_lower)
        if order is None:
            if node.name == NO_CATEGORY_LABEL:
                order = self._category_order_fallback_base + 2_000_000
            else:
                order = self._category_order_fallback_base + 1_000_000
        return (order, node.name.lower(), node.name)

    def _build_category_tree_item(self, node: _CategoryTreeNode) -> QTreeWidgetItem:
        minutes_text = self._format_minutes_value(node.minutes)
        duration_text = self._format_duration(node.minutes)
        item = QTreeWidgetItem([node.name, minutes_text, duration_text])
        item.setData(0, Qt.ItemDataRole.UserRole, node.name.lower())
        item.setData(1, Qt.ItemDataRole.UserRole, node.minutes)
        item.setData(2, Qt.ItemDataRole.UserRole, node.minutes)
        tooltip = f"{node.minutes} minute{'s' if node.minutes != 1 else ''}"
        item.setToolTip(0, node.name)
        item.setToolTip(1, tooltip)
        item.setToolTip(2, duration_text)
        item.setTextAlignment(1, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setTextAlignment(2, int(Qt.AlignRight | Qt.AlignVCenter))
        for child in self._ordered_children(node):
            item.addChild(self._build_category_tree_item(child))
        return item

    def _rebuild_category_order(self) -> None:
        categories = self._category_manager.list_categories()
        order_map: dict[tuple[str, ...], int] = {}
        name_map: dict[tuple[str, ...], str] = {}
        next_index = 0
        for category in categories:
            parts = [segment.strip() for segment in category.split(CATEGORY_PATH_SEPARATOR) if segment.strip()]
            if not parts:
                continue
            parent_lower: tuple[str, ...] = ()
            for part in parts:
                current_lower = parent_lower + (part.lower(),)
                if current_lower not in order_map:
                    order_map[current_lower] = next_index
                    name_map[current_lower] = part
                    next_index += 1
                parent_lower = current_lower
        self._category_order_map = order_map
        self._category_order_names = name_map
        self._category_order_fallback_base = next_index


    def _format_minutes_value(self, minutes: int) -> str:
        return f"{minutes:,}" if minutes else "0"

    def _format_duration(self, minutes: int) -> str:
        return TaskSummary(task="", total_minutes=minutes).pretty_total

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
            ("_update_button", update_available_icon, 22),
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
        self._apply_active_tab_style()

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
