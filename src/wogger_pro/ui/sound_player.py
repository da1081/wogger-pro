"""Sound playback utilities for Wogger prompt notifications."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QSoundEffect

from ..core.paths import alert_sound_path

LOGGER = logging.getLogger("wogger.ui.sound")


class SoundPlayer(QObject):
    """Lightweight wrapper around ``QSoundEffect`` with enable/disable support."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._enabled = True
        self._effect: QSoundEffect | None = None
        self._effective_path: Path | None = None
        self._status_logged = False
        self._load_sound()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable playback without releasing resources."""

        self._enabled = bool(enabled)

    def play_prompt(self) -> None:
        """Play the prompt notification sound if available and enabled."""

        if not self._enabled:
            return
        effect = self._effect
        if effect is None:
            return
        try:
            # Restart playback in case the effect is already playing.
            effect.stop()
            effect.play()
        except Exception:  # pragma: no cover - QtMultimedia failures are environment specific
            LOGGER.exception("Failed to play prompt sound")

    # ------------------------------------------------------------------
    def _load_sound(self) -> None:
        path = alert_sound_path()
        self._effective_path = path
        if not path.exists():
            LOGGER.warning(
                "Prompt sound file not found; sounds will be disabled",
                extra={"event": "prompt_sound_missing", "path": str(path)},
            )
            self._effect = None
            return

        try:
            effect = QSoundEffect(self)
        except Exception:  # pragma: no cover - backend availability is platform specific
            LOGGER.exception("Unable to initialize sound effect backend")
            self._effect = None
            return

        effect.setLoopCount(1)
        effect.setVolume(0.6)
        effect.statusChanged.connect(self._on_status_changed)
        effect.setSource(QUrl.fromLocalFile(str(path)))
        self._effect = effect
        # Log immediately if the backend reports an issue right away.
        self._emit_initial_status(effect)

    def _emit_initial_status(self, effect: QSoundEffect) -> None:
        status = effect.status()
        if status == QSoundEffect.Status.Error:
            self._log_status_error()

    def _on_status_changed(self) -> None:
        effect = self._effect
        if effect is None:
            return
        if effect.status() == QSoundEffect.Status.Error:
            self._log_status_error()

    def _log_status_error(self) -> None:
        if self._status_logged:
            return
        self._status_logged = True
        LOGGER.warning(
            "Prompt sound effect reported an error",
            extra={"event": "prompt_sound_error", "path": str(self._effective_path or "")},
        )
        self._effect = None
