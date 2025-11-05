"""Executable entry point for both package and frozen builds."""

from __future__ import annotations

import pathlib
import sys

if __package__ in (None, ""):
    # When executed as a script (e.g., via PyInstaller), ensure the project
    # root is on sys.path so absolute imports resolve correctly.
    project_root = pathlib.Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from wogger_pro.app import main


if __name__ == "__main__":
    sys.exit(main())
