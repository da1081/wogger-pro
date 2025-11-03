"""Settings dialog for Wogger Pro."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, QUrl  # type: ignore[import]
from PySide6.QtGui import QDesktopServices  # type: ignore[import]
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QVBoxLayout,
    QWidget,
)

from ..core.backup import create_appdata_backup
from ..core.categories import CategoryManager
from ..core.exceptions import BackupError, SettingsError
from ..core.paths import default_app_data_dir, default_downloads_dir, recurring_backups_dir
from ..core.prompt_manager import PromptManager
from ..core.repository import EntriesRepository
from ..core.settings import DEFAULT_PROMPT_CRON, Settings, Theme
from .categories_dialog import CategoriesDialog
from .csv_import_dialog import CsvImportDialog
from .icons import app_icon, import_icon, sound_off_icon, sound_on_icon
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

        self._recurring_enabled_checkbox = QCheckBox("Enable recurring automatic backups", self)
        self._recurring_enabled_checkbox.setChecked(True)
        recurring_layout.addWidget(self._recurring_enabled_checkbox)

        interval_row = QWidget(recurring_widget)
        interval_layout = QHBoxLayout(interval_row)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        interval_layout.setSpacing(8)

        interval_layout.addWidget(QLabel("Create backup every", interval_row))
        self._recurring_interval_spin = QSpinBox(interval_row)
        self._recurring_interval_spin.setRange(1, 365)
        self._recurring_interval_spin.setValue(1)
        interval_layout.addWidget(self._recurring_interval_spin)
        interval_layout.addWidget(QLabel("day(s)", interval_row))
        interval_layout.addStretch(1)
        recurring_layout.addWidget(interval_row)

        retention_row = QWidget(recurring_widget)
        retention_layout = QHBoxLayout(retention_row)
        retention_layout.setContentsMargins(0, 0, 0, 0)
        retention_layout.setSpacing(8)

        retention_layout.addWidget(QLabel("Keep backups for", retention_row))
        self._recurring_retention_spin = QSpinBox(retention_row)
        self._recurring_retention_spin.setRange(1, 100)
        self._recurring_retention_spin.setValue(7)
        retention_layout.addWidget(self._recurring_retention_spin)
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
        import_layout = QHBoxLayout(import_widget)
        import_layout.setContentsMargins(0, 0, 0, 0)
        import_layout.setSpacing(8)

        self._import_button = QPushButton("Import CSV Data…", self)
        try:
            self._import_button.setIcon(import_icon())
        except Exception:  # pragma: no cover - icon fallback
            LOGGER.debug("Import icon unavailable", exc_info=True)
        self._import_button.setToolTip("Import legacy Wogger CSV exports")
        self._import_button.clicked.connect(self._on_import_clicked)
        import_layout.addWidget(self._import_button)
        import_layout.addStretch(1)

        layout.addRow("Legacy data", import_widget)

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
        self._recurring_enabled_checkbox.toggled.connect(self._on_recurring_enabled_toggled)
        self._recurring_interval_spin.valueChanged.connect(lambda _value: self._clear_error())
        self._recurring_retention_spin.valueChanged.connect(lambda _value: self._clear_error())
        self._recurring_path_edit.textChanged.connect(self._clear_error)

        self._update_recurring_controls_enabled()

        self._apply_settings_to_fields(current_settings)

    def _on_accept(self) -> None:
        theme_value = self._theme_combo.currentData()
        cron_value = self._cron_edit.text().strip()
        try:
            recurring_enabled = self._recurring_enabled_checkbox.isChecked()
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

    def _on_recurring_enabled_toggled(self, _checked: bool) -> None:
        self._update_recurring_controls_enabled()
        self._clear_error()

    def _update_recurring_controls_enabled(self) -> None:
        enabled = self._recurring_enabled_checkbox.isChecked()
        targets = (
            self._recurring_interval_spin,
            self._recurring_retention_spin,
            self._recurring_path_edit,
            self._recurring_browse_button,
        )
        for widget in targets:
            widget.setEnabled(enabled)

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
        self._recurring_enabled_checkbox.setChecked(settings.recurring_backup_enabled)
        interval_value = max(1, settings.recurring_backup_interval_days)
        self._recurring_interval_spin.setValue(interval_value)
        retention_value = min(100, max(1, settings.recurring_backup_retention_days))
        self._recurring_retention_spin.setValue(retention_value)
        self._recurring_path_edit.setText(settings.recurring_backup_path)
        self._update_recurring_controls_enabled()

    def _clear_error(self) -> None:
        self._error_label.clear()

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
        )
        self._apply_settings_to_fields(defaults)
        self._result_settings = defaults
        self.accept()
