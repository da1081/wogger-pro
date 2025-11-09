"""Reusable category tree picker widget."""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QFontMetrics, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

CATEGORY_PATH_SEPARATOR = " - "
_CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 41
_NONE_KEY = "__none__"


class _CategoryTreePopup(QFrame):
    category_chosen = Signal(object, object)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setObjectName("categoryTreePopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tree = QTreeView(self)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.clicked.connect(self._on_clicked)
        layout.addWidget(self._tree)

    def set_model(self, model: QStandardItemModel) -> None:
        self._tree.setModel(model)
        self._tree.expandAll()

    def set_current_index(self, index) -> None:
        if index is not None and index.isValid():
            self._tree.setCurrentIndex(index)
            self._tree.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def show_for(self, anchor: QWidget) -> None:
        width = max(anchor.width(), 280)
        height = min(360, max(200, self.sizeHint().height()))
        self.resize(width, height)
        global_pos = anchor.mapToGlobal(QPoint(0, anchor.height()))
        self.move(global_pos)
        self.show()
        self.raise_()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self.closed.emit()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_clicked(self, index) -> None:
        if not index or not index.isValid():
            return
        if not (index.flags() & Qt.ItemIsSelectable):
            return
        value = index.data(_CATEGORY_ROLE)
        self.category_chosen.emit(value, index)
        self.hide()


class CategoryTreePicker(QWidget):
    """Widget that exposes categories in a hierarchical dropdown tree."""

    category_changed = Signal(object)

    def __init__(
        self,
        *,
        allow_none: bool = True,
        auto_popup: bool = False,
        separator: str = CATEGORY_PATH_SEPARATOR,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._allow_none = allow_none
        self._auto_popup = auto_popup
        self._separator = separator
        self._categories: list[str] = []
        self._current_category: Optional[str] = None
        self._model = QStandardItemModel()
        self._node_lookup: Dict[Tuple[str, ...], QStandardItem] = {}
        self._item_lookup: Dict[str, QStandardItem] = {}

        self._popup = _CategoryTreePopup(self)
        self._popup.category_chosen.connect(self._on_popup_chosen)
        self._popup.closed.connect(self._on_popup_closed)

        self._line_edit = QLineEdit(self)
        self._line_edit.setReadOnly(True)
        self._line_edit.setPlaceholderText("(No category)" if allow_none else "Select category")
        self._line_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._line_edit.setObjectName("categoryTreePickerLineEdit")
        self._line_edit.installEventFilter(self)
        self._full_display_text = ""

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._line_edit, 1)

        self.set_categories([])

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elided_text()

    # ------------------------------------------------------------------
    def set_categories(self, categories: Sequence[str]) -> None:
        sanitized: list[str] = []
        seen: set[str] = set()
        for value in categories:
            normalized = value.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            sanitized.append(normalized)
        self._categories = sanitized

        previous = self._current_category
        self._rebuild_model()
        self._popup.set_model(self._model)
        self.set_current_category(previous)

    def categories(self) -> list[str]:
        return list(self._categories)

    def set_current_category(self, category: Optional[str]) -> None:
        target = (category or "").strip()
        if not target:
            self._current_category = None
            item = self._item_lookup.get(_NONE_KEY)
            if item is not None:
                self._popup.set_current_index(item.index())
            self._update_line_display()
            return
        item = self._ensure_category(target)
        self._current_category = target
        if item is None:
            self._update_line_display()
            return
        self._popup.set_current_index(item.index())
        self._update_line_display()

    def current_category(self) -> Optional[str]:
        return self._current_category

    def set_auto_popup(self, enabled: bool) -> None:
        self._auto_popup = enabled

    def open_popup(self) -> None:
        if not self.isEnabled():
            return
        self._popup.set_model(self._model)
        if self._current_category:
            item = self._item_lookup.get(self._current_category.lower())
            if item is not None:
                self._popup.set_current_index(item.index())
        elif self._allow_none:
            item = self._item_lookup.get(_NONE_KEY)
            if item is not None:
                self._popup.set_current_index(item.index())
        self._popup.show_for(self)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._auto_popup:
            QTimer.singleShot(0, self.open_popup)

    # ------------------------------------------------------------------
    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        if self._auto_popup:
            self.open_popup()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_popup()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_popup()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self._line_edit and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
        ):
            if event.button() == Qt.MouseButton.LeftButton:
                self.open_popup()
                return True
        return super().eventFilter(watched, event)

    def setEnabled(self, enabled: bool) -> None:  # type: ignore[override]
        super().setEnabled(enabled)
        self._line_edit.setEnabled(enabled)
        self._button.setEnabled(enabled)

    # ------------------------------------------------------------------
    def _on_popup_chosen(self, category_value, index) -> None:
        value = (category_value or "").strip() or None
        if value == self._current_category:
            self._update_line_display()
            return
        self._current_category = value
        self._update_line_display()
        self.category_changed.emit(self._current_category)

    def _on_popup_closed(self) -> None:
        pass

    def _display_text_for_current(self) -> str:
        if self._current_category:
            parts = [part.strip() for part in self._current_category.split(self._separator) if part.strip()]
            if parts:
                return parts[-1]
            return self._current_category
        return "(No category)" if self._allow_none else ""

    def _update_line_display(self) -> None:
        self._full_display_text = self._display_text_for_current()
        self._apply_elided_text()
        if self._current_category:
            self._line_edit.setToolTip(self._current_category)
        else:
            tooltip = "" if not self._allow_none else "(No category)"
            self._line_edit.setToolTip(tooltip)

    def _apply_elided_text(self) -> None:
        full_text = self._full_display_text
        if not full_text:
            if self._line_edit.text():
                self._line_edit.clear()
            return
        available_width = self._line_edit.contentsRect().width()
        if available_width <= 0:
            display_text = full_text
        else:
            metrics = QFontMetrics(self._line_edit.font())
            display_text = metrics.elidedText(full_text, Qt.TextElideMode.ElideRight, available_width)
        if self._line_edit.text() != display_text:
            self._line_edit.setText(display_text)

    def _rebuild_model(self) -> None:
        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(["Category"])
        self._node_lookup.clear()
        self._item_lookup.clear()
        if self._allow_none:
            none_item = QStandardItem("(No category)")
            none_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            none_item.setData(None, _CATEGORY_ROLE)
            none_item.setData("", Qt.EditRole)
            self._model.appendRow(none_item)
            self._item_lookup[_NONE_KEY] = none_item
        for category in self._categories:
            self._ensure_category(category)

    def _ensure_category(self, category: str) -> Optional[QStandardItem]:
        normalized = category.strip()
        if not normalized:
            return self._item_lookup.get(_NONE_KEY)
        key = normalized.lower()
        existing = self._item_lookup.get(key)
        if existing is not None:
            return existing
        parts = [part.strip() for part in normalized.split(self._separator) if part.strip()]
        if not parts:
            return self._item_lookup.get(_NONE_KEY)
        parent = self._model.invisibleRootItem()
        path: list[str] = []
        for index, part in enumerate(parts):
            path.append(part)
            path_key = tuple(path)
            item = self._node_lookup.get(path_key)
            if item is None:
                item = QStandardItem(part)
                item.setData(part, Qt.DisplayRole)
                if index == len(parts) - 1:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    item.setData(normalized, _CATEGORY_ROLE)
                    item.setData(part, Qt.EditRole)
                    item.setData(normalized, Qt.ToolTipRole)
                else:
                    item.setFlags(Qt.ItemIsEnabled)
                    item.setData(None, _CATEGORY_ROLE)
                parent.appendRow(item)
                self._node_lookup[path_key] = item
            else:
                if item.text() != part:
                    item.setText(part)
                item.setData(part, Qt.DisplayRole)
                if index < len(parts) - 1:
                    item.setFlags(Qt.ItemIsEnabled)
                    item.setData(None, _CATEGORY_ROLE)
                    parent_full = self._separator.join(path)
                    self._item_lookup.pop(parent_full.lower(), None)
                elif not (item.flags() & Qt.ItemIsSelectable):
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    item.setData(normalized, _CATEGORY_ROLE)
                    item.setData(part, Qt.EditRole)
                    item.setData(normalized, Qt.ToolTipRole)
            parent = item
        self._item_lookup[key] = parent
        return parent
