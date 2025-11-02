"""Domain models for Wogger Pro."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from .time_segments import TimeRange

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _default_entry_id() -> str:
    return uuid.uuid4().hex
@dataclass(slots=True)
class Entry:
    """Represents a logged work segment."""

    task: str
    segment_start: datetime
    segment_end: datetime
    minutes: int
    entry_id: str = field(default_factory=_default_entry_id)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "task": self.task,
            "segment_start": self.segment_start.isoformat(timespec="seconds"),
            "segment_end": self.segment_end.isoformat(timespec="seconds"),
            "minutes": self.minutes,
        }

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> "Entry":
        return cls(
            task=str(payload["task"]),
            segment_start=_parse_datetime(payload["segment_start"]),
            segment_end=_parse_datetime(payload["segment_end"]),
            minutes=int(payload["minutes"]),
            entry_id=str(payload.get("entry_id") or _default_entry_id()),
        )

    def as_range(self) -> TimeRange:
        return TimeRange(start=self.segment_start, end=self.segment_end)


@dataclass(slots=True)
class TaskSummary:
    task: str
    total_minutes: int

    @property
    def pretty_total(self) -> str:
        hours, minutes = divmod(self.total_minutes, 60)
        chunks = []
        if hours:
            chunks.append(f"{hours}h")
        if minutes or not chunks:
            chunks.append(f"{minutes}m")
        return " ".join(chunks)


def _default_segment_id() -> str:
    return uuid.uuid4().hex


@dataclass(slots=True)
class ScheduledSegment:
    """Represents a scheduled prompt segment that needs user input."""

    segment_start: datetime
    segment_end: datetime
    minutes: int
    segment_id: str = field(default_factory=_default_segment_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "segment_start": self.segment_start.isoformat(timespec="seconds"),
            "segment_end": self.segment_end.isoformat(timespec="seconds"),
            "minutes": self.minutes,
        }

    @property
    def label(self) -> str:
        start = self.segment_start.strftime("%H:%M")
        end = self.segment_end.strftime("%H:%M")
        return f"{start} - {end}"

    def as_range(self) -> TimeRange:
        return TimeRange(start=self.segment_start, end=self.segment_end)


@dataclass(slots=True)
class SplitPart:
    """Represents a portion of a split segment."""

    task: str
    minutes: int


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
