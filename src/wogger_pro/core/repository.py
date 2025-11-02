"""Persistence layer for work log entries."""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

import portalocker

from .exceptions import BackupError, PersistenceError
from .models import Entry
from .time_segments import TimeRange
from .paths import backups_dir, entries_path


class EntriesRepository:
    """Handles durable persistence of work log entries."""

    def __init__(
        self,
        path: Path | None = None,
        logger: logging.Logger | None = None,
        lock_timeout: float = 10.0,
    ) -> None:
        self._path = Path(path) if path is not None else entries_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_timeout = lock_timeout
        self._logger = logger or logging.getLogger("wogger.repository")
        self._ensure_file()

    # ------------------------------------------------------------------
    # Public API
    def add_entry(self, task: str, segment_start: datetime, segment_end: datetime, minutes: int) -> Entry:
        entry = Entry(task=task, segment_start=segment_start, segment_end=segment_end, minutes=minutes)
        self._logger.info(
            "Adding entry",
            extra={
                "event": "entries_add_one",
                "task": task,
                "segment_start": segment_start.isoformat(),
                "segment_end": segment_end.isoformat(),
                "minutes": minutes,
                "entry_id": entry.entry_id,
            },
        )
        persisted = self.add_entries_batch([entry])
        return persisted[0]

    def add_entries_batch(self, entries: Sequence[Entry]) -> list[Entry]:
        if not entries:
            return []

        serialized_entries = [json.dumps(entry.to_json_dict(), separators=(",", ":")) for entry in entries]
        self._logger.info(
            "Adding batch of entries",
            extra={
                "event": "entries_add_batch",
                "count": len(entries),
                "entry_ids": [entry.entry_id for entry in entries],
            },
        )

        try:
            with portalocker.Lock(
                self._path,
                mode="a+",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.EXCLUSIVE,
                encoding="utf-8",
            ) as locked_file:
                locked_file.seek(0, os.SEEK_END)
                start_position = locked_file.tell()
                try:
                    for line in serialized_entries:
                        locked_file.write(line)
                        locked_file.write("\n")
                    locked_file.flush()
                    os.fsync(locked_file.fileno())
                except Exception as exc:  # pragma: no cover - defensive logic
                    locked_file.seek(start_position)
                    locked_file.truncate()
                    locked_file.flush()
                    os.fsync(locked_file.fileno())
                    self._logger.exception("Failed to write batch; truncated partial data")
                    raise PersistenceError("Unable to persist entries batch") from exc
        except Exception as exc:
            if isinstance(exc, PersistenceError):
                raise
            self._logger.exception("Unexpected error while writing entries batch")
            raise PersistenceError("Unable to persist entries batch") from exc

        return list(entries)

    def get_all_entries(self) -> list[Entry]:
        self._logger.debug("Loading all entries", extra={"event": "entries_load_all"})
        lines = self._read_lines()
        return self._deserialize_entries(lines)

    def get_entries_by_range(self, start_dt: datetime, end_dt: datetime) -> list[Entry]:
        self._logger.debug(
            "Loading entries by range",
            extra={
                "event": "entries_load_range",
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
        )
        entries = [
            entry
            for entry in self.get_all_entries()
            if entry.segment_start >= start_dt and entry.segment_end <= end_dt
        ]
        return entries

    def get_entries_overlapping(self, start_dt: datetime, end_dt: datetime) -> list[Entry]:
        self._logger.debug(
            "Loading entries overlapping range",
            extra={
                "event": "entries_load_overlap",
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
        )
        try:
            base = TimeRange(start=start_dt, end=end_dt)
        except ValueError:
            return []

        overlapping: list[Entry] = []
        for entry in self.get_all_entries():
            if entry.as_range().overlaps(base):
                overlapping.append(entry)
        return overlapping

    def list_tasks_with_counts(self) -> list[tuple[str, int]]:
        entries = self.get_all_entries()
        counts = Counter(entry.task for entry in entries)
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        self._logger.debug(
            "Task counts computed",
            extra={"event": "entries_task_counts", "task_count": len(ordered)},
        )
        return ordered

    def get_last_entry(self) -> Optional[Entry]:
        entries = self.get_all_entries()
        if not entries:
            return None
        return max(entries, key=lambda entry: (entry.segment_end, entry.segment_start))

    def rename_task(self, old_task: str, new_task: str) -> int:
        old_task = old_task.strip()
        new_task = new_task.strip()
        if not old_task or not new_task:
            raise ValueError("Task names must be non-empty")
        if old_task == new_task:
            return 0

        self._logger.info(
            "Renaming task",
            extra={
                "event": "entries_task_rename",
                "from": old_task,
                "to": new_task,
            },
        )

        try:
            with portalocker.Lock(
                self._path,
                mode="r+",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.EXCLUSIVE,
                encoding="utf-8",
            ) as locked_file:
                locked_file.seek(0)
                lines = [line.rstrip("\n") for line in locked_file if line.strip()]
                entries = self._deserialize_entries(lines)

                updated = 0
                for entry in entries:
                    if entry.task == old_task:
                        entry.task = new_task
                        updated += 1

                if updated == 0:
                    return 0

                locked_file.seek(0)
                locked_file.truncate()
                for entry in entries:
                    serialized = json.dumps(entry.to_json_dict(), separators=(",", ":"))
                    locked_file.write(serialized)
                    locked_file.write("\n")
                locked_file.flush()
                os.fsync(locked_file.fileno())
        except ValueError:
            raise
        except Exception as exc:
            self._logger.exception(
                "Failed to rename task",
                extra={"event": "entries_task_rename_failed", "from": old_task, "to": new_task},
            )
            raise PersistenceError("Unable to rename task entries") from exc

        return updated

    def replace_all_entries(self, entries: Sequence[Entry]) -> None:
        ordered = sorted(entries, key=lambda entry: (entry.segment_start, entry.segment_end, entry.task.lower()))
        try:
            with portalocker.Lock(
                self._path,
                mode="w",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.EXCLUSIVE,
                encoding="utf-8",
            ) as locked_file:
                for entry in ordered:
                    payload = json.dumps(entry.to_json_dict(), separators=(",", ":"))
                    locked_file.write(payload)
                    locked_file.write("\n")
                locked_file.flush()
                os.fsync(locked_file.fileno())
        except Exception as exc:
            self._logger.exception("Failed to replace entries", extra={"event": "entries_replace_failed"})
            raise PersistenceError("Unable to persist imported entries") from exc

    def backup(self) -> Path:
        self._logger.info("Starting backup", extra={"event": "entries_backup_start"})
        target_dir = backups_dir()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target_name = f"WOGGER-{self._path.stem}-{timestamp}-BACKUP{self._path.suffix or '.txt'}"
        target_path = target_dir / target_name

        try:
            with portalocker.Lock(
                self._path,
                mode="r",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.SHARED,
                encoding="utf-8",
            ) as source_file:
                data = source_file.read()

            target_path.write_text(data, encoding="utf-8")
            with target_path.open("r+", encoding="utf-8") as written:
                written.flush()
                os.fsync(written.fileno())
        except Exception as exc:  # pragma: no cover - filesystem dependent
            self._logger.exception("Backup failed", extra={"event": "entries_backup_failed"})
            raise BackupError("Failed to create backup") from exc

        self._logger.info(
            "Backup completed",
            extra={"event": "entries_backup_success", "path": str(target_path)},
        )
        return target_path

    # ------------------------------------------------------------------
    # Internal helpers
    def _ensure_file(self) -> None:
        if not self._path.exists():
            self._logger.debug(
                "Creating entries file",
                extra={"event": "entries_file_init", "path": str(self._path)},
            )
            self._path.touch()

    def _read_lines(self) -> list[str]:
        try:
            with portalocker.Lock(
                self._path,
                mode="r",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.SHARED,
                encoding="utf-8",
            ) as locked_file:
                return [line.rstrip("\n") for line in locked_file if line.strip()]
        except FileNotFoundError:
            self._ensure_file()
            return []
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.exception("Failed reading entries file")
            raise PersistenceError("Unable to read entries") from exc

    def _deserialize_entries(self, lines: Iterable[str]) -> list[Entry]:
        entries: list[Entry] = []
        for index, line in enumerate(lines, start=1):
            try:
                payload = json.loads(line)
                entries.append(Entry.from_json_dict(payload))
            except Exception as exc:  # pragma: no cover - resilience
                self._logger.exception(
                    "Skipping malformed entry",
                    extra={"event": "entries_skip_invalid", "line_index": index},
                )
        return entries
