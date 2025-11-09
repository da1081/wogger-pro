"""Dialog for managing categories."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.categories import CategoryManager
from ..core.exceptions import PersistenceError
from ..core.repository import EntriesRepository
from .category_picker import CATEGORY_PATH_SEPARATOR
from .icons import app_icon

LOGGER = logging.getLogger("wogger.ui.categories")


@dataclass(slots=True)
class CategoryRow:
    name: str
    count: int
    managed: bool


class CategoriesDialog(QDialog):
    def __init__(
        self,
        category_manager: CategoryManager,
        repository: EntriesRepository,
        parent: QWidget | None = None,
        *,
        entries_updated: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Categories")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)

        self._category_manager = category_manager
        self._repository = repository
        self._rows: list[CategoryRow] = []
        self._entries_updated_callback = entries_updated

        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel(
            "Create, rename, or remove categories for every task."
            f" Use '{CATEGORY_PATH_SEPARATOR}' between names to build tree levels.",
            self,
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        toggle_row = QWidget(self)
        toggle_layout = QHBoxLayout(toggle_row)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(8)

        self._flat_button = QPushButton("Flat view", toggle_row)
        self._flat_button.setCheckable(True)
        toggle_layout.addWidget(self._flat_button)

        self._tree_button = QPushButton("Tree view", toggle_row)
        self._tree_button.setCheckable(True)
        toggle_layout.addWidget(self._tree_button)

        toggle_layout.addStretch(1)
        layout.addWidget(toggle_row)

        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self._flat_button, 0)
        self._view_group.addButton(self._tree_button, 1)
        self._view_group.buttonToggled.connect(self._on_view_button_toggled)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemSelectionChanged.connect(self._sync_button_states)
        self._list.itemDoubleClicked.connect(lambda _item: self._on_rename_clicked())

        self._tree = QTreeWidget(self)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._sync_button_states)
        self._tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._list)
        self._stack.addWidget(self._tree)
        layout.addWidget(self._stack, 1)

        button_row = QWidget(self)
        row_layout = QHBoxLayout(button_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        self._add_button = QPushButton("Add…", button_row)
        self._add_button.clicked.connect(self._on_add_clicked)
        row_layout.addWidget(self._add_button)

        self._rename_button = QPushButton("Rename…", button_row)
        self._rename_button.clicked.connect(self._on_rename_clicked)
        row_layout.addWidget(self._rename_button)

        self._move_up_button = QPushButton("Move Up", button_row)
        self._move_up_button.clicked.connect(lambda: self._on_move_category(-1))
        row_layout.addWidget(self._move_up_button)

        self._move_down_button = QPushButton("Move Down", button_row)
        self._move_down_button.clicked.connect(lambda: self._on_move_category(1))
        row_layout.addWidget(self._move_down_button)

        self._delete_button = QPushButton("Delete…", button_row)
        self._delete_button.clicked.connect(self._on_delete_clicked)
        row_layout.addWidget(self._delete_button)

        row_layout.addStretch(1)

        self._bulk_edit_button = QPushButton("Bulk Edit…", button_row)
        self._bulk_edit_button.clicked.connect(self._on_bulk_edit_clicked)
        row_layout.addWidget(self._bulk_edit_button)

        layout.addWidget(button_row)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #d97706;")
        layout.addWidget(self._status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(500, 420)
        self._set_view_mode(tree=False, force=True)
        self._sync_button_states()

    # ------------------------------------------------------------------
    def _on_view_button_toggled(self, button: QWidget, checked: bool) -> None:
        if not checked:
            return
        self._set_view_mode(tree=(button is self._tree_button))

    def _set_view_mode(self, *, tree: bool, force: bool = False) -> None:
        if not force and self._using_tree_view() == tree:
            return
        current = self._current_row()
        self._view_group.blockSignals(True)
        self._flat_button.setChecked(not tree)
        self._tree_button.setChecked(tree)
        self._view_group.blockSignals(False)
        self._stack.setCurrentIndex(1 if tree else 0)
        if current is not None:
            self._select_category(current.name)
        else:
            self._clear_selection()
        self._sync_button_states()

    def _using_tree_view(self) -> bool:
        return self._stack.currentIndex() == 1

    # ------------------------------------------------------------------
    def _refresh_list(self) -> None:
        previous = self._current_row()
        previous_name = previous.name if previous else None

        counts = self._gather_counts()
        managed_categories = self._category_manager.list_categories()

        rows: list[CategoryRow] = []
        for name in managed_categories:
            lower = name.lower()
            stored = counts.pop(lower, (name, 0))
            count = stored[1]
            rows.append(CategoryRow(name=name, count=count, managed=True))

        unmanaged = [
            CategoryRow(name=stored[0], count=stored[1], managed=False)
            for stored in counts.values()
        ]
        unmanaged.sort(key=lambda row: row.name.lower())
        rows.extend(unmanaged)

        self._rows = rows

        self._populate_flat_view()
        self._populate_tree_view()

        if previous_name is not None:
            self._select_category(previous_name)
        else:
            self._clear_selection()

        self._sync_button_states()

    def _populate_flat_view(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()

        if not self._rows:
            placeholder = QListWidgetItem("No categories yet")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
            self._list.blockSignals(False)
            return

        for row in self._rows:
            label = self._format_row_label(row)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row)
            if not row.managed:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
                item.setToolTip(
                    "Category exists in entries but is not saved. Rename or delete to resolve."
                )
            else:
                item.setToolTip(row.name)
            self._list.addItem(item)

        self._list.blockSignals(False)

    def _populate_tree_view(self) -> None:
        self._tree.blockSignals(True)
        self._tree.clear()

        if not self._rows:
            placeholder = QTreeWidgetItem(["No categories yet"])
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tree.addTopLevelItem(placeholder)
            self._tree.blockSignals(False)
            return

        node_map: dict[tuple[str, ...], QTreeWidgetItem] = {}

        for row in self._rows:
            parts = [segment.strip() for segment in row.name.split(CATEGORY_PATH_SEPARATOR)]
            parts = [segment for segment in parts if segment]
            if not parts:
                parts = [row.name]

            key_path: list[str] = []
            parent_item: QTreeWidgetItem | None = None

            for index, segment in enumerate(parts):
                key_path.append(segment.lower())
                key = tuple(key_path)
                item = node_map.get(key)
                created = False
                if item is None:
                    item = QTreeWidgetItem([segment])
                    if parent_item is None:
                        self._tree.addTopLevelItem(item)
                    else:
                        parent_item.addChild(item)
                    node_map[key] = item
                    created = True
                if index < len(parts) - 1 and created:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if index == len(parts) - 1:
                    display_name = segment
                    item.setText(0, self._format_row_label(row, display_name=display_name))
                    item.setData(0, Qt.ItemDataRole.UserRole, row)
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                    if not row.managed:
                        font = item.font(0)
                        font.setItalic(True)
                        item.setFont(0, font)
                        item.setToolTip(
                            0,
                            "Category exists in entries but is not saved. Rename or delete to resolve.",
                        )
                    else:
                        item.setToolTip(0, row.name)
                parent_item = item

        self._tree.expandAll()
        self._tree.blockSignals(False)

    def _format_row_label(
        self, row: CategoryRow, *, display_name: str | None = None
    ) -> str:
        name = display_name if display_name is not None else row.name
        suffix = "entry" if row.count == 1 else "entries"
        label = f"{name} ({row.count} {suffix})"
        if not row.managed:
            label += " [unsaved]"
        return label

    def _gather_counts(self) -> dict[str, tuple[str, int]]:
        counts: dict[str, tuple[str, int]] = {}
        try:
            for name, count in self._repository.list_categories_with_counts():
                normalized = (name or "").strip()
                if not normalized:
                    continue
                lower = normalized.lower()
                counts[lower] = (normalized, count)
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Unable to load category counts")
        return counts

    def _current_row(self) -> CategoryRow | None:
        if self._using_tree_view():
            item = self._tree.currentItem()
            if item is None:
                return None
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, CategoryRow):
                return data
            return None

        item = self._list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, CategoryRow):
            return data
        return None

    def _clear_selection(self) -> None:
        self._list.blockSignals(True)
        self._list.clearSelection()
        self._list.blockSignals(False)

        self._tree.blockSignals(True)
        self._tree.clearSelection()
        self._tree.blockSignals(False)

    def _select_category(self, name: str) -> None:
        lookup = name.lower()

        self._list.blockSignals(True)
        matched = False
        for index in range(self._list.count()):
            item = self._list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, CategoryRow) and data.name.lower() == lookup:
                self._list.setCurrentRow(index)
                matched = True
                break
        if not matched:
            self._list.clearSelection()
        self._list.blockSignals(False)

        self._tree.blockSignals(True)
        matched_item: QTreeWidgetItem | None = None
        for item in self._iter_tree_items():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, CategoryRow) and data.name.lower() == lookup:
                matched_item = item
                break
        if matched_item is not None:
            self._tree.setCurrentItem(matched_item)
            self._tree.scrollToItem(matched_item)
        else:
            self._tree.clearSelection()
        self._tree.blockSignals(False)

    def _iter_tree_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        stack: list[QTreeWidgetItem] = [
            self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())
        ]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            items.append(item)
            for idx in range(item.childCount() - 1, -1, -1):
                stack.append(item.child(idx))
        return items

    def _on_add_clicked(self) -> None:
        new_name = self._prompt_for_name("Add Category", "Category name:")
        if new_name is None:
            return
        if self._category_exists(new_name):
            QMessageBox.warning(self, "Duplicate category", "That category already exists.")
            return
        try:
            self._category_manager.add_category(new_name)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid category", str(exc))
            return
        except PersistenceError as exc:
            QMessageBox.critical(self, "Unable to add category", str(exc))
            return
        self._status_label.setText("")
        self._refresh_list()
        self._select_category(new_name)

    def _on_rename_clicked(self) -> None:
        row = self._current_row()
        if row is None:
            return
        new_name = self._prompt_for_name("Rename Category", "New name:", row.name)
        if new_name is None:
            return
        if new_name.lower() == row.name.lower():
            self._status_label.setText("No changes made.")
            return
        if self._category_exists(new_name, exclude=row.name):
            QMessageBox.warning(self, "Duplicate category", "That category already exists.")
            return

        try:
            updated = self._repository.rename_category(row.name, new_name)
        except ValueError as exc:
            QMessageBox.warning(self, "Unable to rename", str(exc))
            return
        except PersistenceError as exc:
            QMessageBox.critical(self, "Unable to rename", str(exc))
            return

        try:
            if row.managed:
                self._category_manager.rename_category(row.name, new_name)
            else:
                self._category_manager.add_category(new_name)
        except ValueError:
            LOGGER.warning("Category manager rename failed; ensuring mapping")
            try:
                self._category_manager.delete_category(row.name)
            except Exception:
                LOGGER.debug("Cleanup for category rename skipped", exc_info=True)
            try:
                self._category_manager.add_category(new_name)
            except Exception:
                LOGGER.exception("Category manager unable to apply rename")
        except PersistenceError as exc:
            QMessageBox.critical(self, "Unable to update category store", str(exc))
            return

        suffix = "entry" if updated == 1 else "entries"
        self._status_label.setText(f"Renamed category for {updated} {suffix}.")
        self._refresh_list()
        self._select_category(new_name)

    def _on_delete_clicked(self) -> None:
        row = self._current_row()
        if row is None:
            return
        if row.count > 0:
            suffix = "entry" if row.count == 1 else "entries"
            message = (
                f"Remove '{row.name}' and clear it from {row.count} {suffix}?\n\n"
                "Entries keep their other details."
            )
            confirm = QMessageBox.question(
                self,
                "Confirm delete",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        else:
            confirm = QMessageBox.question(
                self,
                "Confirm delete",
                f"Remove '{row.name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        try:
            cleared = self._repository.clear_category(row.name)
        except PersistenceError as exc:
            QMessageBox.critical(self, "Unable to update entries", str(exc))
            return

        if row.managed:
            try:
                self._category_manager.delete_category(row.name)
            except PersistenceError as exc:
                QMessageBox.critical(self, "Unable to update category store", str(exc))
                return

        self._status_label.setText(
            "Category removed." if cleared == 0 else f"Cleared from {cleared} entries."
        )
        self._refresh_list()
        self._notify_entries_updated()

    def _on_move_category(self, delta: int) -> None:
        row = self._current_row()
        if row is None or not row.managed:
            return

        order = self._category_manager.list_categories()
        try:
            index = next(i for i, name in enumerate(order) if name.lower() == row.name.lower())
        except StopIteration:
            return

        target_index = index + delta
        if target_index < 0 or target_index >= len(order):
            return

        reordered = list(order)
        item = reordered.pop(index)
        reordered.insert(target_index, item)

        try:
            self._category_manager.reorder_categories(reordered)
        except (ValueError, PersistenceError) as exc:
            QMessageBox.critical(self, "Unable to reorder categories", str(exc))
            return

        self._status_label.setText("Category order updated.")
        self._refresh_list()

    def _on_bulk_edit_clicked(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Bulk Edit Categories")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        description = QLabel(
            "Add or remove categories in bulk. Enter one category per line."
            " Include parent paths using"
            f" '{CATEGORY_PATH_SEPARATOR}' (for example, 'Parent {CATEGORY_PATH_SEPARATOR} Child')."
            " Remove a line to delete a category, or add new lines to create them."
            " Blank lines and duplicates are ignored. This tool does not rename existing categories.",
            dialog,
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        editor = QPlainTextEdit(dialog)
        editor.setPlaceholderText("One category per line")
        editor.setPlainText("\n".join(self._bulk_editor_initial_lines()))
        editor.setMinimumHeight(240)
        layout.addWidget(editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        editor.setFocus()

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_names = self._normalize_bulk_lines(editor.toPlainText())

        previous_order = self._category_manager.list_categories()
        previous_order_cf = [name.casefold() for name in previous_order]
        previous_keys = set(previous_order_cf)

        existing_map = {row.name.casefold(): row for row in self._rows}
        existing_keys = set(existing_map.keys())
        new_keys = {name.casefold() for name in new_names}

        desired_existing_order = [name for name in new_names if name.casefold() in previous_keys]
        order_change_requested = (
            [name.casefold() for name in desired_existing_order] != previous_order_cf
        )

        to_remove = [existing_map[key] for key in existing_keys - new_keys]

        added_lower: set[str] = set()
        to_add: list[str] = []
        for name in new_names:
            key = name.casefold()
            row = existing_map.get(key)
            if row is None or not row.managed:
                if key not in added_lower:
                    to_add.append(name)
                    added_lower.add(key)

        if not to_add and not to_remove and not order_change_requested:
            self._status_label.setText("Bulk edit made no changes.")
            return

        impacted = [row for row in to_remove if row.count > 0]
        if impacted:
            lines = "\n".join(
                f"- {row.name} ({row.count} {'entry' if row.count == 1 else 'entries'})"
                for row in impacted
            )
            message = (
                "The following categories are still assigned to entries:\n\n"
                f"{lines}\n\n"
                "Removing these categories will also remove them from those entries."
                " The entries (Tasks) keep all other information."
            )
            confirm = QMessageBox.question(
                self,
                "Confirm category removal",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        added_count = 0
        for name in to_add:
            try:
                self._category_manager.add_category(name)
            except ValueError as exc:
                QMessageBox.warning(self, "Unable to add category", str(exc))
                self._refresh_list()
                return
            except PersistenceError as exc:
                QMessageBox.critical(self, "Unable to add category", str(exc))
                self._refresh_list()
                return
            added_count += 1

        removed_count = 0
        cleared_entries = 0
        for row in to_remove:
            try:
                cleared = self._repository.clear_category(row.name)
            except PersistenceError as exc:
                QMessageBox.critical(self, "Unable to update entries", str(exc))
                self._refresh_list()
                return
            cleared_entries += cleared
            removed_count += 1
            if row.managed:
                try:
                    self._category_manager.delete_category(row.name)
                except PersistenceError as exc:
                    QMessageBox.critical(self, "Unable to update category store", str(exc))
                    self._refresh_list()
                    return

        try:
            current_after = self._category_manager.list_categories()
            current_keys = {name.casefold() for name in current_after}
            desired_final = [name for name in new_names if name.casefold() in current_keys]
            if current_after or desired_final:
                self._category_manager.reorder_categories(desired_final)
            final_order = self._category_manager.list_categories()
        except (ValueError, PersistenceError) as exc:
            QMessageBox.critical(self, "Unable to finalize category order", str(exc))
            self._refresh_list()
            return

        order_changed_final = (
            [name.casefold() for name in final_order] != previous_order_cf
        )

        self._refresh_list()
        if to_add:
            self._select_category(to_add[0])
        if removed_count:
            self._notify_entries_updated()

        summary_parts: list[str] = []
        if added_count:
            summary_parts.append(
                f"added {added_count} {'category' if added_count == 1 else 'categories'}"
            )
        if removed_count:
            summary_parts.append(
                f"removed {removed_count} {'category' if removed_count == 1 else 'categories'}"
            )
        if cleared_entries:
            summary_parts.append(
                f"cleared {cleared_entries} {'entry' if cleared_entries == 1 else 'entries'}"
            )
        if order_changed_final:
            summary_parts.append("updated category order")

        self._status_label.setText(
            "Bulk edit applied: " + ", ".join(summary_parts) + "."
            if summary_parts
            else "Bulk edit applied."
        )

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, CategoryRow):
            self._on_rename_clicked()

    def _prompt_for_name(
        self, title: str, label: str, default: str | None = None
    ) -> str | None:
        text, ok = QInputDialog.getText(self, title, label, text=default or "")
        if not ok:
            return None
        trimmed = text.strip()
        return trimmed or None

    def _category_exists(self, name: str, *, exclude: str | None = None) -> bool:
        lookup = name.lower()
        excluded = exclude.lower() if exclude else None
        for row in self._rows:
            if excluded is not None and row.name.lower() == excluded:
                continue
            if row.name.lower() == lookup:
                return True
        return False

    def _sync_button_states(self) -> None:
        row = self._current_row()
        has_selection = row is not None
        self._rename_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)
        can_move = has_selection and row is not None and row.managed
        if can_move:
            order = [managed.name for managed in self._rows if managed.managed]
            try:
                index = next(
                    idx for idx, name in enumerate(order) if name.lower() == row.name.lower()
                )
            except StopIteration:
                index = -1
            self._move_up_button.setEnabled(index > 0)
            self._move_down_button.setEnabled(0 <= index < len(order) - 1)
        else:
            self._move_up_button.setEnabled(False)
            self._move_down_button.setEnabled(False)

    def _notify_entries_updated(self) -> None:
        if self._entries_updated_callback is None:
            return
        try:
            self._entries_updated_callback()
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Entries updated callback failed")

    def _bulk_editor_initial_lines(self) -> list[str]:
        managed = [row.name for row in self._rows if row.managed]
        seen = {name.casefold() for name in managed}
        unmanaged: list[str] = []
        for row in self._rows:
            if row.managed:
                continue
            key = row.name.casefold()
            if key in seen:
                continue
            unmanaged.append(row.name)
            seen.add(key)
        unmanaged.sort(key=str.casefold)
        return managed + unmanaged

    def _normalize_bulk_lines(self, text: str) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for raw in text.splitlines():
            candidate = raw.strip()
            if not candidate:
                continue
            folded = candidate.casefold()
            if folded in seen:
                continue
            names.append(candidate)
            seen.add(folded)
        return names
