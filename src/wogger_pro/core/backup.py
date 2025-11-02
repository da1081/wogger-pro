"""Utilities for creating Wogger Pro backups."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Sequence
from zipfile import ZIP_DEFLATED, ZipFile

from .exceptions import BackupError
from .paths import app_data_dir, default_downloads_dir

LOGGER = logging.getLogger("wogger.backup")


def create_appdata_backup(
    target_directory: Path | str | None = None,
    *,
    exclude: Sequence[Path] | None = None,
) -> Path:
    """Create a ZIP archive of the entire app data directory.

    The archive is written to ``target_directory`` if provided; otherwise the user's Downloads folder
    is used.
    """
    source_dir = app_data_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_name = f"wogger-proi-backup-{timestamp}.zip"
    destination_dir = Path(target_directory).expanduser() if target_directory else default_downloads_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    target_path = destination_dir / archive_name
    target_path_resolved = target_path.resolve()
    excluded_roots = [_resolve_path(path) for path in exclude] if exclude else []

    LOGGER.info(
        "Starting app data backup",
        extra={"event": "backup_start", "source": str(source_dir), "target": str(target_path)},
    )

    try:
        if target_path.exists():
            target_path.unlink()

        with ZipFile(target_path, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(source_dir, source_dir.name + "/")
            for item in source_dir.rglob("*"):
                if item.is_dir():
                    if _is_excluded(item, excluded_roots):
                        continue
                    continue
                if item.resolve() == target_path_resolved:
                    continue
                if _is_excluded(item, excluded_roots):
                    continue
                arcname = (Path(source_dir.name) / item.relative_to(source_dir)).as_posix()
                archive.write(item, arcname)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        LOGGER.exception("Backup failed", extra={"event": "backup_failed"})
        raise BackupError("Failed to create app data backup") from exc

    LOGGER.info(
        "Backup completed",
        extra={"event": "backup_completed", "path": str(target_path)},
    )
    return target_path


def _resolve_path(path: Path) -> Path:
    return Path(path).expanduser().resolve()


def _is_excluded(path: Path, excluded_roots: Sequence[Path]) -> bool:
    if not excluded_roots:
        return False
    candidate = path.resolve()
    for root in excluded_roots:
        if _is_relative_to(candidate, root):
            return True
    return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
