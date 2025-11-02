"""Cron-based prompt scheduler for Wogger Pro."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from croniter import croniter
from PySide6.QtCore import QObject, QTimer, Signal

from .models import ScheduledSegment


class PromptScheduler(QObject):
    """Schedules prompt segments according to a cron expression."""

    segment_ready: Signal = Signal(ScheduledSegment)
    schedule_changed: Signal = Signal(str)

    def __init__(self, cron_expression: str, parent: Optional[QObject] = None, logger: Optional[logging.Logger] = None) -> None:
        super().__init__(parent)
        self._logger = logger or logging.getLogger("wogger.scheduler")
        self._cron_expression = cron_expression
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._cron_iter = self._build_croniter(cron_expression, datetime.now())
        self._next_fire: Optional[datetime] = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._timer.isActive():
            return
        self._logger.info(
            "Prompt scheduler starting",
            extra={"event": "scheduler_start", "cron": self._cron_expression},
        )
        self._schedule_next(initial=True)

    def stop(self) -> None:
        if self._timer.isActive():
            self._logger.info("Prompt scheduler stopping", extra={"event": "scheduler_stop"})
            self._timer.stop()
        self._next_fire = None

    def update_cron(self, cron_expression: str) -> None:
        if cron_expression == self._cron_expression:
            return
        self._logger.info(
            "Updating scheduler cron",
            extra={
                "event": "scheduler_cron_update",
                "old_cron": self._cron_expression,
                "new_cron": cron_expression,
            },
        )
        self._cron_expression = cron_expression
        self._cron_iter = self._build_croniter(cron_expression, datetime.now())
        self._next_fire = None
        self.schedule_changed.emit(cron_expression)
        if self._timer.isActive():
            self._timer.stop()
        self._schedule_next(initial=True)

    @property
    def next_fire_time(self) -> Optional[datetime]:
        return self._next_fire

    @property
    def cron_expression(self) -> str:
        return self._cron_expression

    # ------------------------------------------------------------------
    def _on_timeout(self) -> None:
        now = datetime.now()
        due_segments: list[ScheduledSegment] = []

        while self._next_fire and self._next_fire <= now:
            segment = self._build_segment_for_fire(self._next_fire)
            due_segments.append(segment)
            self._logger.info(
                "Scheduled segment ready",
                extra={
                    "event": "segment_ready",
                    "segment_id": segment.segment_id,
                    "start": segment.segment_start.isoformat(),
                    "end": segment.segment_end.isoformat(),
                    "minutes": segment.minutes,
                },
            )
            self.segment_ready.emit(segment)
            self._next_fire = self._cron_iter.get_next(datetime)

        if not due_segments:
            # In rare cases of timer firing early, reschedule quickly.
            self._logger.debug(
                "Timer fired but no due segments; rescheduling",
                extra={"event": "scheduler_reschedule_no_due"},
            )

        self._schedule_next(initial=False)

    def _schedule_next(self, initial: bool) -> None:
        if not self._cron_iter:
            self._cron_iter = self._build_croniter(self._cron_expression, datetime.now())
        if self._next_fire is None:
            self._next_fire = self._cron_iter.get_next(datetime)

        now = datetime.now()
        delay = self._next_fire - now
        if delay <= timedelta(milliseconds=0):
            delay_ms = 1000  # minimal delay to prevent busy loop
        else:
            delay_ms = int(delay.total_seconds() * 1000)
        self._timer.start(delay_ms)
        self._logger.debug(
            "Scheduler %s next fire",
            "initialized" if initial else "updated",
            extra={
                "event": "scheduler_next_fire",
                "fire_at": self._next_fire.isoformat(),
                "delay_ms": delay_ms,
            },
        )

    def _build_segment_for_fire(self, fire_time: datetime) -> ScheduledSegment:
        previous_fire = croniter(self._cron_expression, fire_time).get_prev(datetime)
        minutes = max(1, int((fire_time - previous_fire).total_seconds() // 60))
        return ScheduledSegment(segment_start=previous_fire, segment_end=fire_time, minutes=minutes)

    def _build_croniter(self, cron_expression: str, base: datetime) -> croniter:
        try:
            return croniter(cron_expression, base)
        except Exception as exc:  # pragma: no cover - croniter validation
            self._logger.exception(
                "Failed to build croniter",
                extra={"event": "scheduler_invalid_cron", "cron": cron_expression},
            )
            raise
