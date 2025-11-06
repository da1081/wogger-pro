"""PyInstaller runtime hook to force Qt to use the Schannel TLS backend."""

from __future__ import annotations

import os

# Qt consults QT_SSL_BACKEND during initialization; setting it early ensures
# the native Windows Schannel backend is preferred over OpenSSL.
os.environ.setdefault("QT_SSL_BACKEND", "schannel")
