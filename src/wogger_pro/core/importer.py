"""Utilities for importing entries from legacy Wogger CSV and JF LoggR exports."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from .models import Entry
from .time_segments import TimeRange, subtract

REQUIRED_COLUMNS = {"date", "start time", "end time", "duration (min)", "task"}


class ImportValidationError(ValueError):
    """Raised when an imported CSV file fails validation."""


@dataclass(slots=True)
class MergeResult:
    merged_entries: list[Entry]
    applied_import_entries: list[Entry]
    discarded_import_count: int
    discarded_import_minutes: int
    overlapped_import_count: int
    existing_entries_trimmed: int
    existing_minutes_removed: int


def parse_wogger_csv(path: Path) -> list[Entry]:
    if not path.exists():
        raise ImportValidationError(f"File does not exist: {path}")
    if path.suffix.lower() != ".csv":
        raise ImportValidationError("Selected file is not a CSV document.")

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ImportValidationError("CSV file is missing a header row.")
            header_lookup = {name.strip().lower(): name for name in reader.fieldnames if name}
            missing = REQUIRED_COLUMNS - set(header_lookup)
            if missing:
                raise ImportValidationError(
                    "CSV file is missing required columns: " + ", ".join(sorted(missing))
                )

            entries: list[Entry] = []
            for index, row in enumerate(reader, start=2):
                try:
                    entry = _row_to_entry(row, header_lookup)
                except ImportValidationError as exc:  # re-wrap with row context
                    raise ImportValidationError(f"Row {index}: {exc}") from exc
                entries.append(entry)
    except ImportValidationError:
        raise
    except Exception as exc:  # pragma: no cover - unexpected parsing failure
        raise ImportValidationError(f"Unable to read CSV file: {exc}") from exc

    if not entries:
        raise ImportValidationError("The CSV file does not contain any entries to import.")

    entries.sort(key=_entry_key)
    return entries


def merge_entries(
    existing: Sequence[Entry],
    imported: Sequence[Entry],
    prefer_imported: bool,
) -> MergeResult:
    existing_sorted = sorted(existing, key=_entry_key)
    imported_sorted = sorted(imported, key=_entry_key)

    existing_ranges = [entry.as_range() for entry in existing_sorted]
    import_ranges = [entry.as_range() for entry in imported_sorted]

    overlapped_import_count = _count_overlaps(import_ranges, existing_ranges)

    if prefer_imported:
        merged, trimmed_info = _prefer_imported_merge(existing_sorted, imported_sorted, import_ranges)
        return MergeResult(
            merged_entries=merged,
            applied_import_entries=list(imported_sorted),
            discarded_import_count=0,
            discarded_import_minutes=0,
            overlapped_import_count=overlapped_import_count,
            existing_entries_trimmed=trimmed_info[0],
            existing_minutes_removed=trimmed_info[1],
        )

    merged, applied_entries, discarded_count, discarded_minutes = _prefer_existing_merge(
        existing_sorted,
        imported_sorted,
        existing_ranges,
    )

    return MergeResult(
        merged_entries=merged,
        applied_import_entries=applied_entries,
        discarded_import_count=discarded_count,
        discarded_import_minutes=discarded_minutes,
        overlapped_import_count=overlapped_import_count,
        existing_entries_trimmed=0,
        existing_minutes_removed=0,
    )


def _row_to_entry(row: dict[str, str], header_lookup: dict[str, str]) -> Entry:
    date_value = _cell(row, header_lookup, "date")
    start_value = _cell(row, header_lookup, "start time")
    end_value = _cell(row, header_lookup, "end time")
    duration_value = _cell(row, header_lookup, "duration (min)")
    task_value = _cell(row, header_lookup, "task")

    task = task_value.strip()
    if not task:
        raise ImportValidationError("Task cannot be empty.")

    date_obj = _parse_date(date_value)
    start_time = _parse_time(start_value)
    start = datetime.combine(date_obj, start_time)

    try:
        duration_minutes = int(duration_value)
    except ValueError as exc:
        raise ImportValidationError("Duration (min) must be an integer.") from exc
    if duration_minutes <= 0:
        raise ImportValidationError("Duration (min) must be positive.")

    expected_end = start + timedelta(minutes=duration_minutes)
    end_time = _parse_time(end_value)
    end = datetime.combine(date_obj, end_time)

    if end <= start:
        # Assume the entry wrapped to the next day only when contiguous.
        end = start + timedelta(minutes=duration_minutes)
    else:
        delta_minutes = int((end - start).total_seconds() // 60)
        if abs(delta_minutes - duration_minutes) > 1:
            raise ImportValidationError("End time does not match duration.")
        # Prefer the precise duration from the CSV when they align.
        end = start + timedelta(minutes=duration_minutes)

    return Entry(task=task, segment_start=start, segment_end=end, minutes=duration_minutes)


def _cell(row: dict[str, str], header_lookup: dict[str, str], key: str) -> str:
    return row.get(header_lookup[key], "").strip()


def _parse_date(value: str) -> datetime.date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ImportValidationError(f"Invalid date value: {value!r}")


def _parse_time(value: str) -> datetime.time:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue
    raise ImportValidationError(f"Invalid time value: {value!r}")


def _entry_key(entry: Entry):
    return entry.segment_start, entry.segment_end, entry.task.lower()


def parse_jf_loggr_json(path: Path) -> list[Entry]:
    """Parse entries from a JF LoggR JSON export."""

    if not path.exists():
        raise ImportValidationError(f"File does not exist: {path}")
    if path.suffix.lower() != ".json":
        raise ImportValidationError("Selected file is not a JSON document.")

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except ImportValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise ImportValidationError(f"Invalid JSON format: {exc.msg} (line {exc.lineno})") from exc
    except Exception as exc:  # pragma: no cover - unexpected parsing failure
        raise ImportValidationError(f"Unable to read JSON file: {exc}") from exc

    entries_payload = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries_payload, list):
        raise ImportValidationError("JSON file is missing an 'entries' list.")

    entries: list[Entry] = []
    for index, item in enumerate(entries_payload, start=1):
        if not isinstance(item, dict):
            raise ImportValidationError(f"Entry {index}: expected an object.")
        try:
            entry = _jf_loggr_item_to_entry(item)
        except ImportValidationError as exc:
            raise ImportValidationError(f"Entry {index}: {exc}") from exc
        entries.append(entry)

    if not entries:
        raise ImportValidationError("The JSON file does not contain any entries to import.")

    entries.sort(key=_entry_key)
    return entries


def _jf_loggr_item_to_entry(payload: dict[str, object]) -> Entry:
    try:
        day_value = str(payload["day"]).strip()
    except KeyError as exc:
        raise ImportValidationError("Missing 'day' field.") from exc
    except Exception as exc:
        raise ImportValidationError("Invalid 'day' value.") from exc
    if not day_value:
        raise ImportValidationError("Day cannot be empty.")

    try:
        start_value = str(payload["start"]).strip()
    except KeyError as exc:
        raise ImportValidationError("Missing 'start' field.") from exc
    except Exception as exc:
        raise ImportValidationError("Invalid 'start' value.") from exc
    if not start_value:
        raise ImportValidationError("Start time cannot be empty.")

    try:
        end_value = str(payload["end"]).strip()
    except KeyError as exc:
        raise ImportValidationError("Missing 'end' field.") from exc
    except Exception as exc:
        raise ImportValidationError("Invalid 'end' value.") from exc
    if not end_value:
        raise ImportValidationError("End time cannot be empty.")

    description = payload.get("description", "")
    task = str(description).strip()
    if not task:
        raise ImportValidationError("Description cannot be empty.")

    date_obj = _parse_date(day_value)
    start_time = _parse_time(start_value)
    end_time = _parse_time(end_value)

    start = datetime.combine(date_obj, start_time)
    end = datetime.combine(date_obj, end_time)

    if end <= start:
        raise ImportValidationError("End time must be after start time.")

    delta_seconds = int((end - start).total_seconds())
    if delta_seconds <= 0:
        raise ImportValidationError("Entry duration must be positive.")
    if delta_seconds % 60 != 0:
        raise ImportValidationError("Entry duration must be in whole minutes.")

    minutes = delta_seconds // 60

    category_raw = payload.get("category", "")
    category = str(category_raw).strip() or None

    return Entry(
        task=task,
        segment_start=start,
        segment_end=end,
        minutes=minutes,
        category=category,
    )


def _prefer_imported_merge(
    existing_entries: Sequence[Entry],
    imported_entries: Sequence[Entry],
    import_ranges: Sequence[TimeRange],
) -> tuple[list[Entry], tuple[int, int]]:
    trimmed_entries: list[Entry] = []
    trimmed_count = 0
    minutes_removed = 0

    for entry in existing_entries:
        base_range = entry.as_range()
        remainders = subtract(base_range, import_ranges)

        if len(remainders) == 1 and _range_equals(remainders[0], base_range):
            trimmed_entries.append(entry)
            continue

        if not remainders:
            trimmed_count += 1
            minutes_removed += entry.minutes
            continue

        trimmed_count += 1
        remaining_minutes = 0
        for rng in remainders:
            minutes = _range_minutes(rng)
            remaining_minutes += minutes
            trimmed_entries.append(_entry_from_range(entry.task, rng, minutes))

        removed = max(0, entry.minutes - remaining_minutes)
        minutes_removed += removed

    merged = trimmed_entries + list(imported_entries)
    merged.sort(key=_entry_key)
    return merged, (trimmed_count, minutes_removed)


def _prefer_existing_merge(
    existing_entries: Sequence[Entry],
    imported_entries: Sequence[Entry],
    existing_ranges: Sequence[TimeRange],
) -> tuple[list[Entry], list[Entry], int, int]:
    merged_entries = list(existing_entries)
    applied_entries: list[Entry] = []
    occupied_ranges = list(existing_ranges)
    discarded_count = 0
    discarded_minutes = 0

    for entry in imported_entries:
        base_range = entry.as_range()
        remainders = subtract(base_range, occupied_ranges)

        if not remainders:
            discarded_count += 1
            discarded_minutes += entry.minutes
            continue

        total_remainder_minutes = 0
        for rng in remainders:
            minutes = _range_minutes(rng)
            total_remainder_minutes += minutes
            new_entry = _entry_from_range(entry.task, rng, minutes)
            applied_entries.append(new_entry)
            merged_entries.append(new_entry)
            occupied_ranges.append(rng)

        trimmed_minutes = max(0, entry.minutes - total_remainder_minutes)
        discarded_minutes += trimmed_minutes

    merged_entries.sort(key=_entry_key)
    return merged_entries, applied_entries, discarded_count, discarded_minutes


def _count_overlaps(targets: Sequence[TimeRange], others: Sequence[TimeRange]) -> int:
    count = 0
    if not targets or not others:
        return 0
    for target in targets:
        if any(target.overlaps(other) for other in others):
            count += 1
    return count


def _range_equals(left: TimeRange, right: TimeRange) -> bool:
    return left.start == right.start and left.end == right.end


def _range_minutes(rng: TimeRange) -> int:
    minutes = int((rng.end - rng.start).total_seconds() // 60)
    return max(1, minutes)


def _entry_from_range(task: str, rng: TimeRange, minutes: int) -> Entry:
    return Entry(task=task, segment_start=rng.start, segment_end=rng.end, minutes=minutes)
