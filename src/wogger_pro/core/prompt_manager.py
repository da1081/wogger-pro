"""Manages prompt lifecycle and persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

from PySide6.QtCore import QObject, Signal

from .exceptions import PersistenceError, SegmentConflictError
from .models import Entry, ScheduledSegment, SplitPart
from .segment_utils import SegmentConflict, compute_conflicts, ensure_half_open
from .time_segments import TimeRange, minutes_between, sort_ranges, subtract
from .repository import EntriesRepository
from .scheduler import PromptScheduler


class PromptManager(QObject):
    """Coordinates scheduled prompts and persists user responses."""

    prompt_ready: Signal = Signal(object)
    segment_completed: Signal = Signal(str, object)
    segment_split: Signal = Signal(str, object)
    segment_dismissed: Signal = Signal(str)
    error_occurred: Signal = Signal(object)
    manual_entry_saved: Signal = Signal(object)
    entries_replaced: Signal = Signal()

    def __init__(
        self,
        scheduler: PromptScheduler,
        repository: EntriesRepository,
        logger: Optional[logging.Logger] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._scheduler = scheduler
        self._repository = repository
        self._logger = logger or logging.getLogger("wogger.prompts")
        self._pending_segments: dict[str, ScheduledSegment] = {}
        self._last_task: Optional[str] = None

        self._scheduler.segment_ready.connect(self._handle_segment_ready)

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._logger.info("Prompt manager starting", extra={"event": "prompt_manager_start"})
        self._scheduler.start()

    def stop(self) -> None:
        self._logger.info("Prompt manager stopping", extra={"event": "prompt_manager_stop"})
        self._scheduler.stop()

    def pending_segments(self) -> Sequence[ScheduledSegment]:
        return tuple(self._pending_segments.values())

    def last_task(self) -> Optional[str]:
        if self._last_task:
            return self._last_task
        last_entry = self._repository.get_last_entry()
        if last_entry:
            self._last_task = last_entry.task
            return self._last_task
        return None

    def task_suggestions(self) -> list[tuple[str, int]]:
        return self._repository.list_tasks_with_counts()

    def update_repository(self, repository: EntriesRepository) -> None:
        """Swap the underlying repository used for persistence."""
        self._repository = repository

    def rename_task(self, old_task: str, new_task: str) -> int:
        updated = self._repository.rename_task(old_task, new_task)
        if updated and self._last_task == old_task:
            self._last_task = new_task
        return updated

    def set_task_category(self, task: str, category: str | None) -> int:
        return self._repository.assign_category_to_task(task, category)

    def refresh_last_task(self) -> None:
        last_entry = self._repository.get_last_entry()
        self._last_task = last_entry.task if last_entry else None

    def notify_entries_replaced(self) -> None:
        """Notify listeners that the repository entries have been replaced."""

        self.entries_replaced.emit()

    def range_conflicts(self, start: datetime, end: datetime) -> list[SegmentConflict]:
        base = ensure_half_open(start, end)
        entries = self._repository.get_entries_overlapping(base.start, base.end)
        return compute_conflicts(base, entries)

    def segment_remainders(self, segment: ScheduledSegment) -> list[TimeRange]:
        entries = self._repository.get_entries_overlapping(segment.segment_start, segment.segment_end)
        base = segment.as_range()
        subtractors = [entry.as_range() for entry in entries]
        if not subtractors:
            return [base]
        return sort_ranges(subtract(base, subtractors))

    def manual_entry_defaults(self) -> tuple[datetime, datetime]:
        now = datetime.now().replace(second=0, microsecond=0)
        last = self._repository.get_last_entry()
        if last is not None:
            start = last.segment_end
        else:
            start = now - timedelta(minutes=15)
        if start >= now:
            now = start + timedelta(minutes=1)
        return start, now

    def record_manual_entry(self, task: str, start: datetime, end: datetime) -> Entry:
        conflicts = self.range_conflicts(start, end)
        if conflicts:
            self._logger.warning(
                "Manual entry overlaps existing entries",
                extra={
                    "event": "manual_entry_conflict",
                    "conflict_count": len(conflicts),
                },
            )
            raise SegmentConflictError("The selected time range overlaps an existing entry.")

        duration_minutes = minutes_between(start, end)
        if duration_minutes < 1:
            raise ValueError("Manual entry must be at least one minute long")

        try:
            entry = self._repository.add_entry(task, start, end, duration_minutes)
        except PersistenceError:
            self._logger.exception("Failed to persist manual entry", extra={"event": "manual_entry_failed"})
            raise

        self._last_task = task
        self._logger.info(
            "Manual entry recorded",
            extra={
                "event": "manual_entry_saved",
                "task": task,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "minutes": duration_minutes,
                "entry_id": entry.entry_id,
            },
        )
        self.manual_entry_saved.emit(entry)
        return entry

    def restrict_segment_to_range(self, segment_id: str, remainder: TimeRange) -> ScheduledSegment:
        segment = self._pending_segments.get(segment_id)
        if not segment:
            raise KeyError(f"Unknown segment: {segment_id}")
        segment.segment_start = remainder.start
        segment.segment_end = remainder.end
        segment.minutes = max(1, minutes_between(remainder.start, remainder.end))
        self._logger.debug(
            "Segment restricted to remainder",
            extra={
                "event": "segment_restricted",
                "segment_id": segment.segment_id,
                "start": remainder.start.isoformat(),
                "end": remainder.end.isoformat(),
                "minutes": segment.minutes,
            },
        )
        return segment

    def create_virtual_segment(self, start: datetime, end: datetime) -> ScheduledSegment:
        minutes = max(1, minutes_between(start, end))
        segment = ScheduledSegment(segment_start=start, segment_end=end, minutes=minutes)
        self._pending_segments[segment.segment_id] = segment
        self._logger.debug(
            "Virtual remainder segment created",
            extra={
                "event": "segment_virtual_created",
                "segment_id": segment.segment_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "minutes": minutes,
            },
        )
        return segment

    def log_remainder_entries(self, segment_id: str, remainders: Sequence[TimeRange], tasks: Sequence[str]) -> list[Entry]:
        if len(remainders) != len(tasks):
            raise ValueError("Task assignments must match remainder count")
        segment = self._pending_segments.pop(segment_id, None)
        if segment is None:
            raise KeyError(f"Unknown segment: {segment_id}")

        entries: list[Entry] = []
        for rng, task in zip(remainders, tasks):
            task = task.strip()
            if not task:
                raise ValueError("Task is required for each remainder")
            minutes = minutes_between(rng.start, rng.end)
            if minutes < 1:
                raise ValueError("Remainder duration must be at least one minute")
            entries.append(Entry(task=task, segment_start=rng.start, segment_end=rng.end, minutes=minutes))

        try:
            persisted = self._repository.add_entries_batch(entries)
        except PersistenceError:
            self._logger.exception(
                "Failed to persist remainder entries",
                extra={"event": "remainder_persist_failed", "segment_id": segment_id},
            )
            self._pending_segments[segment_id] = segment
            raise

        self._last_task = tasks[-1]
        self._logger.info(
            "Remainder entries saved",
            extra={
                "event": "remainder_entries_saved",
                "segment_id": segment_id,
                "count": len(persisted),
                "entry_ids": [entry.entry_id for entry in persisted],
            },
        )
        self.segment_split.emit(segment_id, persisted)
        return persisted

    def complete_segment(self, segment_id: str, task: str) -> Entry:
        segment = self._pop_segment(segment_id)
        try:
            entry = self._repository.add_entry(task, segment.segment_start, segment.segment_end, segment.minutes)
        except PersistenceError as exc:
            self._logger.exception(
                "Failed to persist segment",
                extra={"event": "prompt_persist_failed", "segment_id": segment.segment_id},
            )
            self._pending_segments[segment.segment_id] = segment
            self.error_occurred.emit(exc)
            raise

        self._last_task = task
        self._logger.info(
            "Segment completed",
            extra={
                "event": "prompt_completed",
                "segment_id": segment.segment_id,
                "task": task,
                "minutes": segment.minutes,
            },
        )
        self.segment_completed.emit(segment.segment_id, entry)
        return entry

    def split_segment(self, segment_id: str, parts: Iterable[SplitPart]) -> list[Entry]:
        segment = self._pop_segment(segment_id)
        parts_list = list(parts)
        if len(parts_list) < 2:
            raise ValueError("At least two parts required for split")
        total_minutes = sum(part.minutes for part in parts_list)
        if total_minutes != segment.minutes:
            raise ValueError("Split minutes must sum to segment length")
        if any(part.minutes < 1 for part in parts_list):
            raise ValueError("Split minutes must be >= 1")

        entries_to_persist: list[Entry] = []
        cursor = segment.segment_start
        for part in parts_list:
            next_cursor = cursor + timedelta(minutes=part.minutes)
            entries_to_persist.append(
                Entry(task=part.task, segment_start=cursor, segment_end=next_cursor, minutes=part.minutes)
            )
            cursor = next_cursor

        try:
            persisted = self._repository.add_entries_batch(entries_to_persist)
        except PersistenceError as exc:
            self._logger.exception(
                "Failed to persist split entries",
                extra={"event": "prompt_split_failed", "segment_id": segment.segment_id},
            )
            self._pending_segments[segment.segment_id] = segment
            self.error_occurred.emit(exc)
            raise

        self._last_task = parts_list[-1].task
        self._logger.info(
            "Segment split saved",
            extra={
                "event": "prompt_split_saved",
                "segment_id": segment.segment_id,
                "entries": [entry.entry_id for entry in persisted],
            },
        )
        self.segment_split.emit(segment.segment_id, persisted)
        return persisted

    def dismiss_segment(self, segment_id: str, reason: str | None = None) -> None:
        segment = self._pending_segments.pop(segment_id, None)
        if not segment:
            return
        self._logger.info(
            "Segment dismissed",
            extra={
                "event": "prompt_dismissed",
                "segment_id": segment.segment_id,
                "reason": reason or "",
            },
        )
        self.segment_dismissed.emit(segment_id)

    def requeue_segment(self, segment: ScheduledSegment) -> None:
        self._pending_segments[segment.segment_id] = segment
        self._logger.info(
            "Segment requeued",
            extra={"event": "prompt_requeued", "segment_id": segment.segment_id},
        )
        self.prompt_ready.emit(segment)

    # ------------------------------------------------------------------
    def _handle_segment_ready(self, segment: ScheduledSegment) -> None:
        self._pending_segments[segment.segment_id] = segment
        self._logger.info(
            "Prompt created",
            extra={
                "event": "prompt_created",
                "segment_id": segment.segment_id,
                "start": segment.segment_start.isoformat(),
                "end": segment.segment_end.isoformat(),
                "minutes": segment.minutes,
            },
        )
        self.prompt_ready.emit(segment)

    def _pop_segment(self, segment_id: str) -> ScheduledSegment:
        segment = self._pending_segments.pop(segment_id, None)
        if not segment:
            raise KeyError(f"Unknown segment: {segment_id}")
        return segment
