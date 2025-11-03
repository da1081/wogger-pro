"""Centralized Qt message handling for logging and suppression."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QtMsgType, QMessageLogContext, qInstallMessageHandler

LOGGER = logging.getLogger("wogger.qt")
_SOUND_LOGGER = logging.getLogger("wogger.ui.sound")

_AUDIO_DEVICE_INVALIDATED = "IAudioClient3::GetCurrentPadding failed"

_previous_handler: Optional[Callable[[QtMsgType, QMessageLogContext, str], None]] = None
_installed = False


def install_qt_message_handler() -> None:
    """Install a Qt message handler that routes logs through Python logging."""

    global _installed, _previous_handler
    if _installed:
        return
    _previous_handler = qInstallMessageHandler(_handle_qt_message)
    _installed = True


def _handle_qt_message(msg_type: QtMsgType, context: QMessageLogContext, message: str) -> None:
    """Forward Qt messages to the Python logging subsystem."""

    if _AUDIO_DEVICE_INVALIDATED in (message or ""):
        _SOUND_LOGGER.warning("Audio device invalidated; disabling sound playback. message=%s", message)
        try:
            from .sound_player import notify_audio_device_invalidated

            notify_audio_device_invalidated()
        except Exception:  # pragma: no cover - defensive import
            LOGGER.debug("Unable to notify sound players about device invalidation", exc_info=True)
        return

    if msg_type == QtMsgType.QtDebugMsg:
        LOGGER.debug(message)
    elif msg_type in (QtMsgType.QtInfoMsg, getattr(QtMsgType, "QtSystemMsg", QtMsgType.QtInfoMsg)):
        LOGGER.info(message)
    elif msg_type == QtMsgType.QtWarningMsg:
        LOGGER.warning(message)
    elif msg_type == QtMsgType.QtCriticalMsg:
        LOGGER.error(message)
    elif msg_type == QtMsgType.QtFatalMsg:
        LOGGER.critical(message)
    else:
        LOGGER.warning("Unhandled Qt message (%s): %s", msg_type, message)

    if _previous_handler is not None and msg_type == QtMsgType.QtFatalMsg:
        _previous_handler(msg_type, context, message)
