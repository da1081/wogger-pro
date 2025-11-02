"""Application-wide logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import Iterable

from .paths import ensure_app_structure, log_path

_LOGGER_INITIALIZED = False

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
DEFAULT_LOG_LEVEL = logging.INFO


def _build_handlers() -> list[logging.Handler]:
    file_handler = RotatingFileHandler(
        log_path(), maxBytes=5_000_000, backupCount=5, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return [file_handler]


def configure_logging(level: int | str = DEFAULT_LOG_LEVEL, extra_handlers: Iterable[logging.Handler] | None = None) -> logging.Logger:
    """Configure the shared application logger and return it."""
    global _LOGGER_INITIALIZED
    logger = logging.getLogger("wogger")
    if _LOGGER_INITIALIZED:
        if level:
            logger.setLevel(level)
        return logger

    ensure_app_structure()

    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handlers = _build_handlers()
    if extra_handlers:
        handlers.extend(extra_handlers)

    for handler in handlers:
        logger.addHandler(handler)

    logging.captureWarnings(True)

    _LOGGER_INITIALIZED = True
    logger.info("Logging initialized", extra={"event": "logging_configured", "level": level})
    return logger


def reset_logging(level: int | str = DEFAULT_LOG_LEVEL, *, reconfigure: bool = True) -> logging.Logger:
    """Close existing handlers and optionally rebuild logging configuration."""
    global _LOGGER_INITIALIZED
    logger = logging.getLogger("wogger")
    for handler in list(logger.handlers):
        try:
            handler.close()
        finally:
            logger.removeHandler(handler)
    _LOGGER_INITIALIZED = False
    if reconfigure:
        return configure_logging(level)
    return logger
