"""Category consistency helpers for task entries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .models import Entry


@dataclass(slots=True)
class CategoryConflictSummary:
    """Represents a task whose stored entries have divergent categories."""

    task: str
    counts: dict[str | None, int]

    def ordered_categories(self) -> list[tuple[str | None, int]]:
        return sorted(
            self.counts.items(),
            key=lambda item: (-item[1], "" if item[0] is None else item[0].lower()),
        )

    def default_category(self) -> str | None:
        for category, _ in self.ordered_categories():
            if category:
                return category
        return None


def analyze_category_consistency(
    entries: Iterable[Entry],
) -> tuple[list[tuple[str, str]], list[CategoryConflictSummary]]:
    """Classify tasks by whether their entries have consistent categories.

    Returns a tuple of:
    * A list of (task, category) pairs that can be auto-assigned because a
      single non-empty category exists alongside missing values.
    * A list of conflicts requiring user input.
    """

    grouped: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        task = entry.task.strip()
        if not task:
            continue
        grouped[task].append(entry)

    auto_assign: list[tuple[str, str]] = []
    conflicts: list[CategoryConflictSummary] = []

    for task, task_entries in grouped.items():
        counts: dict[str | None, int] = {}
        for entry in task_entries:
            category = (entry.category or "").strip() or None
            counts[category] = counts.get(category, 0) + 1

        non_empty = [value for value in counts if value]
        if len(non_empty) == 1:
            category_value = non_empty[0]
            if counts.get(None, 0) > 0:
                auto_assign.append((task, category_value))
            continue

        if len(non_empty) <= 1:
            continue

        conflicts.append(CategoryConflictSummary(task=task, counts=counts))

    auto_assign.sort(key=lambda item: item[0].lower())
    conflicts.sort(key=lambda conflict: conflict.task.lower())
    return auto_assign, conflicts
