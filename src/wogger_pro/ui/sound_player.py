"""Sound playback utilities for Wogger prompt notifications."""

from __future__ import annotations

import logging
from pathlib import Path
from weakref import WeakSet

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QMediaDevices, QSoundEffect

from ..core.paths import alert_sound_path

LOGGER = logging.getLogger("wogger.ui.sound")


_ACTIVE_PLAYERS: "WeakSet[SoundPlayer]" = WeakSet()


def notify_audio_device_invalidated() -> None:
    """Notify all active players that the audio device became invalid."""

    for player in list(_ACTIVE_PLAYERS):
        try:
            player._on_audio_device_invalidated()
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.debug("Unable to invalidate sound player", exc_info=True)


class SoundPlayer(QObject):
    """Lightweight wrapper around ``QSoundEffect`` with enable/disable support."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._user_enabled = True
        self._backend_available = True
        self._effect: QSoundEffect | None = None
        self._effective_path: Path | None = None
        self._failure_logged = False
        try:
            self._media_devices = QMediaDevices(self)
            self._media_devices.audioOutputsChanged.connect(self._on_outputs_changed)
        except Exception:  # pragma: no cover - environment specific
            LOGGER.debug("Unable to monitor audio device changes", exc_info=True)
            self._media_devices = None
        self._load_sound()
        _ACTIVE_PLAYERS.add(self)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable playback without releasing resources."""

        self._user_enabled = bool(enabled)
        if not self._user_enabled and self._effect is not None:
            try:
                self._effect.stop()
            except Exception:  # pragma: no cover - backend shutdown robustness
                LOGGER.debug("Unable to stop sound effect while disabling", exc_info=True)
        if self._user_enabled and not self._backend_available:
            self._load_sound()

    def play_prompt(self) -> None:
        """Play the prompt notification sound if available and enabled."""

        if not self._user_enabled or not self._backend_available:
            return
        effect = self._effect
        if effect is None:
            self._load_sound()
            effect = self._effect
            if effect is None or not self._backend_available:
                return
        try:
            # Restart playback in case the effect is already playing.
            effect.stop()
            effect.play()
        except Exception as exc:  # pragma: no cover - QtMultimedia failures are environment specific
            LOGGER.exception("Failed to play prompt sound")
            self._handle_backend_failure("play_exception", exc)

    # ------------------------------------------------------------------
    def _load_sound(self) -> None:
        self._release_effect()
        try:
            default_output = QMediaDevices.defaultAudioOutput()
        except Exception as exc:  # pragma: no cover - QtMultimedia robustness
            LOGGER.exception("Unable to query audio outputs")
            self._handle_backend_failure("device_query_failed", exc)
            return

        try:
            if not default_output or getattr(default_output, "isNull", lambda: False)():
                self._handle_backend_failure("no_output_device")
                return
        except Exception:  # pragma: no cover - defensive against missing isNull implementation
            pass

        path = alert_sound_path()
        self._effective_path = path
        if not path.exists():
            LOGGER.warning(
                "Prompt sound file not found; sounds will be disabled",
                extra={"event": "prompt_sound_missing", "path": str(path)},
            )
            self._backend_available = False
            self._effect = None
            return

        try:
            effect = QSoundEffect(self)
        except Exception:  # pragma: no cover - backend availability is platform specific
            LOGGER.exception("Unable to initialize sound effect backend")
            self._handle_backend_failure("init_failed")
            return

        effect.setLoopCount(1)
        effect.setVolume(0.6)
        effect.statusChanged.connect(self._on_status_changed)
        effect.setSource(QUrl.fromLocalFile(str(path)))
        self._effect = effect
        self._backend_available = True
        self._failure_logged = False
        # Log immediately if the backend reports an issue right away.
        self._emit_initial_status(effect)

    def _emit_initial_status(self, effect: QSoundEffect) -> None:
        status = effect.status()
        if status == QSoundEffect.Status.Error:
            self._handle_backend_failure("initial_status_error")

    def _on_status_changed(self) -> None:
        effect = self._effect
        if effect is None:
            return
        if effect.status() == QSoundEffect.Status.Error:
            self._handle_backend_failure("status_changed_error")

    def _handle_backend_failure(self, reason: str, exc: Exception | None = None) -> None:
        self._release_effect()
        self._backend_available = False
        if self._failure_logged:
            return
        self._failure_logged = True
        message = "Prompt sound error; sounds will stay disabled"
        if exc is not None:
            LOGGER.warning(
                message,
                exc_info=exc,
                extra={
                    "event": "prompt_sound_error",
                    "reason": reason,
                    "path": str(self._effective_path or ""),
                },
            )
        else:
            LOGGER.warning(
                message,
                extra={
                    "event": "prompt_sound_error",
                    "reason": reason,
                    "path": str(self._effective_path or ""),
                },
            )

    def _release_effect(self) -> None:
        effect = self._effect
        if effect is None:
            return
        try:
            effect.stop()
        except Exception:  # pragma: no cover - backend cleanup robustness
            LOGGER.debug("Unable to stop effect during release", exc_info=True)
        effect.deleteLater()
        self._effect = None

    def _on_outputs_changed(self) -> None:
        if not self._user_enabled:
            self._release_effect()
            self._backend_available = False
            self._failure_logged = False
            return
        self._backend_available = True
        self._failure_logged = False
        self._load_sound()

    def _on_audio_device_invalidated(self) -> None:
        if not self._backend_available and self._effect is None:
            return
        LOGGER.warning("Audio device invalidated; disabling sounds until reloaded")
        self._release_effect()
        self._backend_available = False
        self._failure_logged = False
