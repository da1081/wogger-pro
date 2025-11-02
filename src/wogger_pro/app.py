"""Application entry point for Wogger Pro."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from .app_controller import run_app

LOGGER = logging.getLogger("wogger.main")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else None
    LOGGER.info("Starting Wogger Pro", extra={"event": "app_start"})
    exit_code = run_app(args)
    LOGGER.info("Wogger Pro exited", extra={"event": "app_exit", "code": exit_code})
    return exit_code
