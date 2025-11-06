"""PyInstaller runtime hook to preload bundled OpenSSL libraries."""

from __future__ import annotations

import os
import sys
from ctypes import WinDLL

_meipass = getattr(sys, "_MEIPASS", None)
if _meipass and os.name == "nt":
    candidates = [
        "libcrypto-3-x64.dll",
        "libssl-3-x64.dll",
    ]
    for name in candidates:
        path = os.path.join(_meipass, name)
        if os.path.exists(path):
            try:
                WinDLL(path)
            except OSError:
                # Safe to ignore; loader will fall back to PATH ordering.
                pass
