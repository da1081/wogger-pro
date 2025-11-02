"""Glue code between the prompt manager and dialog UI."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, Sequence

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from ..core.exceptions import PersistenceError
from ..core.models import ScheduledSegment, SplitPart
from ..core.prompt_manager import PromptManager
from ..core.time_segments import TimeRange
from .manual_entry_dialog import ManualEntryDialog
from .multi_remainder_dialog import MultiRemainderDialog, format_range_label
from .prompt_dialog import PromptDialog, TaskSuggestion
from .sound_player import SoundPlayer

LOGGER = logging.getLogger("wogger.ui.prompt_service")


class PromptService(QObject):
    """Manages creation of prompt dialogs for scheduled segments."""

    def __init__(
        self,
        prompt_manager: PromptManager,
        sound_player: SoundPlayer | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = prompt_manager
        self._dialogs: Dict[str, PromptDialog] = {}
        self._cascade_index: int = 0
        self._cascade_step: int = 28
        self._cascade_cycle: int = 8
        self._sound_player = sound_player

        self._manager.prompt_ready.connect(self._on_prompt_ready)
        self._manager.error_occurred.connect(self._on_error)
        self._manager.segment_completed.connect(self._on_segment_completed)
        self._manager.segment_split.connect(self._on_segment_split)
        self._manager.segment_dismissed.connect(self._cleanup_dialog)

    # ------------------------------------------------------------------
    def _on_prompt_ready(self, segment: ScheduledSegment) -> None:
        remainders = self._manager.segment_remainders(segment)
        if not remainders:
            LOGGER.info(
                "Segment already covered",
                extra={
                    "event": "prompt_segment_consumed",
                    "segment_id": segment.segment_id,
                },
            )
            self._manager.dismiss_segment(segment.segment_id, "already_logged")
            QMessageBox.information(
                None,
                "Segment already logged",
                "This scheduled segment is already fully covered by existing entries.",
            )
            return

        if len(remainders) == 1:
            remainder = remainders[0]
            self._manager.restrict_segment_to_range(segment.segment_id, remainder)
            range_hint = self._format_range_hint(remainder, index=1, total=1)
            self._show_prompt_dialog(segment, range_hint=range_hint)
            return

        count = len(remainders)
        intro = (
            "This scheduled segment was fragmented into "
            f"{count} slices by existing entries. Assign a task to each remaining slice to resolve it."
        )
        self._open_multi_remainder_dialog(segment, remainders, intro_text=intro)

    def _show_prompt_dialog(self, segment: ScheduledSegment, range_hint: str | None = None) -> None:
        if segment.segment_id in self._dialogs:
            dialog = self._dialogs[segment.segment_id]
            dialog.raise_()
            dialog.activateWindow()
            return

        suggestions = self._wrap_suggestions(self._manager.task_suggestions())
        dialog = PromptDialog(
            segment=segment,
            task_suggestions=suggestions,
            task_suggestions_loader=lambda: self._wrap_suggestions(self._manager.task_suggestions()),
            default_task=self._manager.last_task(),
            range_hint=range_hint,
            parent=None,
        )
        self._dialogs[segment.segment_id] = dialog

        self._configure_popup(dialog)
        self._play_prompt_sound()

        dialog.submitted.connect(lambda task, seg_id=segment.segment_id: self._handle_submit(seg_id, task))
        dialog.split_saved.connect(lambda parts, seg_id=segment.segment_id: self._handle_split(seg_id, parts))
        dialog.dismissed.connect(lambda reason, seg_id=segment.segment_id: self._handle_dismiss(seg_id, reason))
        dialog.finished.connect(lambda result, seg_id=segment.segment_id: self._on_dialog_finished(seg_id, result))

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_multi_remainder_dialog(
        self,
        segment: ScheduledSegment,
        remainders: Sequence[TimeRange],
        intro_text: str | None = None,
    ) -> None:
        suggestions = self._wrap_suggestions(self._manager.task_suggestions())
        dialog = MultiRemainderDialog(
            remainders=remainders,
            task_suggestions=suggestions,
            task_suggestions_loader=lambda: self._wrap_suggestions(self._manager.task_suggestions()),
            default_task=self._manager.last_task(),
            parent=None,
            intro_text=intro_text,
        )
        self._configure_popup(dialog)
        self._play_prompt_sound()
        dialog.raise_()
        dialog.activateWindow()
        result = dialog.exec()
        if result == QDialog.Accepted:
            try:
                selected_ranges = dialog.selected_remainders
                assignments = dialog.assignments
                self._manager.log_remainder_entries(segment.segment_id, selected_ranges, assignments)
            except (PersistenceError, ValueError) as exc:
                LOGGER.exception("Failed to persist remainder entries")
                QMessageBox.critical(
                    None,
                    "Unable to save entries",
                    str(exc),
                )
        else:
            self._manager.dismiss_segment(segment.segment_id, "multi_remainder_canceled")
        self._cascade_index = 0

    def _on_dialog_finished(self, segment_id: str, _result: int) -> None:
        self._cleanup_dialog(segment_id)

    def _format_range_hint(self, range_: TimeRange, index: int, total: int) -> str:
        label = format_range_label(range_)
        if total <= 1:
            return f"Remaining segment: {label}"
        return f"Remaining segment {index} of {total}: {label}"

    def _handle_submit(self, segment_id: str, task: str) -> None:
        dialog = self._dialogs.get(segment_id)
        try:
            self._manager.complete_segment(segment_id, task)
        except PersistenceError as exc:
            LOGGER.exception("Failed to persist segment submission")
            if dialog:
                dialog.notify_failure()
            QMessageBox.critical(
                dialog,
                "Unable to save entry",
                "The entry could not be saved. Please try again.",
            )
            return
        if dialog:
            dialog.notify_success()

    def _handle_split(self, segment_id: str, parts: Iterable[SplitPart]) -> None:
        dialog = self._dialogs.get(segment_id)
        try:
            self._manager.split_segment(segment_id, parts)
        except PersistenceError as exc:
            LOGGER.exception("Failed to persist split")
            if dialog:
                dialog.notify_failure()
            QMessageBox.critical(
                dialog,
                "Unable to save split",
                "The split entries could not be saved. Please try again.",
            )
            return
        except ValueError as exc:
            LOGGER.warning("Invalid split request: %s", exc)
            if dialog:
                dialog.notify_failure()
            QMessageBox.warning(
                dialog,
                "Invalid split",
                str(exc),
            )
            return
        if dialog:
            dialog.notify_success()

    def _handle_dismiss(self, segment_id: str, reason: str) -> None:
        LOGGER.info(
            "Prompt dismissed",
            extra={"event": "prompt_dismissed", "segment_id": segment_id, "reason": reason},
        )
        self._manager.dismiss_segment(segment_id, reason)

    def show_manual_entry_dialog(self, parent: QWidget | None = None) -> None:
        dialog = ManualEntryDialog(
            manager=self._manager,
            task_suggestions=self._wrap_suggestions(self._manager.task_suggestions()),
            task_suggestions_loader=lambda: self._wrap_suggestions(self._manager.task_suggestions()),
            default_task=self._manager.last_task(),
            parent=parent,
        )
        dialog.exec()

    def _on_segment_completed(self, segment_id: str, entry) -> None:
        LOGGER.info(
            "Prompt segment recorded",
            extra={
                "event": "prompt_entry_saved",
                "segment_id": segment_id,
                "entry_id": entry.entry_id,
                "task": entry.task,
            },
        )

    def _on_segment_split(self, segment_id: str, entries) -> None:
        for entry in entries:
            LOGGER.info(
                "Split segment entry saved",
                extra={
                    "event": "prompt_split_entry_saved",
                    "segment_id": segment_id,
                    "entry_id": entry.entry_id,
                    "task": entry.task,
                },
            )

    def _cleanup_dialog(self, identifier: str) -> None:
        dialog = self._dialogs.pop(identifier, None)
        if dialog:
            dialog.deleteLater()
        if not self._dialogs:
            self._cascade_index = 0

    def _on_error(self, exc: Exception) -> None:
        QMessageBox.critical(None, "Error", str(exc))

    def _wrap_suggestions(self, suggestions: Sequence[tuple[str, int]]) -> list[TaskSuggestion]:
        ordered = sorted(suggestions, key=lambda item: (-item[1], item[0].lower()))
        return [TaskSuggestion(task=name, count=count) for name, count in ordered]

    def _configure_popup(self, dialog: QDialog) -> None:
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.ensurePolished()
        dialog.adjustSize()

        screen = self._resolve_target_screen(dialog)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return

        try:
            geometry = screen.availableGeometry()
        except RuntimeError:
            fallback_screen = QGuiApplication.primaryScreen()
            if fallback_screen is None:
                return
            try:
                geometry = fallback_screen.availableGeometry()
            except RuntimeError:
                return

        size = dialog.sizeHint()
        width = size.width()
        height = size.height()

        base_x = geometry.x() + max(0, (geometry.width() - width) // 2)
        base_y = geometry.y() + max(0, (geometry.height() - height) // 2)

        offset = self._cascade_index * self._cascade_step
        max_offset = min(geometry.width(), geometry.height()) // 3
        if offset > max_offset:
            offset = 0
            self._cascade_index = 0

        dialog.move(base_x + offset, base_y + offset)
        dialog.raise_()
        dialog.activateWindow()
        self._cascade_index = (self._cascade_index + 1) % self._cascade_cycle

    def _resolve_target_screen(self, dialog: QDialog):
        focused = QGuiApplication.focusWindow()
        if focused and focused is not dialog.window():
            screen = focused.screen()
            if screen is not None:
                try:
                    screen.availableGeometry()
                except RuntimeError:
                    screen = None
            if screen is not None:
                return screen

        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is not None:
            try:
                screen.availableGeometry()
            except RuntimeError:
                screen = None
        if screen is not None:
            return screen

        parent = dialog.parentWidget()
        if parent is not None and parent.screen() is not None:
            screen = parent.screen()
            try:
                screen.availableGeometry()
            except RuntimeError:
                screen = None
            if screen is not None:
                return screen

        return QGuiApplication.primaryScreen()

    def _play_prompt_sound(self) -> None:
        if self._sound_player is None:
            return
        try:
            self._sound_player.play_prompt()
        except Exception:  # pragma: no cover - audio backend issues are environment specific
            LOGGER.exception("Prompt sound playback failed")
