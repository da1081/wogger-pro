"""Shared helpers for schedule/manual segment conflict handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from .models import Entry, ScheduledSegment
from .time_segments import TimeRange, coalesce, subtract


@dataclass(frozen=True, slots=True)
class SegmentConflict:
    """Represents an overlap between a proposed time range and an existing entry."""

    requested: TimeRange
    conflicting: TimeRange
    entry: Entry


def entry_ranges(entries: Iterable[Entry]) -> list[TimeRange]:
    return [entry.as_range() for entry in entries]


def compute_conflicts(requested: TimeRange, entries: Sequence[Entry]) -> list[SegmentConflict]:
    conflicts: list[SegmentConflict] = []
    for entry in entries:
        entry_range = entry.as_range()
        if requested.overlaps(entry_range):
            conflicts.append(SegmentConflict(requested=requested, conflicting=entry_range, entry=entry))
    return conflicts


def compute_remainders_for_segment(segment: ScheduledSegment, entries: Sequence[Entry]) -> list[TimeRange]:
    base = segment.as_range()
    subtractors = entry_ranges(entries)
    return coalesce(subtract(base, subtractors)) if subtractors else [base]


def subtract_ranges(base: TimeRange, entries: Sequence[Entry]) -> list[TimeRange]:
    subtractors = entry_ranges(entries)
    return subtract(base, subtractors)


def normalize_ranges(ranges: Iterable[TimeRange]) -> list[TimeRange]:
    return coalesce(ranges)


def remainder_minutes(ranges: Iterable[TimeRange]) -> int:
    return sum(rng.minutes for rng in ranges)


def ensure_half_open(start: datetime, end: datetime) -> TimeRange:
    return TimeRange(start=start, end=end)
