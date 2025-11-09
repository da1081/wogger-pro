"""Settings dialog for Wogger Pro."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QSize, QUrl, Qt  # type: ignore[import]
from PySide6.QtGui import QDesktopServices  # type: ignore[import]
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QAbstractSpinBox,
)

from ..core.backup import create_appdata_backup
from ..core.categories import CategoryManager
from ..core.exceptions import BackupError, SettingsError
from ..core.paths import default_app_data_dir, default_downloads_dir, recurring_backups_dir
from ..core.models import Entry
from ..core.exporter import create_jf_excel_export
from ..core.prompt_manager import PromptManager
from ..core.repository import EntriesRepository
from ..core.settings import DEFAULT_PROMPT_CRON, Settings, Theme
from .advanced_export_dialog import AdvancedExportDialog
from .categories_dialog import CategoriesDialog
from .csv_import_dialog import CsvImportDialog
from .jf_loggr_import_dialog import JfLoggrImportDialog
from .icons import (
    add_palette_listener,
    app_icon,
    backup_off_icon,
    backup_on_icon,
    import_icon,
    minus_icon,
    plus_icon,
    remove_palette_listener,
    sound_off_icon,
    sound_on_icon,
)
from .sound_player import SoundPlayer

LOGGER = logging.getLogger("wogger.ui.settings")


class SettingsDialog(QDialog):
    def __init__(
        self,
        current_settings: Settings,
        repository: EntriesRepository,
        prompt_manager: PromptManager,
        sound_player: SoundPlayer | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self._initial_settings = current_settings
        self._result_settings: Settings | None = None
        self._repository = repository
        self._prompt_manager = prompt_manager
        self._category_manager = CategoryManager()
        self._spin_buttons: dict[QSpinBox, tuple[QToolButton, QToolButton]] = {}
        add_palette_listener(self._refresh_spin_icons)
        if sound_player is not None:
            self._sound_player = sound_player
        else:
            self._sound_player = SoundPlayer(self)
        self.resize(500, 260)
        layout = QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._theme_combo = QComboBox(self)
        for theme in Theme:
            self._theme_combo.addItem(theme.value.title(), theme.value)
        self._cron_edit = QLineEdit(self)
        self._cron_edit.setPlaceholderText("*/15 * * * *")

        layout.addRow("Theme", self._theme_combo)
        layout.addRow("Prompt schedule (cron)", self._cron_edit)

        self._missing_slot_spin = QSpinBox()
        self._missing_slot_spin.setRange(0, 2_147_483_647)
        self._missing_slot_spin.setSuffix(" min")
        self._missing_slot_spin.setToolTip(
            "Treat gaps shorter than this duration as missed timeslots (0 disables detection)"
        )
        layout.addRow("Missing timeslot window", self._create_spin_control(self._missing_slot_spin, step=10))

        self._sound_toggle_button = QPushButton(self)
        self._sound_toggle_button.setCheckable(True)
        self._sound_toggle_button.setMinimumWidth(160)
        self._sound_toggle_button.toggled.connect(self._on_sound_button_toggled)
        self._sound_toggle_button.setIconSize(QSize(20, 20))

        sound_row = QWidget(self)
        sound_layout = QHBoxLayout(sound_row)
        sound_layout.setContentsMargins(0, 0, 0, 0)
        sound_layout.setSpacing(8)
        sound_layout.addWidget(self._sound_toggle_button)

        self._sound_preview_button = QPushButton("Play Sound", self)
        self._sound_preview_button.setIcon(sound_on_icon())
        self._sound_preview_button.setIconSize(QSize(18, 18))
        self._sound_preview_button.setToolTip("Preview the prompt notification sound")
        self._sound_preview_button.clicked.connect(self._on_sound_preview_clicked)
        sound_layout.addWidget(self._sound_preview_button)
        sound_layout.addStretch(1)

        self._sound_toggle_button.setChecked(current_settings.prompt_sounds_enabled)
        self._sound_player.set_enabled(current_settings.prompt_sounds_enabled)
        self._update_sound_button(current_settings.prompt_sounds_enabled)
        layout.addRow("Prompt sounds", sound_row)

        app_data_widget = QWidget(self)
        app_data_layout = QHBoxLayout(app_data_widget)
        app_data_layout.setContentsMargins(0, 0, 0, 0)
        app_data_layout.setSpacing(8)

        self._app_data_edit = QLineEdit(self)
        app_data_layout.addWidget(self._app_data_edit, 1)

        self._open_appdata_button = QPushButton("Open Appdata Folder", self)
        self._open_appdata_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self._open_appdata_button.setToolTip("Open the current app data folder")
        self._open_appdata_button.clicked.connect(self._on_open_appdata_clicked)
        app_data_layout.addWidget(self._open_appdata_button)

        layout.addRow("App data path", app_data_widget)

        backup_widget = QWidget(self)
        backup_layout = QHBoxLayout(backup_widget)
        backup_layout.setContentsMargins(0, 0, 0, 0)
        backup_layout.setSpacing(8)

        self._backup_path_edit = QLineEdit(self)
        backup_layout.addWidget(self._backup_path_edit, 1)

        self._backup_button = QPushButton("Create Backup", self)
        self._backup_button.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self._backup_button.setToolTip("Create a ZIP backup in the selected folder")
        self._backup_button.clicked.connect(self._on_backup_clicked)
        backup_layout.addWidget(self._backup_button)

        layout.addRow("Backup folder", backup_widget)

        recurring_widget = QWidget(self)
        recurring_layout = QVBoxLayout(recurring_widget)
        recurring_layout.setContentsMargins(0, 0, 0, 0)
        recurring_layout.setSpacing(6)

        self._recurring_toggle_button = QPushButton(self)
        self._recurring_toggle_button.setCheckable(True)
        self._recurring_toggle_button.setMinimumWidth(220)
        self._recurring_toggle_button.setIconSize(QSize(20, 20))
        self._recurring_toggle_button.toggled.connect(self._on_recurring_enabled_toggled)
        recurring_layout.addWidget(self._recurring_toggle_button)

        interval_row = QWidget(recurring_widget)
        interval_layout = QHBoxLayout(interval_row)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        interval_layout.setSpacing(8)

        interval_layout.addWidget(QLabel("Create backup every", interval_row))
        self._recurring_interval_spin = QSpinBox()
        self._recurring_interval_spin.setRange(1, 365)
        self._recurring_interval_spin.setValue(1)
        interval_layout.addWidget(self._create_spin_control(self._recurring_interval_spin, step=1))
        interval_layout.addWidget(QLabel("day(s)", interval_row))
        interval_layout.addStretch(1)
        recurring_layout.addWidget(interval_row)

        retention_row = QWidget(recurring_widget)
        retention_layout = QHBoxLayout(retention_row)
        retention_layout.setContentsMargins(0, 0, 0, 0)
        retention_layout.setSpacing(8)

        retention_layout.addWidget(QLabel("Keep backups for", retention_row))
        self._recurring_retention_spin = QSpinBox()
        self._recurring_retention_spin.setRange(1, 100)
        self._recurring_retention_spin.setValue(7)
        retention_layout.addWidget(self._create_spin_control(self._recurring_retention_spin, step=1))
        retention_layout.addWidget(QLabel("day(s)", retention_row))
        retention_layout.addStretch(1)
        recurring_layout.addWidget(retention_row)

        path_row = QWidget(recurring_widget)
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(8)

        self._recurring_path_edit = QLineEdit(recurring_widget)
        path_layout.addWidget(self._recurring_path_edit, 1)

        self._recurring_browse_button = QPushButton("Browse…", recurring_widget)
        self._recurring_browse_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self._recurring_browse_button.clicked.connect(self._on_recurring_browse_clicked)
        path_layout.addWidget(self._recurring_browse_button)
        recurring_layout.addWidget(path_row)

        layout.addRow("Recurring backups", recurring_widget)

        import_widget = QWidget(self)
        import_layout = QVBoxLayout(import_widget)
        import_layout.setContentsMargins(0, 0, 0, 0)
        import_layout.setSpacing(6)

        legacy_row = QWidget(import_widget)
        legacy_layout = QHBoxLayout(legacy_row)
        legacy_layout.setContentsMargins(0, 0, 0, 0)
        legacy_layout.setSpacing(8)

        self._import_button = QPushButton("Import legacy Wogger CSV Data…", self)
        try:
            self._import_button.setIcon(import_icon())
        except Exception:  # pragma: no cover - icon fallback
            LOGGER.debug("Import icon unavailable", exc_info=True)
        self._import_button.setToolTip("Import legacy Wogger CSV exports")
        self._import_button.clicked.connect(self._on_import_clicked)
        legacy_layout.addWidget(self._import_button)
        legacy_layout.addStretch(1)
        import_layout.addWidget(legacy_row)

        jf_row = QWidget(import_widget)
        jf_layout = QHBoxLayout(jf_row)
        jf_layout.setContentsMargins(0, 0, 0, 0)
        jf_layout.setSpacing(8)

        self._jf_loggr_button = QPushButton("Import from JF LoggR…", self)
        try:
            self._jf_loggr_button.setIcon(import_icon())
        except Exception:  # pragma: no cover - icon fallback
            LOGGER.debug("Import icon unavailable", exc_info=True)
        self._jf_loggr_button.setToolTip("Import entries from a JF LoggR worklogger.json export")
        self._jf_loggr_button.clicked.connect(self._on_import_jf_loggr_clicked)
        jf_layout.addWidget(self._jf_loggr_button)
        jf_layout.addStretch(1)
        import_layout.addWidget(jf_row)

        layout.addRow("Data import", import_widget)

        export_widget = QWidget(self)
        export_layout = QVBoxLayout(export_widget)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setSpacing(6)

        advanced_row = QWidget(export_widget)
        advanced_layout = QHBoxLayout(advanced_row)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)

        self._advanced_export_button = QPushButton("Open advanced export…", self)
        self._advanced_export_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self._advanced_export_button.setToolTip("Configure and export grouped summaries")
        self._advanced_export_button.clicked.connect(self._on_advanced_export_clicked)
        advanced_layout.addWidget(self._advanced_export_button)
        advanced_layout.addStretch(1)
        export_layout.addWidget(advanced_row)

        jf_excel_row = QWidget(export_widget)
        jf_excel_layout = QHBoxLayout(jf_excel_row)
        jf_excel_layout.setContentsMargins(0, 0, 0, 0)
        jf_excel_layout.setSpacing(8)

        self._jf_excel_export_button = QPushButton("Export to JF Excel…", self)
        self._jf_excel_export_button.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
        self._jf_excel_export_button.setToolTip("Export categories to an Excel tree grouped by date")
        self._jf_excel_export_button.clicked.connect(self._on_export_jf_excel_clicked)
        jf_excel_layout.addWidget(self._jf_excel_export_button)
        jf_excel_layout.addStretch(1)
        export_layout.addWidget(jf_excel_row)

        jf_export_row = QWidget(export_widget)
        jf_export_layout = QHBoxLayout(jf_export_row)
        jf_export_layout.setContentsMargins(0, 0, 0, 0)
        jf_export_layout.setSpacing(8)

        self._jf_loggr_export_button = QPushButton("Export to JF LoggR…", self)
        self._jf_loggr_export_button.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self._jf_loggr_export_button.setToolTip("Create a JF LoggR-compatible work-logger.json export")
        self._jf_loggr_export_button.clicked.connect(self._on_export_jf_loggr_clicked)
        jf_export_layout.addWidget(self._jf_loggr_export_button)
        jf_export_layout.addStretch(1)
        export_layout.addWidget(jf_export_row)

        layout.addRow("Data export", export_widget)

        categories_widget = QWidget(self)
        categories_layout = QHBoxLayout(categories_widget)
        categories_layout.setContentsMargins(0, 0, 0, 0)
        categories_layout.setSpacing(8)

        self._categories_button = QPushButton("Manage Categories…", self)
        self._categories_button.clicked.connect(self._on_categories_clicked)
        categories_layout.addWidget(self._categories_button)
        categories_layout.addStretch(1)

        layout.addRow("Categories", categories_widget)

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #d0342c;")
        layout.addRow(self._error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        self._reset_button = QPushButton("Reset Settings", self)
        self._reset_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self._reset_button.setToolTip("Restore all preferences to their defaults")
        self._reset_button.clicked.connect(self._on_reset_clicked)

        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(12)
        footer_layout.addWidget(self._reset_button)
        footer_layout.addStretch(1)
        footer_layout.addWidget(buttons)
        layout.addRow(footer)

        self._theme_combo.currentIndexChanged.connect(self._clear_error)
        self._cron_edit.textChanged.connect(self._clear_error)
        self._sound_toggle_button.toggled.connect(lambda _checked: self._clear_error())
        self._app_data_edit.textChanged.connect(self._clear_error)
        self._backup_path_edit.textChanged.connect(self._clear_error)
        self._recurring_interval_spin.valueChanged.connect(lambda _value: self._clear_error())
        self._recurring_retention_spin.valueChanged.connect(lambda _value: self._clear_error())
        self._recurring_path_edit.textChanged.connect(self._clear_error)
        self._missing_slot_spin.valueChanged.connect(lambda _value: self._clear_error())

        self._update_recurring_controls_enabled()

        self._apply_settings_to_fields(current_settings)

    def _on_accept(self) -> None:
        theme_value = self._theme_combo.currentData()
        cron_value = self._cron_edit.text().strip()
        try:
            recurring_enabled = self._recurring_toggle_button.isChecked()
            recurring_interval = self._recurring_interval_spin.value()
            recurring_retention = self._recurring_retention_spin.value()
            recurring_path = self._recurring_path_edit.text().strip() or str(recurring_backups_dir())
            new_settings = Settings(
                theme=Theme(theme_value),
                prompt_cron=cron_value,
                prompt_sounds_enabled=self._sound_toggle_button.isChecked(),
                auto_launch_on_startup=self._initial_settings.auto_launch_on_startup,
                app_data_path=self._app_data_edit.text().strip() or str(default_app_data_dir()),
                backup_path=self._backup_path_edit.text().strip() or str(default_downloads_dir()),
                recurring_backup_enabled=recurring_enabled,
                recurring_backup_interval_days=recurring_interval,
                recurring_backup_retention_days=recurring_retention,
                recurring_backup_path=recurring_path,
                missing_timeslot_threshold_minutes=self._missing_slot_spin.value(),
            )
        except SettingsError as exc:
            LOGGER.warning("Settings validation failed: %s", exc)
            self._error_label.setText(str(exc))
            return
        except Exception as exc:  # pragma: no cover - unexpected errors
            LOGGER.exception("Unable to save settings")
            QMessageBox.critical(self, "Error", str(exc))
            return

        self._result_settings = new_settings
        self.accept()

    def _on_backup_clicked(self) -> None:
        original_text = self._backup_button.text()
        self._backup_button.setEnabled(False)
        self._backup_button.setText("Processing...")
        QApplication.processEvents()

        try:
            target_dir = self._backup_path_edit.text().strip() or None
            backup_path = create_appdata_backup(target_dir)
        except BackupError as exc:
            LOGGER.warning("Backup failed: %s", exc)
            QMessageBox.critical(self, "Backup failed", str(exc))
        except Exception as exc:  # pragma: no cover - unexpected errors
            LOGGER.exception("Unexpected error during backup")
            QMessageBox.critical(self, "Backup failed", str(exc))
        else:
            LOGGER.info(
                "Backup created",
                extra={"event": "ui_backup_success", "path": str(backup_path)},
            )
            QMessageBox.information(
                self,
                "Backup complete",
                f"Backup created at:\n{backup_path}",
            )
        finally:
            self._backup_button.setText(original_text)
            self._backup_button.setEnabled(True)

    def _on_recurring_enabled_toggled(self, checked: bool) -> None:
        self._update_recurring_controls_enabled(checked)
        self._clear_error()

    def _update_recurring_controls_enabled(self, checked: bool | None = None) -> None:
        if checked is None:
            checked = self._recurring_toggle_button.isChecked()
        enabled = bool(checked)
        targets = (
            self._recurring_interval_spin,
            self._recurring_retention_spin,
            self._recurring_path_edit,
            self._recurring_browse_button,
        )
        for widget in targets:
            widget.setEnabled(enabled)
        for spin in (self._recurring_interval_spin, self._recurring_retention_spin):
            buttons = self._spin_buttons.get(spin)
            if buttons:
                for button in buttons:
                    button.setEnabled(enabled)
        self._update_recurring_button(checked)

    def _update_recurring_button(self, checked: bool | None = None) -> None:
        if checked is None:
            checked = self._recurring_toggle_button.isChecked()
        if checked:
            self._recurring_toggle_button.setText("Recurring backups enabled")
            self._recurring_toggle_button.setIcon(backup_on_icon())
            self._recurring_toggle_button.setToolTip("Click to disable recurring backups")
            self._recurring_toggle_button.setStyleSheet("background-color: #2563eb; color: #ffffff;")
        else:
            self._recurring_toggle_button.setText("Recurring backups disabled")
            self._recurring_toggle_button.setIcon(backup_off_icon())
            self._recurring_toggle_button.setToolTip("Click to enable recurring backups")
            self._recurring_toggle_button.setStyleSheet("background-color: #4b5563; color: #f9fafb;")

    def _on_sound_button_toggled(self, checked: bool) -> None:
        self._sound_player.set_enabled(checked)
        self._update_sound_button(checked)

    def _update_sound_button(self, checked: bool | None = None) -> None:
        if checked is None:
            checked = self._sound_toggle_button.isChecked()

        if checked:
            self._sound_toggle_button.setText("Sound On")
            self._sound_toggle_button.setIcon(sound_on_icon())
            self._sound_toggle_button.setToolTip("Click to mute prompt sounds")
            self._sound_toggle_button.setStyleSheet("background-color: #2563eb; color: #ffffff;")
            self._sound_preview_button.setToolTip("Preview the prompt notification sound")
            self._sound_preview_button.setEnabled(True)
        else:
            self._sound_toggle_button.setText("Sound Off")
            self._sound_toggle_button.setIcon(sound_off_icon())
            self._sound_toggle_button.setToolTip("Click to enable prompt sounds")
            self._sound_toggle_button.setStyleSheet("background-color: #4b5563; color: #f9fafb;")
            self._sound_preview_button.setToolTip("Enable prompt sounds to hear the preview")
            self._sound_preview_button.setEnabled(False)

    def _on_sound_preview_clicked(self) -> None:
        try:
            self._sound_player.play_prompt()
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Sound preview failed")

    def _on_recurring_browse_clicked(self) -> None:
        start_dir = self._recurring_path_edit.text().strip() or str(recurring_backups_dir())
        selected = QFileDialog.getExistingDirectory(self, "Select recurring backup folder", start_dir)
        if selected:
            self._recurring_path_edit.setText(selected)
            self._clear_error()

    def _on_import_clicked(self) -> None:
        dialog = CsvImportDialog(self._repository, self._prompt_manager, parent=self)
        try:
            result = dialog.exec()
        except Exception as exc:  # pragma: no cover - Qt dialog errors are user specific
            LOGGER.exception("Import dialog failed")
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        if result == QDialog.DialogCode.Accepted:
            self._clear_error()

    def _on_import_jf_loggr_clicked(self) -> None:
        dialog = JfLoggrImportDialog(self._repository, self._prompt_manager, parent=self)
        try:
            result = dialog.exec()
        except Exception as exc:  # pragma: no cover - Qt dialog errors are user specific
            LOGGER.exception("JF LoggR import dialog failed")
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        if result == QDialog.DialogCode.Accepted:
            self._clear_error()

    def _on_advanced_export_clicked(self) -> None:
        try:
            entries = self._repository.get_all_entries()
        except Exception as exc:  # pragma: no cover - repository failures are environment specific
            LOGGER.exception("Unable to load entries for advanced export")
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        if not entries:
            QMessageBox.information(self, "No entries", "There are no entries available to export yet.")
            return

        dialog = AdvancedExportDialog(entries, parent=self)
        try:
            dialog.exec()
        except Exception as exc:  # pragma: no cover - Qt dialog errors are user specific
            LOGGER.exception("Advanced export dialog failed")
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._clear_error()

    def _on_export_jf_excel_clicked(self) -> None:
        try:
            entries = self._repository.get_all_entries()
        except Exception as exc:  # pragma: no cover - repository failures are environment specific
            LOGGER.exception("Unable to load entries for JF Excel export")
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        if not entries:
            QMessageBox.information(self, "No entries", "There are no entries available to export yet.")
            return

        try:
            categories = self._category_manager.list_categories()
        except Exception as exc:  # pragma: no cover - category retrieval issues are environment specific
            LOGGER.exception("Unable to load categories for JF Excel export")
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        default_path = Path(default_downloads_dir()) / "jf.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save JF Excel export",
            str(default_path),
            "Excel Workbook (*.xlsx);;All Files (*.*)",
            "Excel Workbook (*.xlsx)",
        )
        if not file_path:
            return

        target_path = Path(file_path).expanduser()
        if target_path.suffix.lower() != ".xlsx":
            target_path = target_path.with_suffix(".xlsx")

        try:
            create_jf_excel_export(entries, categories, target_path)
        except Exception as exc:  # pragma: no cover - filesystem/environment dependent
            LOGGER.exception("JF Excel export failed")
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        LOGGER.info(
            "JF Excel export created",
            extra={
                "event": "ui_export_jf_excel",
                "path": str(target_path),
                "entries": len(entries),
                "categories": len(categories),
            },
        )

        QMessageBox.information(
            self,
            "Export complete",
            f"Exported category summary workbook to:\n{target_path}",
        )
        self._clear_error()

    def _on_export_jf_loggr_clicked(self) -> None:
        entries = self._repository.get_all_entries()
        if not entries:
            QMessageBox.information(self, "Nothing to export", "There are no entries available to export yet.")
            return

        default_path = Path(default_downloads_dir()) / "work-logger.json"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export JF LoggR data",
            str(default_path),
            "JF LoggR export (work-logger.json);;JSON Files (*.json);;All Files (*.*)",
        )
        if not file_path:
            return

        target_path = Path(file_path).expanduser()
        if not target_path.suffix:
            target_path = target_path.with_suffix(".json")

        payload = self._build_jf_loggr_export_payload(entries)

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except Exception as exc:  # pragma: no cover - filesystem/environment dependent
            LOGGER.exception("JF LoggR export failed")
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        entry_count = len(payload["entries"])
        total_minutes = sum(entry.minutes for entry in entries)

        LOGGER.info(
            "JF LoggR export created",
            extra={
                "event": "ui_export_jf_loggr",
                "entries": entry_count,
                "minutes": total_minutes,
                "path": str(target_path),
            },
        )

        QMessageBox.information(
            self,
            "Export complete",
            (
                f"Exported {entry_count} segment{'s' if entry_count != 1 else ''} "
                f"({total_minutes} minute{'s' if total_minutes != 1 else ''}) to:\n{target_path}"
            ),
        )
        self._clear_error()

    def _on_categories_clicked(self) -> None:
        dialog = CategoriesDialog(
            self._category_manager,
            self._repository,
            parent=self,
            entries_updated=self._prompt_manager.notify_entries_replaced,
        )
        try:
            dialog.exec()
        except Exception as exc:  # pragma: no cover - Qt dialog errors are user specific
            LOGGER.exception("Categories dialog failed")
            QMessageBox.critical(self, "Unable to open categories", str(exc))
            return
        self._clear_error()

    @property
    def updated_settings(self) -> Settings:
        return self._result_settings or self._initial_settings

    def closeEvent(self, event) -> None:  # type: ignore[override]
        remove_palette_listener(self._refresh_spin_icons)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def _apply_settings_to_fields(self, settings: Settings) -> None:
        index = self._theme_combo.findData(settings.theme.value)
        if index >= 0:
            self._theme_combo.setCurrentIndex(index)
        else:  # pragma: no cover - defensive
            self._theme_combo.setCurrentIndex(0)
        self._cron_edit.setText(settings.prompt_cron)
        self._sound_toggle_button.blockSignals(True)
        self._sound_toggle_button.setChecked(settings.prompt_sounds_enabled)
        self._sound_toggle_button.blockSignals(False)
        self._sound_player.set_enabled(settings.prompt_sounds_enabled)
        self._update_sound_button()
        self._app_data_edit.setText(settings.app_data_path)
        self._backup_path_edit.setText(settings.backup_path)
        self._recurring_toggle_button.blockSignals(True)
        self._recurring_toggle_button.setChecked(settings.recurring_backup_enabled)
        self._recurring_toggle_button.blockSignals(False)
        interval_value = max(1, settings.recurring_backup_interval_days)
        self._recurring_interval_spin.setValue(interval_value)
        retention_value = min(100, max(1, settings.recurring_backup_retention_days))
        self._recurring_retention_spin.setValue(retention_value)
        self._recurring_path_edit.setText(settings.recurring_backup_path)
        self._update_recurring_controls_enabled(settings.recurring_backup_enabled)
        clamped_missing = max(
            self._missing_slot_spin.minimum(),
            min(self._missing_slot_spin.maximum(), settings.missing_timeslot_threshold_minutes),
        )
        self._missing_slot_spin.setValue(clamped_missing)

    def _create_spin_control(self, spin: QSpinBox, *, step: int) -> QWidget:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        minus_button = QToolButton(container)
        minus_button.setAutoRaise(True)
        minus_button.setCursor(Qt.PointingHandCursor)
        minus_button.setIconSize(QSize(18, 18))
        minus_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        minus_button.setAutoRepeat(True)
        minus_button.setAutoRepeatInterval(150)
        minus_button.clicked.connect(lambda _checked=False, box=spin: self._adjust_spin(box, -1))

        plus_button = QToolButton(container)
        plus_button.setAutoRaise(True)
        plus_button.setCursor(Qt.PointingHandCursor)
        plus_button.setIconSize(QSize(18, 18))
        plus_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        plus_button.setAutoRepeat(True)
        plus_button.setAutoRepeatInterval(150)
        plus_button.clicked.connect(lambda _checked=False, box=spin: self._adjust_spin(box, 1))

        spin.setParent(container)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setSingleStep(step)
        spin.setAccelerated(True)
        spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        spin.setMinimumWidth(80)

        layout.addWidget(minus_button)
        layout.addWidget(spin, 1)
        layout.addWidget(plus_button)

        self._spin_buttons[spin] = (minus_button, plus_button)
        self._refresh_spin_icons()
        return container

    def _adjust_spin(self, spin: QSpinBox, steps: int) -> None:
        if not spin.isEnabled():
            return
        delta = spin.singleStep() * steps
        new_value = spin.value() + delta
        bounded = max(spin.minimum(), min(spin.maximum(), new_value))
        spin.setValue(bounded)

    def _set_spin_button_tooltips(
        self,
        spin: QSpinBox,
        minus_button: QToolButton,
        plus_button: QToolButton,
    ) -> None:
        amount = spin.singleStep()
        suffix = spin.suffix().strip()
        suffix_text = f" {suffix}" if suffix else ""
        minus_button.setToolTip(f"Decrease by {amount}{suffix_text}")
        plus_button.setToolTip(f"Increase by {amount}{suffix_text}")

    def _refresh_spin_icons(self) -> None:
        for spin, (minus_button, plus_button) in self._spin_buttons.items():
            minus_button.setIcon(minus_icon(18))
            plus_button.setIcon(plus_icon(18))
            self._set_spin_button_tooltips(spin, minus_button, plus_button)

    def _clear_error(self) -> None:
        self._error_label.clear()

    def _build_jf_loggr_export_payload(self, entries: Sequence[Entry]) -> dict[str, object]:
        items: list[dict[str, object]] = []
        for entry in sorted(entries, key=lambda e: (e.segment_start, e.segment_end, e.task.lower())):
            item: dict[str, object] = {
                "day": entry.segment_start.strftime("%Y-%m-%d"),
                "start": entry.segment_start.strftime("%H:%M"),
                "end": entry.segment_end.strftime("%H:%M"),
                "description": entry.task,
            }
            if entry.category:
                item["category"] = entry.category
            items.append(item)
        return {"entries": items}

    def _on_open_appdata_clicked(self) -> None:
        path_text = self._app_data_edit.text().strip()
        if not path_text:
            path_text = str(default_app_data_dir())
            self._app_data_edit.setText(path_text)
        target = Path(path_text).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - filesystem dependent
            LOGGER.warning("Unable to open app data folder: %s", exc)
            QMessageBox.warning(self, "Unable to open", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _on_reset_clicked(self) -> None:
        if QMessageBox.question(
            self,
            "Reset settings",
            "Restore all settings to their defaults?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        defaults = Settings(
            theme=Theme.DARK,
            prompt_cron=DEFAULT_PROMPT_CRON,
            prompt_sounds_enabled=True,
            auto_launch_on_startup=False,
            app_data_path=str(default_app_data_dir()),
            backup_path=str(default_downloads_dir()),
            recurring_backup_enabled=True,
            recurring_backup_interval_days=1,
            recurring_backup_retention_days=7,
            recurring_backup_path=str(recurring_backups_dir()),
            missing_timeslot_threshold_minutes=240,
        )
        self._apply_settings_to_fields(defaults)
        self._result_settings = defaults
        self.accept()
