"""PyInstaller runtime hook to force Qt to use the Schannel TLS backend."""

from __future__ import annotations

import os
import sys

# CRITICAL: Set these BEFORE any Qt/PySide6 imports happen.
# This must execute at the PyInstaller bootloader stage, not in application code.
# Use direct assignment (not setdefault) to ensure these values take precedence.
os.environ["QT_SSL_BACKEND"] = "schannel"
os.environ["QT_SSL_USE_SCHANNEL"] = "1"
os.environ["QT_SSL_USE_OPENSSL"] = "0"

# Point Qt at the bundled plugin tree extracted by PyInstaller so the Schannel
# TLS plugin is discoverable in one-file builds.
_meipass = getattr(sys, "_MEIPASS", None)
if _meipass:
    plugins_root = os.path.join(_meipass, "PySide6", "Qt", "plugins")
    os.environ["QT_PLUGIN_PATH"] = plugins_root
    
    # Ensure TLS plugin path is explicitly set
    tls_path = os.path.join(plugins_root, "tls")
    if os.path.isdir(tls_path):
        # Add to QT_PLUGIN_PATH if not already there
        current_path = os.environ.get("QT_PLUGIN_PATH", "")
        if tls_path not in current_path:
            os.environ["QT_PLUGIN_PATH"] = f"{current_path};{tls_path}" if current_path else tls_path

# AGGRESSIVE: Filter System32 from PATH to prevent Qt from finding Windows OpenSSL DLLs
# This is a safety measure to ensure Qt doesn't attempt to load incompatible system OpenSSL
if os.name == "nt":
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    # Keep only non-system32 directories
    filtered = [d for d in path_dirs if "system32" not in d.lower() and "syswow64" not in d.lower()]
    os.environ["PATH"] = os.pathsep.join(filtered)
