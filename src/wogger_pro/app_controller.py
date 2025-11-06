"""Application controller wiring all services together."""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

from PySide6.QtNetwork import QSslSocket  # type: ignore[import]
from PySide6.QtWidgets import QDialog, QApplication, QMessageBox  # type: ignore[import]

from .core.category_consistency import analyze_category_consistency
from .core.categories import CategoryManager
from .core.exception_logging import install_global_exception_logger
from .core.exceptions import PersistenceError, SettingsError
from .core.logging_config import configure_logging, reset_logging
from .core.paths import ensure_app_structure, recurring_backups_dir, set_app_data_directory
from .core.prompt_manager import PromptManager
from .core.repository import EntriesRepository
from .core.scheduler import PromptScheduler
from .core.settings import Settings, SettingsManager, Theme
from .core.recurring_backup import process_recurring_backups
from .ui.category_conflict_dialog import CategoryConflictDialog
from .ui.icons import app_icon
from .ui.main_window import MainWindow
from .ui.prompt_service import PromptService
from .ui.sound_player import SoundPlayer
from .ui.settings_dialog import SettingsDialog
from .ui.theme_manager import ThemeManager
from .ui.qt_message_handler import install_qt_message_handler

LOGGER = logging.getLogger("wogger.app")


class ApplicationController:
    def __init__(self, app: QApplication) -> None:
        try:
            QSslSocket.setActiveBackend("schannel")
        except Exception:
            LOGGER.warning("Failed to activate Schannel TLS backend", exc_info=True)
        else:
            LOGGER.info(
                "Qt TLS: available=%s active=%s supportsSsl=%s",
                list(QSslSocket.availableBackends()),
                QSslSocket.activeBackend(),
                QSslSocket.supportsSsl(),
            )
        self._app = app
        self._startup_aborted = False
        self._scheduler: PromptScheduler | None = None
        self._prompt_manager: PromptManager | None = None
        self._prompt_service: PromptService | None = None
        self._sound_player: SoundPlayer | None = None
        self._main_window: MainWindow | None = None
        self._repository: EntriesRepository | None = None
        self._theme_manager: ThemeManager | None = None
        self._settings_manager = SettingsManager()
        try:
            self._settings = self._settings_manager.load()
        except SettingsError as exc:
            LOGGER.exception("Failed to load settings; using defaults")
            QMessageBox.warning(None, "Settings error", str(exc))
            self._settings = Settings()

        set_app_data_directory(Path(self._settings.app_data_path).expanduser())
        ensure_app_structure()
        configure_logging()
        install_global_exception_logger()
        install_qt_message_handler()
        # Rebuild manager to ensure it points at the active app data directory
        self._settings_manager = SettingsManager()

        self._theme_manager = ThemeManager(app)
        self._theme_manager.apply(self._settings.theme)

        self._repository = EntriesRepository()
        if not self._ensure_category_consistency():
            self._startup_aborted = True
            self._app.quit()
            return

        self._scheduler = PromptScheduler(self._settings.prompt_cron)
        self._prompt_manager = PromptManager(self._scheduler, self._repository)
        self._sound_player = SoundPlayer()
        self._sound_player.set_enabled(self._settings.prompt_sounds_enabled)
        self._prompt_service = PromptService(self._prompt_manager, self._sound_player)

        self._main_window = MainWindow(
            self._repository,
            self._prompt_manager,
            self._prompt_service,
            self._settings,
        )
        self._apply_window_icon()
        self._connect_signals()

        self._prompt_manager.start()
        self._main_window.show()
        self._run_recurring_backup_cycle()

    def _connect_signals(self) -> None:
        if self._main_window is None:
            return
        self._main_window.settings_requested.connect(self._open_settings_dialog)

    def _ensure_category_consistency(self) -> bool:
        if self._repository is None:
            return True

        while True:
            try:
                entries = self._repository.get_all_entries()
            except PersistenceError as exc:
                LOGGER.exception("Unable to load entries for category audit")
                QMessageBox.critical(
                    None,
                    "Unable to read entries",
                    f"Categories could not be validated: {exc}",
                )
                return True

            auto_assign, conflicts = analyze_category_consistency(entries)
            auto_updates_applied = False
            for task, category in auto_assign:
                try:
                    updated = self._repository.assign_category_to_task(task, category)
                    if updated:
                        auto_updates_applied = True
                except (PersistenceError, ValueError) as exc:
                    LOGGER.exception("Failed to auto-assign category for task '%s'", task)
                    QMessageBox.warning(
                        None,
                        "Category update failed",
                        f"Unable to update the category for task '{task}': {exc}",
                    )

            if auto_updates_applied:
                continue

            if not conflicts:
                return True

            manager = CategoryManager()
            known_categories = manager.list_categories()
            dialog = CategoryConflictDialog(conflicts, known_categories, parent=None)
            result = dialog.exec()
            if result != QDialog.Accepted:
                QMessageBox.information(
                    None,
                    "Category selection required",
                    "The application cannot continue until category conflicts are resolved.",
                )
                return False

            selections = dialog.selected_categories()
            had_error = False
            for task, category in selections.items():
                try:
                    self._repository.assign_category_to_task(task, category)
                except (PersistenceError, ValueError) as exc:
                    LOGGER.exception("Failed to apply selected category for task '%s'", task)
                    QMessageBox.critical(
                        None,
                        "Unable to update category",
                        f"Task '{task}': {exc}",
                    )
                    had_error = True
                    break

            if had_error:
                continue

            # Re-run the analysis to ensure all conflicts are resolved.
            continue

    def _open_settings_dialog(self) -> None:
        if self._main_window is None or self._prompt_manager is None or self._sound_player is None:
            return
        if self._repository is None:
            return
        dialog = SettingsDialog(
            self._settings,
            self._repository,
            self._prompt_manager,
            self._sound_player,
            parent=self._main_window,
        )
        result = dialog.exec()
        if result != QDialog.DialogCode.Accepted:
            return
        new_settings = dialog.updated_settings

        try:
            self._apply_settings_update(new_settings)
        except SettingsError as exc:
            LOGGER.exception("Unable to apply settings")
            QMessageBox.critical(self._main_window, "Settings error", str(exc))
            return

        try:
            self._settings_manager.save(self._settings)
        except SettingsError as exc:
            LOGGER.exception("Unable to persist settings")
            QMessageBox.critical(self._main_window, "Settings error", str(exc))
            return

        self._run_recurring_backup_cycle()

    # ------------------------------------------------------------------
    def _apply_settings_update(self, new_settings: Settings) -> None:
        if self._scheduler is None or self._theme_manager is None:
            return
        old_settings = self._settings

        old_app_path = Path(old_settings.app_data_path).expanduser()
        new_app_path = Path(new_settings.app_data_path).expanduser()
        if old_app_path.resolve() != new_app_path.resolve():
            self._handle_app_data_change(old_app_path, new_app_path)

        if new_settings.theme != old_settings.theme:
            self._theme_manager.apply(new_settings.theme)

        if new_settings.prompt_cron != old_settings.prompt_cron:
            self._scheduler.update_cron(new_settings.prompt_cron)

        if self._sound_player is not None:
            self._sound_player.set_enabled(new_settings.prompt_sounds_enabled)

        self._settings = new_settings
        if self._main_window is not None:
            self._main_window.apply_settings(new_settings)

    def _handle_app_data_change(self, old_path: Path, new_path: Path) -> None:
        if self._prompt_manager is None:
            return
        if self._main_window is None:
            return
        old_resolved = old_path.resolve()
        new_resolved = new_path.resolve()

        if old_resolved == new_resolved:
            set_app_data_directory(new_resolved)
            ensure_app_structure()
            return

        if old_resolved.is_relative_to(new_resolved):
            raise SettingsError("The new app data path cannot contain the existing directory.")
        if new_resolved.is_relative_to(old_resolved):
            raise SettingsError("The new app data path cannot be inside the existing directory.")

        new_resolved.mkdir(parents=True, exist_ok=True)

        # Pause prompt processing while we migrate files
        self._prompt_manager.stop()
        reset_logging(reconfigure=False)
        try:
            self._move_app_data_contents(old_resolved, new_resolved)
        except Exception as exc:
            configure_logging()
            self._prompt_manager.start()
            raise SettingsError("Failed to move app data to the new location") from exc

        set_app_data_directory(new_resolved)
        ensure_app_structure()
        configure_logging()

        self._repository = EntriesRepository()
        self._prompt_manager.update_repository(self._repository)
        self._main_window.update_repository(self._repository)

        self._settings_manager = SettingsManager()
        self._prompt_manager.start()

    def _run_recurring_backup_cycle(self) -> None:
        if self._main_window is None:
            return
        target_path_text = self._settings.recurring_backup_path.strip()
        if target_path_text:
            target_path = Path(target_path_text).expanduser()
        else:
            target_path = recurring_backups_dir()

        outcome = process_recurring_backups(
            enabled=self._settings.recurring_backup_enabled,
            interval_days=self._settings.recurring_backup_interval_days,
            retention_days=self._settings.recurring_backup_retention_days,
            target_directory=target_path,
        )

        if outcome.error is not None:
            QMessageBox.warning(
                self._main_window,
                "Backup failed",
                f"Automatic backup could not be completed:\n{outcome.error}",
            )

    def _move_app_data_contents(self, source: Path, destination: Path) -> None:
        if not source.exists():
            return

        for item in source.iterdir():
            target = destination / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))

        # Attempt to remove the now-empty source directory tree
        try:
            for path in sorted(source.rglob("*"), reverse=True):
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink(missing_ok=True)
            source.rmdir()
        except Exception:
            LOGGER.debug("Left original app data directory in place", exc_info=True)

    def _apply_window_icon(self) -> None:
        icon = app_icon()
        if icon is not None and self._main_window is not None:
            self._app.setWindowIcon(icon)
            self._main_window.setWindowIcon(icon)


def run_app(argv: list[str] | None = None) -> int:
    qt_args = argv if argv is not None else sys.argv
    app = QApplication(qt_args)
    controller = ApplicationController(app)
    return app.exec()
