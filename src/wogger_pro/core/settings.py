"""Settings management for Wogger Pro."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from croniter import croniter

from .exceptions import SettingsError
from .paths import (
    current_app_data_dir,
    default_app_data_dir,
    default_downloads_dir,
    ensure_app_structure,
    recurring_backups_dir,
    settings_path,
)


class Theme(str, Enum):
    LIGHT = "light"
    DARK = "dark"


DEFAULT_PROMPT_CRON = "0,15,30,45 * * * *"
_SUPPORTED_THEMES = {member.value for member in Theme}


@dataclass(slots=True)
class Settings:
    theme: Theme = Theme.DARK
    prompt_cron: str = DEFAULT_PROMPT_CRON
    prompt_sounds_enabled: bool = True
    auto_launch_on_startup: bool = False
    app_data_path: str = field(default_factory=lambda: str(current_app_data_dir()))
    backup_path: str = field(default_factory=lambda: str(default_downloads_dir()))
    recurring_backup_enabled: bool = True
    recurring_backup_interval_days: int = 1
    recurring_backup_retention_days: int = 7
    recurring_backup_path: str = field(default_factory=lambda: str(recurring_backups_dir()))
    missing_timeslot_threshold_minutes: int = 240

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["theme"] = self.theme.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Settings":
        theme_value = str(payload.get("theme", Theme.DARK.value)).lower()
        if theme_value not in _SUPPORTED_THEMES:
            raise SettingsError(f"Unsupported theme: {theme_value}")
        prompt_cron = str(payload.get("prompt_cron", DEFAULT_PROMPT_CRON))
        _validate_cron(prompt_cron)
        prompt_sounds_enabled = bool(payload.get("prompt_sounds_enabled", True))
        auto_launch = bool(payload.get("auto_launch_on_startup", False))
        app_data = str(payload.get("app_data_path") or default_app_data_dir()).strip()
        backup_path = str(payload.get("backup_path") or default_downloads_dir()).strip()
        recurring_enabled = bool(payload.get("recurring_backup_enabled", True))
        interval_days = int(payload.get("recurring_backup_interval_days", 1) or 1)
        retention_days = int(payload.get("recurring_backup_retention_days", 7) or 1)
        recurring_path = str(payload.get("recurring_backup_path") or recurring_backups_dir()).strip()
        missing_threshold = int(payload.get("missing_timeslot_threshold_minutes", 240) or 0)

        if interval_days < 1:
            raise SettingsError("Recurring backup interval must be at least 1 day")
        if retention_days < 1 or retention_days > 100:
            raise SettingsError("Recurring backup retention must be between 1 and 100 days")
        if missing_threshold < 0:
            raise SettingsError("Missing timeslot threshold must be zero or greater")

        return cls(
            theme=Theme(theme_value),
            prompt_cron=prompt_cron,
            prompt_sounds_enabled=prompt_sounds_enabled,
            auto_launch_on_startup=auto_launch,
            app_data_path=app_data,
            backup_path=backup_path,
            recurring_backup_enabled=recurring_enabled,
            recurring_backup_interval_days=interval_days,
            recurring_backup_retention_days=retention_days,
            recurring_backup_path=recurring_path,
            missing_timeslot_threshold_minutes=missing_threshold,
        )


class SettingsManager:
    def __init__(self, path: Path | None = None, logger: logging.Logger | None = None) -> None:
        ensure_app_structure()
        self._path = Path(path) if path is not None else settings_path()
        self._logger = logger or logging.getLogger("wogger.settings")

    def load(self) -> Settings:
        if not self._path.exists():
            self._logger.info(
                "Settings file missing; using defaults",
                extra={"event": "settings_load_default", "path": str(self._path)},
            )
            return Settings()

        try:
            with self._path.open("r", encoding="utf-8") as infile:
                payload = json.load(infile)
        except json.JSONDecodeError as exc:
            self._logger.exception(
                "Invalid JSON in settings file; falling back to defaults",
                extra={"event": "settings_load_invalid_json"},
            )
            raise SettingsError("Settings file is malformed") from exc
        except Exception as exc:
            self._logger.exception("Unexpected error loading settings")
            raise SettingsError("Unable to load settings") from exc

        try:
            settings = Settings.from_dict(payload)
        except SettingsError:
            raise
        except Exception as exc:
            self._logger.exception(
                "Settings payload invalid; reverting to defaults",
                extra={"event": "settings_load_invalid_payload"},
            )
            raise SettingsError("Settings payload is invalid") from exc

        self._logger.info(
            "Settings loaded successfully",
            extra={
                "event": "settings_loaded",
                "theme": settings.theme.value,
                "prompt_cron": settings.prompt_cron,
                "prompt_sounds_enabled": settings.prompt_sounds_enabled,
                "auto_launch_on_startup": settings.auto_launch_on_startup,
                "app_data_path": settings.app_data_path,
                "backup_path": settings.backup_path,
                "recurring_backup_enabled": settings.recurring_backup_enabled,
                "recurring_backup_interval_days": settings.recurring_backup_interval_days,
                "recurring_backup_retention_days": settings.recurring_backup_retention_days,
                "recurring_backup_path": settings.recurring_backup_path,
                "missing_timeslot_threshold_minutes": settings.missing_timeslot_threshold_minutes,
            },
        )
        return settings

    def save(self, settings: Settings) -> None:
        self._logger.info(
            "Saving settings",
            extra={
                "event": "settings_save",
                "theme": settings.theme.value,
                "prompt_cron": settings.prompt_cron,
                "prompt_sounds_enabled": settings.prompt_sounds_enabled,
                "auto_launch_on_startup": settings.auto_launch_on_startup,
                "app_data_path": settings.app_data_path,
                "backup_path": settings.backup_path,
                "recurring_backup_enabled": settings.recurring_backup_enabled,
                "recurring_backup_interval_days": settings.recurring_backup_interval_days,
                "recurring_backup_retention_days": settings.recurring_backup_retention_days,
                "recurring_backup_path": settings.recurring_backup_path,
                "missing_timeslot_threshold_minutes": settings.missing_timeslot_threshold_minutes,
            },
        )
        _validate_cron(settings.prompt_cron)
        if settings.theme.value not in _SUPPORTED_THEMES:
            raise SettingsError(f"Unsupported theme: {settings.theme.value}")
        if settings.recurring_backup_interval_days < 1:
            raise SettingsError("Recurring backup interval must be at least 1 day")
        if settings.recurring_backup_retention_days < 1 or settings.recurring_backup_retention_days > 100:
            raise SettingsError("Recurring backup retention must be between 1 and 100 days")
        if settings.missing_timeslot_threshold_minutes < 0:
            raise SettingsError("Missing timeslot threshold must be zero or greater")

        temp_path = self._path.with_suffix(".tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as outfile:
                json.dump(settings.to_dict(), outfile, indent=2)
                outfile.flush()
                os.fsync(outfile.fileno())
            temp_path.replace(self._path)
        except Exception as exc:
            self._logger.exception("Failed to save settings")
            raise SettingsError("Unable to save settings") from exc

    def update(self, transform: Callable[[Settings], Settings]) -> Settings:
        current = self.load()
        updated = transform(current)
        self.save(updated)
        return updated


def _validate_cron(expression: str) -> None:
    try:
        croniter(expression)
    except Exception as exc:  # pragma: no cover - croniter details
        raise SettingsError(f"Invalid cron expression: {expression}") from exc
