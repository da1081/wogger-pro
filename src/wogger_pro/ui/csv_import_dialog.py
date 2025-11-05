"""Dialog for importing legacy Wogger CSV exports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
    QStyle,
)

from ..core.exceptions import PersistenceError
from ..core.importer import ImportValidationError, MergeResult, merge_entries, parse_wogger_csv
from ..core.models import Entry
from ..core.prompt_manager import PromptManager
from ..core.repository import EntriesRepository


@dataclass(slots=True)
class CsvImportSummary:
    file_path: Path
    source_entry_count: int
    applied_entry_count: int
    applied_minutes: int
    discarded_entry_count: int
    discarded_minutes: int
    overlapped_entry_count: int
    existing_entries_trimmed: int
    existing_minutes_removed: int
    resulting_count: int
    prefer_imported: bool
    applied_start: Optional[datetime]
    applied_end: Optional[datetime]


class CsvImportDialog(QDialog):
    def __init__(
        self,
        repository: EntriesRepository,
        prompt_manager: PromptManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import legacy Wogger CSV Data")
        self.setModal(True)
        self._repository = repository
        self._prompt_manager = prompt_manager
        self._summary: CsvImportSummary | None = None

        self._build_ui()

    @property
    def summary(self) -> CsvImportSummary | None:
        return self._summary

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        intro = QLabel(
            "Select a CSV export from the original Wogger. "
            "Required columns: Date, Day, Start Time, End Time, Duration (min), Task.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        file_row = QWidget(self)
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(8)

        self._path_edit = QLineEdit(self)
        self._path_edit.setPlaceholderText("Select a CSV file…")
        file_layout.addWidget(self._path_edit, 1)

        self._browse_button = QPushButton("Browse…", self)
        self._browse_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self._browse_button.clicked.connect(self._on_browse)
        file_layout.addWidget(self._browse_button)

        layout.addWidget(file_row)

        conflict_box = QWidget(self)
        conflict_layout = QVBoxLayout(conflict_box)
        conflict_layout.setContentsMargins(0, 0, 0, 0)
        conflict_layout.setSpacing(4)

        conflict_layout.addWidget(QLabel("When imported entries overlap existing ones:"))

        self._conflict_group = QButtonGroup(self)
        self._prefer_import = QRadioButton("Let imported data overwrite overlapping time")
        self._prefer_existing = QRadioButton("Keep existing time and fill only uncovered minutes")
        self._conflict_group.addButton(self._prefer_import)
        self._conflict_group.addButton(self._prefer_existing)
        self._prefer_import.setChecked(True)

        conflict_layout.addWidget(self._prefer_import)
        conflict_layout.addWidget(self._prefer_existing)
        layout.addWidget(conflict_box)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #d0342c;")
        layout.addWidget(self._status_label)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        self._buttons.button(QDialogButtonBox.Ok).setText("Import")
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _on_browse(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select CSV export",
            "",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if file_path:
            self._path_edit.setText(file_path)
            self._status_label.clear()

    def _on_accept(self) -> None:
        path_text = self._path_edit.text().strip()
        if not path_text:
            self._status_label.setText("Please select a CSV file to import.")
            return

        csv_path = Path(path_text).expanduser()

        try:
            imported_entries = parse_wogger_csv(csv_path)
        except ImportValidationError as exc:
            self._status_label.setText(str(exc))
            return

        prefer_imported = self._prefer_import.isChecked()
        existing_entries = self._repository.get_all_entries()

        merge_result = merge_entries(existing_entries, imported_entries, prefer_imported)

        summary_text = self._build_summary_text(csv_path, imported_entries, existing_entries, merge_result, prefer_imported)
        confirm = QMessageBox.question(
            self,
            "Confirm import",
            summary_text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            self._repository.replace_all_entries(merge_result.merged_entries)
        except PersistenceError as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        self._prompt_manager.refresh_last_task()
        self._prompt_manager.notify_entries_replaced()

        applied_entries = merge_result.applied_import_entries
        applied_minutes = sum(entry.minutes for entry in applied_entries)
        applied_start = min((entry.segment_start for entry in applied_entries), default=None)
        applied_end = max((entry.segment_end for entry in applied_entries), default=None)

        self._summary = CsvImportSummary(
            file_path=csv_path,
            source_entry_count=len(imported_entries),
            applied_entry_count=len(applied_entries),
            applied_minutes=applied_minutes,
            discarded_entry_count=merge_result.discarded_import_count,
            discarded_minutes=merge_result.discarded_import_minutes,
            overlapped_entry_count=merge_result.overlapped_import_count,
            existing_entries_trimmed=merge_result.existing_entries_trimmed,
            existing_minutes_removed=merge_result.existing_minutes_removed,
            resulting_count=len(merge_result.merged_entries),
            prefer_imported=prefer_imported,
            applied_start=applied_start,
            applied_end=applied_end,
        )

        QMessageBox.information(
            self,
            "Import complete",
            self._format_completion_message(self._summary),
        )
        self.accept()

    def _build_summary_text(
        self,
        csv_path: Path,
        imported_entries: Sequence[Entry],
        existing_entries: Sequence[Entry],
        merge_result: MergeResult,
        prefer_imported: bool,
    ) -> str:
        source_minutes = sum(entry.minutes for entry in imported_entries)
        applied_entries = merge_result.applied_import_entries
        applied_minutes = sum(entry.minutes for entry in applied_entries)
        applied_count = len(applied_entries)
        applied_start = min((entry.segment_start for entry in applied_entries), default=None)
        applied_end = max((entry.segment_end for entry in applied_entries), default=None)

        lines: list[str] = [
            f"File: {csv_path.name}",
            f"Entries in file: {len(imported_entries)} ({source_minutes} minutes)",
            f"Existing entries before import: {len(existing_entries)}",
        ]

        if applied_count:
            span_text = (
                f"Adjusted import span: {applied_start:%Y-%m-%d %H:%M} → {applied_end:%Y-%m-%d %H:%M}"
                if applied_start and applied_end
                else "Adjusted import span: (calculated from applied segments)"
            )
            lines.append(span_text)
            lines.append(
                f"Time that will be added: {applied_minutes} minute{'s' if applied_minutes != 1 else ''} "
                f"across {applied_count} segment{'s' if applied_count != 1 else ''}."
            )
        else:
            lines.append("Imported time is fully covered by existing entries; no new segments will be added.")

        if merge_result.overlapped_import_count:
            lines.append(
                f"Imported segments touching existing data: {merge_result.overlapped_import_count}."
            )

        if prefer_imported:
            if merge_result.existing_entries_trimmed:
                lines.append(
                    f"Existing entries adjusted: {merge_result.existing_entries_trimmed} "
                    f"({merge_result.existing_minutes_removed} minutes replaced)."
                )
            else:
                lines.append("Existing entries do not require trimming.")

            strategy_intro = "Strategy: overwrite conflicts with imported data."
            strategy_detail = (
                "Overlapping portions of existing entries will be trimmed or removed so that the CSV data takes precedence."
            )
        else:
            if merge_result.discarded_import_count:
                lines.append(
                    f"Imported segments fully skipped: {merge_result.discarded_import_count} "
                    f"({merge_result.discarded_import_minutes} minutes)."
                )
            elif merge_result.discarded_import_minutes:
                lines.append(
                    f"Minutes skipped because existing data covers them: {merge_result.discarded_import_minutes}."
                )

            strategy_intro = "Strategy: keep existing entries and fit imports into free time."
            strategy_detail = (
                "Only the portions of the CSV that do not overlap existing entries will be added; overlapping minutes are skipped."
            )

        lines.append(f"Resulting entry count after import: {len(merge_result.merged_entries)}")
        lines.append("")
        lines.append(strategy_intro)
        lines.append(strategy_detail)
        lines.append("")
        lines.append("Proceed with the import?")

        return "\n".join(lines)

    def _format_completion_message(self, summary: CsvImportSummary) -> str:
        lines: list[str] = []

        if summary.applied_entry_count:
            lines.append(
                f"Imported {summary.applied_entry_count} segment{'s' if summary.applied_entry_count != 1 else ''} "
                f"({summary.applied_minutes} minute{'s' if summary.applied_minutes != 1 else ''}) from {summary.file_path.name}."
            )
            if summary.applied_start and summary.applied_end:
                lines.append(
                    f"Applied span: {summary.applied_start:%Y-%m-%d %H:%M} → {summary.applied_end:%Y-%m-%d %H:%M}"
                )
        else:
            lines.append(
                f"No new time was imported from {summary.file_path.name} because existing entries already cover the CSV data."
            )

        if summary.discarded_minutes:
            lines.append(
                f"Minutes skipped to preserve existing entries: {summary.discarded_minutes} "
                f"across {summary.discarded_entry_count} segment{'s' if summary.discarded_entry_count != 1 else ''}."
            )

        if summary.prefer_imported:
            lines.append("Strategy: imported entries overwrite overlaps.")
            if summary.existing_entries_trimmed:
                lines.append(
                    f"Existing entries adjusted: {summary.existing_entries_trimmed} "
                    f"({summary.existing_minutes_removed} minutes replaced)."
                )
            else:
                lines.append("Existing entries did not need trimming.")
        else:
            lines.append("Strategy: existing entries kept their time; imported data filled gaps only.")

        lines.append(f"Resulting total entries: {summary.resulting_count}")
        return "\n".join(lines)
