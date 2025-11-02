from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("TimeRange end must be after start")

    @property
    def minutes(self) -> int:
        return max(1, int((self.end - self.start).total_seconds() // 60))

    def overlaps(self, other: "TimeRange") -> bool:
        return self.start < other.end and self.end > other.start

    def touches(self, other: "TimeRange") -> bool:
        return self.end == other.start or self.start == other.end

    def merge(self, other: "TimeRange") -> "TimeRange":
        if not (self.overlaps(other) or self.touches(other)):
            raise ValueError("Ranges must overlap or touch to merge")
        start = min(self.start, other.start)
        end = max(self.end, other.end)
        return TimeRange(start=start, end=end)

    def intersect(self, other: "TimeRange") -> "TimeRange | None":
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        if end <= start:
            return None
        return TimeRange(start=start, end=end)


def sort_ranges(ranges: Iterable[TimeRange]) -> list[TimeRange]:
    return sorted(ranges, key=lambda rng: (rng.start, rng.end))


def coalesce(ranges: Iterable[TimeRange]) -> list[TimeRange]:
    ordered = sort_ranges(ranges)
    if not ordered:
        return []

    merged: list[TimeRange] = [ordered[0]]
    for current in ordered[1:]:
        last = merged[-1]
        if last.overlaps(current) or last.touches(current):
            merged[-1] = last.merge(current)
        else:
            merged.append(current)
    return merged


def subtract(base: TimeRange, subtractors: Sequence[TimeRange]) -> list[TimeRange]:
    if not subtractors:
        return [base]

    relevant: list[TimeRange] = []
    for rng in subtractors:
        intersection = base.intersect(rng)
        if intersection is not None:
            relevant.append(intersection)

    if not relevant:
        return [base]

    merged = coalesce(relevant)
    remainders: list[TimeRange] = []
    cursor = base.start

    for rng in merged:
        if rng.start > cursor:
            remainders.append(TimeRange(start=cursor, end=rng.start))
        cursor = max(cursor, rng.end)

    if cursor < base.end:
        remainders.append(TimeRange(start=cursor, end=base.end))

    return remainders


def subtract_many(base_ranges: Sequence[TimeRange], subtractors: Sequence[TimeRange]) -> list[TimeRange]:
    remainders: list[TimeRange] = []
    for base in base_ranges:
        remainders.extend(subtract(base, subtractors))
    return sort_ranges(remainders)


def minutes_between(start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    return int((end - start).total_seconds() // 60)


def expand_minutes(start: datetime, minutes: int) -> datetime:
    if minutes < 0:
        raise ValueError("minutes must be non-negative")
    return start + timedelta(minutes=minutes)
