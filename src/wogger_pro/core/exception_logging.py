"""Helpers for logging uncaught exceptions across the application."""

from __future__ import annotations

import logging
import sys
import threading
from types import TracebackType
from typing import Optional, Type

_LOGGER = logging.getLogger("wogger.exceptions")
_INSTALLED = False
_PREVIOUS_SYS_HOOK = None
_PREVIOUS_THREAD_HOOK = None


def _log_exception(exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: Optional[TracebackType]) -> None:
    """Write an uncaught exception to the shared application logger."""
    _LOGGER.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))


def install_global_exception_logger() -> None:
    """Route uncaught exceptions (main thread and worker threads) through logging."""

    global _INSTALLED, _PREVIOUS_SYS_HOOK, _PREVIOUS_THREAD_HOOK
    if _INSTALLED:
        return

    _PREVIOUS_SYS_HOOK = sys.excepthook
    _PREVIOUS_THREAD_HOOK = getattr(threading, "excepthook", None)

    def _handle_exception(exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: Optional[TracebackType]) -> None:
        if issubclass(exc_type, KeyboardInterrupt):  # pragma: no cover - pass through interactive cancellation
            if _PREVIOUS_SYS_HOOK is not None:
                _PREVIOUS_SYS_HOOK(exc_type, exc_value, exc_traceback)
            else:
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        _log_exception(exc_type, exc_value, exc_traceback)
        if _PREVIOUS_SYS_HOOK not in (None, sys.excepthook, _handle_exception):
            _PREVIOUS_SYS_HOOK(exc_type, exc_value, exc_traceback)
        else:
            sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = _handle_exception

    def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:  # pragma: no cover - trivial wrapper
        if issubclass(args.exc_type, KeyboardInterrupt):
            if callable(_PREVIOUS_THREAD_HOOK):
                _PREVIOUS_THREAD_HOOK(args)
            return
        _log_exception(args.exc_type, args.exc_value, args.exc_traceback)
        if callable(_PREVIOUS_THREAD_HOOK):
            _PREVIOUS_THREAD_HOOK(args)

    if hasattr(threading, "excepthook"):
        threading.excepthook = _handle_thread_exception  # type: ignore[attr-defined]

    _INSTALLED = True
