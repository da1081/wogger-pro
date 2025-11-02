"""Runtime theme management for Wogger Pro."""

from __future__ import annotations

import logging

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..core.settings import Theme
from .icons import IconColor, IconPalette, set_icon_palette

LOGGER = logging.getLogger("wogger.ui.theme")

_LIGHT_QSS = """
QWidget { background-color: #f5f6f8; }
QToolButton { padding: 4px 12px; border-radius: 4px; }
QToolButton:checked { background-color: #d0e7ff; }
QTableView { background-color: #ffffff; alternate-background-color: #f1f1f1; gridline-color: #d0d0d0; }
QTableView::item { padding: 6px 12px; }
QHeaderView::section { background-color: #ebedf0; padding: 4px 12px; border: none; text-align: left; }
QStatusBar { background-color: #f1f1f1; }
QRadioButton { padding: 4px 0; }
QRadioButton::indicator { width: 18px; height: 18px; border-radius: 9px; border: 2px solid #94a3b8; background-color: #ffffff; margin-right: 6px; }
QRadioButton::indicator:hover { border-color: #2563eb; }
QRadioButton::indicator:checked { border: 2px solid #2563eb; background-color: #2563eb; }
QRadioButton::indicator:checked:hover { border-color: #1d4ed8; }
QRadioButton:focus { outline: none; }
"""

_DARK_QSS = """
QWidget { background-color: #1e1f22; color: #f0f0f0; }
QToolButton { padding: 4px 12px; border-radius: 4px; }
QToolButton:checked { background-color: #3a506b; }
QTableView { background-color: #2b2d31; alternate-background-color: #1f2023; gridline-color: #3c3f45; }
QTableView::item { padding: 6px 12px; }
QHeaderView::section { background-color: #323438; color: #f0f0f0; padding: 4px 12px; border: none; text-align: left; }
QStatusBar { background-color: #2b2d31; }
QLineEdit, QComboBox, QSpinBox { background-color: #2b2d31; border: 1px solid #3c3f45; border-radius: 4px; padding: 4px; }
QRadioButton { padding: 4px 0; }
QRadioButton::indicator { width: 18px; height: 18px; border-radius: 9px; border: 2px solid #4b5563; background-color: #2b2d31; margin-right: 6px; }
QRadioButton::indicator:hover { border-color: #60a5fa; }
QRadioButton::indicator:checked { border: 2px solid #60a5fa; background-color: #60a5fa; }
QRadioButton::indicator:checked:hover { border-color: #38bdf8; }
QRadioButton:focus { outline: none; }
"""


_LIGHT_ICON_PALETTE = IconPalette(
    roles={
        "accent": IconColor(normal="#0f172a", active="#1d4ed8"),
        "danger": IconColor(normal="#1f2937", active="#ef4444"),
        "control": IconColor(normal="#1f2937", active="#2563eb"),
    }
)

_DARK_ICON_PALETTE = IconPalette(
    roles={
        "accent": IconColor(normal="#f3f4f6", active="#38bdf8"),
        "danger": IconColor(normal="#f87171", active="#fca5a5"),
        "control": IconColor(normal="#e5e7eb", active="#60a5fa"),
    }
)


class ThemeManager:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._current: Theme | None = None

    def apply(self, theme: Theme) -> None:
        if theme == self._current:
            return
        LOGGER.info("Applying theme", extra={"event": "ui_theme_apply", "theme": theme.value})
        if theme == Theme.DARK:
            self._apply_dark()
            self._app.setStyleSheet(_DARK_QSS)
            set_icon_palette(_DARK_ICON_PALETTE)
        else:
            self._apply_light()
            self._app.setStyleSheet(_LIGHT_QSS)
            set_icon_palette(_LIGHT_ICON_PALETTE)
        self._current = theme

    def current(self) -> Theme | None:
        return self._current

    def _apply_light(self) -> None:
        palette = self._app.style().standardPalette()
        palette.setColor(QPalette.Window, QColor("#f5f6f8cf"))
        palette.setColor(QPalette.Base, QColor("#ffffff"))
        palette.setColor(QPalette.AlternateBase, QColor("#f1f1f1"))
        palette.setColor(QPalette.Highlight, QColor("#0078d4"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        self._app.setPalette(palette)

    def _apply_dark(self) -> None:
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#1e1f22"))
        palette.setColor(QPalette.WindowText, QColor("#f0f0f0"))
        palette.setColor(QPalette.Base, QColor("#2b2d31"))
        palette.setColor(QPalette.AlternateBase, QColor("#1f2023"))
        palette.setColor(QPalette.ToolTipBase, QColor("#f0f0f0"))
        palette.setColor(QPalette.ToolTipText, QColor("#1e1f22"))
        palette.setColor(QPalette.Text, QColor("#f0f0f0"))
        palette.setColor(QPalette.Button, QColor("#2b2d31"))
        palette.setColor(QPalette.ButtonText, QColor("#f0f0f0"))
        palette.setColor(QPalette.Highlight, QColor("#3a506b"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.BrightText, QColor("#ff6b6b"))
        self._app.setPalette(palette)
