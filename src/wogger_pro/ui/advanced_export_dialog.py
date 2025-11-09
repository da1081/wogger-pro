"""Dialog for configuring and exporting advanced reports."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.exporter import (
    ExportFormat,
    ExportOptions,
    ExportType,
    TimeGrouping,
    generate_export_table,
    write_export,
)
from ..core.models import Entry
from ..core.paths import default_downloads_dir

LOGGER = logging.getLogger("wogger.ui.export.advanced")


@dataclass
class _ExportRange:
    start: datetime
    end: datetime


class AdvancedExportDialog(QDialog):
    def __init__(
        self,
        entries: Iterable[Entry],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Advanced Export")
        self.setModal(True)
        self.resize(420, 320)

        self._entries = sorted(entries, key=lambda entry: entry.segment_start)
        self._range = self._compute_range(self._entries)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(self._build_intro())
        layout.addWidget(self._build_form())
        layout.addWidget(self._build_status_label())
        layout.addStretch(1)
        layout.addWidget(self._build_buttons())

        self._apply_initial_values()
        self._update_controls_enabled()

    def _build_intro(self) -> QWidget:
        label = QLabel(
            "Configure the export time frame, grouping, and format."
            " The generated file will include the selected data only.",
            self,
        )
        label.setWordWrap(True)
        return label

    def _build_form(self) -> QWidget:
        container = QWidget(self)
        form = QFormLayout(container)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._start_edit = QDateTimeEdit(self)
        self._start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._start_edit.setCalendarPopup(True)
        self._start_edit.setMinimumWidth(180)
        self._start_edit.dateTimeChanged.connect(self._on_start_changed)
        form.addRow("Start", self._start_edit)

        end_row = QWidget(self)
        end_layout = QHBoxLayout(end_row)
        end_layout.setContentsMargins(0, 0, 0, 0)
        end_layout.setSpacing(8)

        self._end_edit = QDateTimeEdit(self)
        self._end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setMinimumWidth(180)
        end_layout.addWidget(self._end_edit, 1)

        self._all_button = QPushButton("All", self)
        self._all_button.setToolTip("Use the full span of available entries")
        self._all_button.clicked.connect(self._on_all_clicked)
        end_layout.addWidget(self._all_button)
        form.addRow("End", end_row)

        self._type_combo = QComboBox(self)
        self._type_combo.addItem("Categorized", ExportType.CATEGORIES)
        self._type_combo.addItem("Tasks", ExportType.TASKS)
        self._type_combo.addItem("Entries", ExportType.ENTRIES)
        form.addRow("Export type", self._type_combo)

        self._group_combo = QComboBox(self)
        self._group_combo.addItem("Hours", TimeGrouping.HOURS)
        self._group_combo.addItem("Days", TimeGrouping.DAYS)
        self._group_combo.addItem("Weeks", TimeGrouping.WEEKS)
        self._group_combo.addItem("Months", TimeGrouping.MONTHS)
        self._group_combo.addItem("Years", TimeGrouping.YEARS)
        form.addRow("Time grouping", self._group_combo)

        self._format_combo = QComboBox(self)
        self._format_combo.addItem("CSV", ExportFormat.CSV)
        self._format_combo.addItem("JSONL", ExportFormat.JSONL)
        self._format_combo.addItem("JSON", ExportFormat.JSON)
        self._format_combo.addItem("Excel Workbook", ExportFormat.EXCEL)
        form.addRow("Format", self._format_combo)

        return container

    def _build_status_label(self) -> QLabel:
        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #d0342c;")
        return self._status_label

    def _build_buttons(self) -> QWidget:
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        ok_button = box.button(QDialogButtonBox.Ok)
        ok_button.setText("Export")
        box.accepted.connect(self._on_export)
        box.rejected.connect(self.reject)
        self._button_box = box
        return box

    def _update_controls_enabled(self) -> None:
        has_entries = bool(self._entries)
        for widget in (self._start_edit, self._end_edit, self._type_combo, self._group_combo, self._format_combo, self._all_button):
            widget.setEnabled(has_entries)
        self._button_box.button(QDialogButtonBox.Ok).setEnabled(has_entries)
        if not has_entries:
            self._status_label.setText("No entries available to export yet.")

    def _apply_initial_values(self) -> None:
        now = QDateTime.currentDateTime()
        default_start = now.addDays(-7)
        default_end = now
        if self._range:
            default_start = QDateTime(self._range.start)
            default_end = QDateTime(self._range.end)
        self._start_edit.setDateTime(default_start)
        self._end_edit.setDateTime(default_end)
        self._start_edit.setMinimumDateTime(default_start.addYears(-25))
        self._end_edit.setMinimumDateTime(default_start)
        self._end_edit.setMaximumDateTime(QDateTime.currentDateTime().addYears(25))

    def _on_all_clicked(self) -> None:
        if not self._range:
            return
        self._start_edit.setDateTime(QDateTime(self._range.start))
        self._end_edit.setDateTime(QDateTime(self._range.end))
        self._status_label.clear()

    def _on_start_changed(self, value: QDateTime) -> None:
        self._end_edit.setMinimumDateTime(value)
        if self._end_edit.dateTime() < value:
            self._end_edit.setDateTime(value)
        self._status_label.clear()

    def _on_export(self) -> None:
        self._status_label.clear()
        if not self._entries:
            self.reject()
            return

        start = _to_datetime(self._start_edit.dateTime())
        end = _to_datetime(self._end_edit.dateTime())

        if start >= end:
            self._status_label.setText("Start must be earlier than end.")
            return

        options = ExportOptions(
            start=start,
            end=end,
            export_type=self._type_combo.currentData() or ExportType.CATEGORIES,
            grouping=self._group_combo.currentData() or TimeGrouping.DAYS,
            format=self._format_combo.currentData() or ExportFormat.CSV,
        )

        try:
            table = generate_export_table(self._entries, options)
        except Exception as exc:
            LOGGER.exception("Failed to generate export table")
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        if not table.rows:
            QMessageBox.information(self, "No data", "No entries match the selected filters.")
            return

        target_path = self._prompt_for_path(options.format)
        if not target_path:
            return

        try:
            write_export(table, options, target_path)
        except Exception as exc:
            LOGGER.exception("Failed to write export")
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        LOGGER.info(
            "Advanced export created",
            extra={
                "event": "ui_export_advanced",
                "type": options.export_type.value,
                "format": options.format.value,
                "grouping": options.grouping.value,
                "path": str(target_path),
                "start": options.start.isoformat(timespec="seconds"),
                "end": options.end.isoformat(timespec="seconds"),
                "row_count": len(table.rows),
            },
        )

        QMessageBox.information(
            self,
            "Export complete",
            (
                f"Exported {len(table.rows)} row{'s' if len(table.rows) != 1 else ''} "
                f"to {target_path.name}."
            ),
        )
        self.accept()

    def _prompt_for_path(self, fmt: ExportFormat) -> Path | None:
        default_name = {
            ExportFormat.CSV: "advanced-export.csv",
            ExportFormat.JSONL: "advanced-export.jsonl",
            ExportFormat.JSON: "advanced-export.json",
            ExportFormat.EXCEL: "advanced-export.xlsx",
        }[fmt]
        filters = {
            ExportFormat.CSV: "CSV Files (*.csv)",
            ExportFormat.JSONL: "JSON Lines (*.jsonl)",
            ExportFormat.JSON: "JSON Files (*.json)",
            ExportFormat.EXCEL: "Excel Workbook (*.xlsx)",
        }
        filter_string = ";;".join(filters.values())
        suggestion = Path(default_downloads_dir()) / default_name
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save export",
            str(suggestion),
            filter_string,
            filters[fmt],
        )
        if not file_path:
            return None
        target = Path(file_path).expanduser()
        if target.suffix == "":
            extension = {
                ExportFormat.CSV: ".csv",
                ExportFormat.JSONL: ".jsonl",
                ExportFormat.JSON: ".json",
                ExportFormat.EXCEL: ".xlsx",
            }[fmt]
            target = target.with_suffix(extension)
        return target

    @staticmethod
    def _compute_range(entries: Iterable[Entry]) -> _ExportRange | None:
        entries_list = list(entries)
        if not entries_list:
            return None
        return _ExportRange(start=entries_list[0].segment_start, end=max(entry.segment_end for entry in entries_list))


def _to_datetime(value: QDateTime) -> datetime:
    if hasattr(value, "toPython"):
        return value.toPython()
    return value.toPyDateTime()
