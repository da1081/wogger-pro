"""Helpers for loading UI icons with theme-aware colors."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterable, Mapping

import qtawesome as qta  # type: ignore[import]
from PySide6.QtGui import QIcon

from ..core.paths import app_icon_path


PLUS_ICON_NAMES: tuple[str, ...] = (
    "fa5s.plus",
    "fa6s.plus",
    "mdi.plus",
)

TRASH_ICON_NAMES: tuple[str, ...] = (
    "fa5s.trash",
    "fa5s.trash-alt",
    "fa6s.trash",
    "mdi.trash-can",
    "mdi.trash-can-outline",
)

SETTINGS_ICON_NAMES: tuple[str, ...] = (
    "fa5s.cog",
    "fa5s.cogs",
    "fa6s.gear",
    "mdi.cog",
    "mdi.cog-outline",
)

CALENDAR_ICON_NAMES: tuple[str, ...] = (
    "fa5s.calendar",
    "fa5s.calendar-alt",
    "fa6s.calendar",
    "mdi.calendar",
    "mdi.calendar-outline",
)

IMPORT_ICON_NAMES: tuple[str, ...] = (
    "fa5s.file-import",
    "fa6s.file-import",
    "mdi.database-import",
    "mdi.import",
)

SOUND_ON_ICON_NAMES: tuple[str, ...] = (
    "fa5s.volume-up",
    "fa5s.volume",
    "fa6s.volume-high",
    "mdi.volume-high",
    "mdi.volume-source",
)

SOUND_OFF_ICON_NAMES: tuple[str, ...] = (
    "fa5s.volume-mute",
    "fa5s.volume-off",
    "fa6s.volume-xmark",
    "mdi.volume-off",
    "mdi.volume-variant-off",
)


@dataclass(frozen=True)
class IconColor:
    normal: str
    active: str


@dataclass(frozen=True)
class IconPalette:
    roles: Mapping[str, IconColor] = field(default_factory=dict)

    def color_for(self, role: str) -> IconColor:
        if role in self.roles:
            return self.roles[role]
        raise KeyError(role)


_LOGGER = logging.getLogger("wogger.ui.icons")

_DEFAULT_PALETTE = IconPalette(
    roles={
        "accent": IconColor(normal="#0f172a", active="#1d4ed8"),
        "danger": IconColor(normal="#1f2937", active="#ef4444"),
        "control": IconColor(normal="#1f2937", active="#2563eb"),
    }
)

_current_palette = _DEFAULT_PALETTE

_palette_listeners: set[Callable[[], None]] = set()


def set_icon_palette(palette: IconPalette) -> None:
    """Set the active icon palette used for dynamic theming."""

    global _current_palette
    if palette == _current_palette:
        return
    _current_palette = palette
    _notify_palette_changed()


def add_palette_listener(callback: Callable[[], None]) -> None:
    """Register a callback invoked when the icon palette changes."""

    _palette_listeners.add(callback)


def remove_palette_listener(callback: Callable[[], None]) -> None:
    """Remove a previously registered palette listener."""

    _palette_listeners.discard(callback)


def _notify_palette_changed() -> None:
    for callback in tuple(_palette_listeners):
        try:
            callback()
        except Exception:  # pragma: no cover - listeners should not break theme changes
            _LOGGER.exception("Icon palette listener failed")


@lru_cache(maxsize=1)
def app_icon() -> QIcon | None:
    """Return the global application icon if it exists."""
    icon_path = app_icon_path()
    if icon_path.exists():
        return QIcon(str(icon_path))
    return None


def _resolve_colors(role: str) -> IconColor:
    try:
        return _current_palette.color_for(role)
    except KeyError:
        _LOGGER.warning("Missing icon role '%s' in palette; falling back to accent", role)
        return _DEFAULT_PALETTE.color_for("accent")


def _qtawesome_icon(names: Iterable[str], size: int, role: str) -> QIcon:
    """Return the first renderable QtAwesome icon from ``names`` using themed colors."""

    colors = _resolve_colors(role)
    last_error: Exception | None = None
    for name in names:
        try:
            icon = qta.icon(name, color=colors.normal, color_active=colors.active)
        except Exception as exc:  # pragma: no cover - defensive guard for misnamed glyphs.
            last_error = exc
            continue
        pixmap = icon.pixmap(size, size)
        if not pixmap.isNull():
            return QIcon(pixmap)

    message = f"QtAwesome icon lookup failed for {tuple(names)}"
    if last_error is not None:
        raise RuntimeError(message) from last_error
    raise RuntimeError(message)


def plus_icon(size: int = 24) -> QIcon:
    """Return the QtAwesome plus icon styled for the current theme."""

    return _qtawesome_icon(PLUS_ICON_NAMES, size, role="accent")


def trash_icon(size: int = 20) -> QIcon:
    """Return the QtAwesome trash icon styled for the current theme."""

    return _qtawesome_icon(TRASH_ICON_NAMES, size, role="danger")


def settings_icon(size: int = 20) -> QIcon:
    """Return the QtAwesome settings/gear icon styled for the current theme."""

    return _qtawesome_icon(SETTINGS_ICON_NAMES, size, role="control")


def calendar_icon(size: int = 20) -> QIcon:
    """Return the QtAwesome calendar icon styled for the current theme."""

    return _qtawesome_icon(CALENDAR_ICON_NAMES, size, role="control")


def import_icon(size: int = 20) -> QIcon:
    """Return the import icon styled for the current theme."""

    return _qtawesome_icon(IMPORT_ICON_NAMES, size, role="control")


def sound_on_icon(size: int = 22) -> QIcon:
    """Return the icon used when prompt sounds are enabled."""

    return _qtawesome_icon(SOUND_ON_ICON_NAMES, size, role="control")


def sound_off_icon(size: int = 22) -> QIcon:
    """Return the icon used when prompt sounds are disabled."""

    return _qtawesome_icon(SOUND_OFF_ICON_NAMES, size, role="control")
