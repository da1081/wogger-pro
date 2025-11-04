"""Detection and tracking for short gaps in recorded work segments."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import portalocker

from .models import Entry
from .paths import ignored_missing_timeslots_path
from .time_segments import minutes_between

LOGGER = logging.getLogger("wogger.missing")


@dataclass(frozen=True, slots=True)
class MissingTimeslot:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return max(1, minutes_between(self.start, self.end))

    def key(self) -> tuple[str, str]:
        return (
            self.start.isoformat(timespec="seconds"),
            self.end.isoformat(timespec="seconds"),
        )


def detect_missing_timeslots(
    entries: Sequence[Entry],
    threshold_minutes: int,
    ignored: Iterable[tuple[str, str]] | None = None,
) -> list[MissingTimeslot]:
    """Return gaps between entries that fall below the configured threshold."""

    if threshold_minutes <= 0:
        return []

    ignored_lookup = set(ignored or [])
    ordered = sorted(entries, key=lambda entry: (entry.segment_start, entry.segment_end))
    if len(ordered) < 2:
        return []

    results: list[MissingTimeslot] = []
    seen: set[tuple[str, str]] = set()
    previous = ordered[0]

    for current in ordered[1:]:
        gap_start = previous.segment_end
        gap_end = current.segment_start
        if gap_end > gap_start:
            gap_minutes = minutes_between(gap_start, gap_end)
            if 0 < gap_minutes <= threshold_minutes:
                slot = MissingTimeslot(start=gap_start, end=gap_end)
                key = slot.key()
                if key not in ignored_lookup and key not in seen:
                    results.append(slot)
                    seen.add(key)
        if current.segment_end > previous.segment_end:
            previous = current

    return results


class MissingTimeslotStore:
    """Persists the list of user-dismissed missing timeslots."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        lock_timeout: float = 5.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else ignored_missing_timeslots_path()
        self._lock_timeout = lock_timeout
        self._logger = logger or logging.getLogger("wogger.missing.store")
        self._ensure_file()

    # ------------------------------------------------------------------
    def refresh_path(self, path: Path | None = None) -> None:
        """Re-point the store at a new file location (e.g. after app data move)."""

        self._path = Path(path) if path is not None else ignored_missing_timeslots_path()
        self._ensure_file()

    def ignored_keys(self) -> set[tuple[str, str]]:
        self._ensure_file()
        try:
            with portalocker.Lock(
                self._path,
                mode="r",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.SHARED,
                encoding="utf-8",
            ) as handle:
                try:
                    payload = json.load(handle)
                except json.JSONDecodeError:
                    self._logger.warning("Ignored missing timeslots file is corrupted; resetting")
                    handle.close()
                    self._reset_file()
                    return set()
        except FileNotFoundError:
            self._reset_file()
            return set()
        except Exception:  # pragma: no cover - defensive
            self._logger.exception("Unable to read ignored missing timeslots file")
            return set()

        ignored: set[tuple[str, str]] = set()
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict):
                continue
            start = item.get("start")
            end = item.get("end")
            if isinstance(start, str) and isinstance(end, str):
                ignored.add((start, end))
        return ignored

    def dismiss(self, timeslot: MissingTimeslot) -> None:
        key = timeslot.key()
        data = list(self._load_payload())
        if any(entry.get("start") == key[0] and entry.get("end") == key[1] for entry in data):
            return
        data.append({"start": key[0], "end": key[1]})
        self._write_payload(data)

    # ------------------------------------------------------------------
    def _ensure_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("[]", encoding="utf-8")

    def _reset_file(self) -> None:
        try:
            self._path.write_text("[]", encoding="utf-8")
        except Exception:
            self._logger.exception("Failed to reset ignored missing timeslots file")

    def _load_payload(self) -> list[dict[str, str]]:
        self._ensure_file()
        try:
            with portalocker.Lock(
                self._path,
                mode="r",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.SHARED,
                encoding="utf-8",
            ) as handle:
                try:
                    payload = json.load(handle)
                    if isinstance(payload, list):
                        return [
                            item for item in payload
                            if isinstance(item, dict)
                            and isinstance(item.get("start"), str)
                            and isinstance(item.get("end"), str)
                        ]
                except json.JSONDecodeError:
                    self._logger.warning("Ignored missing timeslots file is corrupted; resetting")
        except FileNotFoundError:
            self._reset_file()
            return []
        except Exception:  # pragma: no cover - defensive
            self._logger.exception("Unable to load ignored missing timeslots")
            return []

        self._reset_file()
        return []

    def _write_payload(self, payload: list[dict[str, str]]) -> None:
        self._ensure_file()
        try:
            with portalocker.Lock(
                self._path,
                mode="w",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.EXCLUSIVE,
                encoding="utf-8",
            ) as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
        except Exception:  # pragma: no cover - defensive
            self._logger.exception("Unable to persist ignored missing timeslots")