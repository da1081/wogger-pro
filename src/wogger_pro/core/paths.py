"""Filesystem path utilities for Wogger Pro."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "wogger-pro"
ENTRIES_FILENAME = "entries.txt"
SETTINGS_FILENAME = "settings.json"
LOG_FILENAME = "wogger.log"
BACKUPS_DIRNAME = "backups"
RECURRING_BACKUPS_DIRNAME = "recurring"
RECURRING_BACKUP_LOG_FILENAME = "recurring-backup-log.json"
RESOURCES_DIRNAME = "resources"
APP_ICON_FILENAME = "wogger.ico"
ALERT_SOUND_FILENAME = "wogger.wav"
CATEGORIES_FILENAME = "categories.json"
IGNORED_MISSING_TIMESLOTS_FILENAME = "ignored-missing-timeslots.json"
FEATURES_FILENAME = "features.json"
DATA_POINTER_FILENAME = f"{APP_NAME}-data-dir.json"

_DATA_DIR_OVERRIDE: Path | None = None


def _roaming_root() -> Path:
    """Return the user's roaming application data directory."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    # Fallback for non-Windows or missing APPDATA
    return Path.home() / "AppData" / "Roaming"


def default_app_data_dir() -> Path:
    return _roaming_root() / APP_NAME


def _override_file() -> Path:
    return _roaming_root() / DATA_POINTER_FILENAME


def _load_override() -> Path | None:
    pointer = _override_file()
    if not pointer.exists():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except Exception:
        return None
    raw_path = data.get("path") if isinstance(data, dict) else None
    if not raw_path:
        return None
    return Path(raw_path)


def _store_override(path: Path | None) -> None:
    pointer = _override_file()
    if path is None:
        with contextlib.suppress(FileNotFoundError):
            pointer.unlink()
        return
    pointer.parent.mkdir(parents=True, exist_ok=True)
    payload = {"path": str(path)}
    pointer.write_text(json.dumps(payload), encoding="utf-8")


def current_app_data_dir() -> Path:
    override = _DATA_DIR_OVERRIDE if _DATA_DIR_OVERRIDE is not None else _load_override()
    return override or default_app_data_dir()


def set_app_data_directory(path: Path | str | None) -> Path:
    global _DATA_DIR_OVERRIDE
    target = Path(path).expanduser() if path else None
    _DATA_DIR_OVERRIDE = target
    app_data_dir.cache_clear()
    if target is not None:
        target.mkdir(parents=True, exist_ok=True)
    _store_override(target)
    return app_data_dir()


def reset_app_data_directory() -> Path:
    return set_app_data_directory(None)


@lru_cache(maxsize=1)
def app_data_dir() -> Path:
    """Return the base application data directory, ensuring it exists."""
    global _DATA_DIR_OVERRIDE
    if _DATA_DIR_OVERRIDE is None:
        _DATA_DIR_OVERRIDE = _load_override()
    base = _DATA_DIR_OVERRIDE or default_app_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return app_data_dir() / SETTINGS_FILENAME


def entries_path() -> Path:
    return app_data_dir() / ENTRIES_FILENAME


def log_path() -> Path:
    return app_data_dir() / LOG_FILENAME


def backups_dir() -> Path:
    path = app_data_dir() / BACKUPS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def recurring_backups_dir() -> Path:
    path = backups_dir() / RECURRING_BACKUPS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def recurring_backup_log_path() -> Path:
    return app_data_dir() / RECURRING_BACKUP_LOG_FILENAME


def ensure_app_structure() -> None:
    """Proactively create the directory structure the app relies on."""
    app_data_dir()
    backups_dir()
    recurring_backups_dir()
    log_file = recurring_backup_log_path()
    if not log_file.exists():
        log_file.write_text("[]", encoding="utf-8")
    categories_file = categories_path()
    if not categories_file.exists():
        categories_file.write_text("[]", encoding="utf-8")
    ignore_file = ignored_missing_timeslots_path()
    if not ignore_file.exists():
        ignore_file.write_text("[]", encoding="utf-8")


def default_downloads_dir() -> Path:
    path = Path.home() / "Downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def app_icon_path() -> Path:
    """Locate the application icon within the project tree."""
    candidates = []

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        base = Path(bundle_root)
        candidates.extend([
            base / APP_ICON_FILENAME,
            base / RESOURCES_DIRNAME / APP_ICON_FILENAME,
        ])

    package_root = Path(__file__).resolve().parent.parent
    project_root = package_root.parent.parent

    candidates.extend([
        package_root.parent / APP_ICON_FILENAME,
        project_root / APP_ICON_FILENAME,
        project_root / RESOURCES_DIRNAME / APP_ICON_FILENAME,
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Fallback to the resources directory path even if it does not yet exist.
    return project_root / RESOURCES_DIRNAME / APP_ICON_FILENAME


@lru_cache(maxsize=1)
def alert_sound_path() -> Path:
    """Locate the alert sound file within the project tree."""
    candidates = []

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        base = Path(bundle_root)
        candidates.extend([
            base / ALERT_SOUND_FILENAME,
            base / RESOURCES_DIRNAME / ALERT_SOUND_FILENAME,
        ])

    package_root = Path(__file__).resolve().parent.parent
    project_root = package_root.parent.parent

    candidates.extend([
        package_root.parent / ALERT_SOUND_FILENAME,
        project_root / ALERT_SOUND_FILENAME,
        project_root / RESOURCES_DIRNAME / ALERT_SOUND_FILENAME,
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return project_root / RESOURCES_DIRNAME / ALERT_SOUND_FILENAME


def categories_path() -> Path:
    return app_data_dir() / CATEGORIES_FILENAME


def ignored_missing_timeslots_path() -> Path:
    return app_data_dir() / IGNORED_MISSING_TIMESLOTS_FILENAME


def features_path() -> Path:
    return app_data_dir() / FEATURES_FILENAME
