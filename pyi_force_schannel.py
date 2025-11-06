"""PyInstaller runtime hook to force Qt to use the Schannel TLS backend."""

from __future__ import annotations

import os
import sys

# Qt consults QT_SSL_BACKEND during initialization; setting it early ensures
# the native Windows Schannel backend is preferred over OpenSSL.
os.environ.setdefault("QT_SSL_BACKEND", "schannel")

# Point Qt at the bundled plugin tree extracted by PyInstaller so the Schannel
# TLS plugin is discoverable in one-file builds.
_meipass = getattr(sys, "_MEIPASS", None)
if _meipass:
    plugins_root = os.path.join(_meipass, "PySide6", "Qt", "plugins")
    os.environ.setdefault("QT_PLUGIN_PATH", plugins_root)
