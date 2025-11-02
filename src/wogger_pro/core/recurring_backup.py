"""Automatic recurring backup management."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Sequence

from .backup import create_appdata_backup
from .exceptions import BackupError
from .paths import recurring_backup_log_path

LOGGER = logging.getLogger("wogger.backup.recurring")

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"
MAX_RECURRING_BACKUPS = 100
MAX_LOG_ENTRIES = 300


@dataclass(slots=True)
class BackupLogEntry:
    path: str
    created_at: datetime
    status: str = "success"
    error: str | None = None
    deleted_at: datetime | None = None
    deletion_error: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        payload = {
            "path": self.path,
            "created_at": self.created_at.strftime(ISO_FORMAT),
            "status": self.status,
            "error": self.error,
            "deleted_at": self.deleted_at.strftime(ISO_FORMAT) if self.deleted_at else None,
            "deletion_error": self.deletion_error,
        }
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "BackupLogEntry":
        created_raw = str(payload.get("created_at"))
        created_at = datetime.strptime(created_raw, ISO_FORMAT)
        deleted_raw = payload.get("deleted_at")
        deleted_at = (
            datetime.strptime(str(deleted_raw), ISO_FORMAT)
            if isinstance(deleted_raw, str) and deleted_raw
            else None
        )
        return cls(
            path=str(payload.get("path", "")),
            created_at=created_at,
            status=str(payload.get("status", "success")),
            error=str(payload.get("error", "")) if payload.get("error") else None,
            deleted_at=deleted_at,
            deletion_error=str(payload.get("deletion_error", "")) if payload.get("deletion_error") else None,
        )


@dataclass(slots=True)
class RecurringBackupOutcome:
    attempted: bool
    success: bool
    backup_path: Path | None = None
    error: Exception | None = None


def process_recurring_backups(
    *,
    enabled: bool,
    interval_days: int,
    retention_days: int,
    target_directory: Path,
    now: datetime | None = None,
) -> RecurringBackupOutcome:
    """Handle cleanup and optional creation of a recurring backup."""

    clock = now or datetime.now()
    entries = _load_log_entries()
    changed = _cleanup_outdated_backups(entries, retention_days, clock)
    changed |= _prune_excess_backups(entries, MAX_RECURRING_BACKUPS, clock)
    changed |= _trim_backup_log(entries, MAX_LOG_ENTRIES)

    if not enabled:
        if changed:
            _save_log_entries(entries)
        return RecurringBackupOutcome(attempted=False, success=True)

    due = _backup_due(entries, interval_days, clock)
    if not due:
        if changed:
            _save_log_entries(entries)
        return RecurringBackupOutcome(attempted=False, success=True)

    target_directory.mkdir(parents=True, exist_ok=True)

    try:
        backup_path = create_appdata_backup(target_directory, exclude=[target_directory])
    except BackupError as exc:
        LOGGER.exception("Automatic backup failed", extra={"event": "backup_recurring_failed"})
        entry = BackupLogEntry(
            path=str(target_directory / f"FAILED-{clock.strftime(ISO_FORMAT)}"),
            created_at=clock,
            status="failed",
            error=str(exc),
        )
        entries.append(entry)
        _trim_backup_log(entries, MAX_LOG_ENTRIES)
        _save_log_entries(entries)
        return RecurringBackupOutcome(attempted=True, success=False, error=exc)

    entry = BackupLogEntry(path=str(backup_path), created_at=clock, status="success")
    entries.append(entry)
    _trim_backup_log(entries, MAX_LOG_ENTRIES)
    _save_log_entries(entries)
    LOGGER.info(
        "Recurring backup created",
        extra={"event": "backup_recurring_success", "path": str(backup_path)},
    )
    return RecurringBackupOutcome(attempted=True, success=True, backup_path=backup_path)


def _backup_due(entries: Sequence[BackupLogEntry], interval_days: int, now: datetime) -> bool:
    if interval_days < 1:
        interval_days = 1
    threshold = timedelta(days=interval_days)
    last_entry = _last_completed_backup(entries)
    if last_entry is None:
        return True
    return now - last_entry.created_at >= threshold


def _last_completed_backup(entries: Sequence[BackupLogEntry]) -> BackupLogEntry | None:
    for entry in sorted(entries, key=lambda item: item.created_at, reverse=True):
        if entry.status in {"success", "deleted", "delete_failed"}:
            return entry
    return None


def _cleanup_outdated_backups(entries: Sequence[BackupLogEntry], retention_days: int, now: datetime) -> bool:
    if retention_days < 1:
        retention_days = 1
    cutoff = now - timedelta(days=retention_days)
    changed = False
    for entry in entries:
        if entry.created_at <= cutoff and entry.status in {"success", "delete_failed"} and entry.deleted_at is None:
            backup_path = Path(entry.path)
            try:
                if backup_path.exists():
                    backup_path.unlink()
                entry.status = "deleted"
                entry.deleted_at = now
                entry.deletion_error = None
                changed = True
                LOGGER.info(
                    "Recurring backup pruned",
                    extra={"event": "backup_recurring_pruned", "path": entry.path},
                )
            except Exception as exc:  # pragma: no cover - filesystem dependent
                entry.status = "delete_failed"
                entry.deletion_error = str(exc)
                LOGGER.exception(
                    "Failed to delete old recurring backup",
                    extra={"event": "backup_recurring_prune_failed", "path": entry.path},
                )
                changed = True
    return changed


def _prune_excess_backups(
    entries: Sequence[BackupLogEntry],
    max_backups: int,
    now: datetime,
) -> bool:
    if max_backups < 1:
        max_backups = 1
    active = [
        entry
        for entry in entries
        if entry.status in {"success", "delete_failed"} and entry.deleted_at is None
    ]
    if len(active) <= max_backups:
        return False

    active.sort(key=lambda item: item.created_at)
    changed = False
    while len(active) > max_backups:
        entry = active.pop(0)
        backup_path = Path(entry.path)
        try:
            if backup_path.exists():
                backup_path.unlink()
            entry.status = "deleted"
            entry.deleted_at = now
            entry.deletion_error = None
            LOGGER.info(
                "Recurring backup pruned to enforce limit",
                extra={"event": "backup_recurring_pruned_limit", "path": entry.path},
            )
        except Exception as exc:  # pragma: no cover - filesystem dependent
            entry.status = "delete_failed"
            entry.deletion_error = str(exc)
            LOGGER.exception(
                "Failed to delete recurring backup while enforcing max count",
                extra={"event": "backup_recurring_limit_failed", "path": entry.path},
            )
        changed = True
    return changed


def _trim_backup_log(entries: List[BackupLogEntry], max_entries: int) -> bool:
    if max_entries < 1:
        max_entries = 1
    if len(entries) <= max_entries:
        return False

    removed = 0
    while len(entries) > max_entries:
        oldest = min(entries, key=lambda item: item.created_at)
        entries.remove(oldest)
        removed += 1

    if removed:
        LOGGER.info(
            "Recurring backup log trimmed",
            extra={"event": "backup_recurring_log_trimmed", "removed": removed},
        )
    return removed > 0


def _load_log_entries() -> List[BackupLogEntry]:
    log_path = recurring_backup_log_path()
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("[]", encoding="utf-8")
        return []
    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Log payload is not a list")
    except Exception:
        LOGGER.exception("Recurring backup log corrupted; resetting", extra={"event": "backup_recurring_log_corrupt"})
        log_path.write_text("[]", encoding="utf-8")
        return []

    entries: List[BackupLogEntry] = []
    for payload in raw:
        if not isinstance(payload, dict):
            continue
        try:
            entries.append(BackupLogEntry.from_dict(payload))
        except Exception:
            LOGGER.debug("Skipping malformed recurring backup log entry", exc_info=True)
    return entries


def _save_log_entries(entries: Sequence[BackupLogEntry]) -> None:
    log_path = recurring_backup_log_path()
    payload = [entry.to_dict() for entry in entries]
    log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
