# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for Wogger Pro."""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all  # type: ignore[import]
import PySide6

project_root = Path(sys.argv[0]).resolve().parent
src_root = project_root / "src"
entry_point = src_root / "wogger_pro" / "__main__.py"
resources_dir = project_root / "resources"


qtawesome_datas, qtawesome_binaries, qtawesome_hiddenimports = collect_all("qtawesome")

binaries = qtawesome_binaries

# CRITICAL: Filter out any OpenSSL DLLs to prevent conflicts with system libraries.
# We use Windows native Schannel, so OpenSSL binaries must not be bundled.
binaries = [
    (path, dest)
    for path, dest in binaries
    if not any(
        excluded in Path(path).name.lower()
        for excluded in ["libssl", "libcrypto", "openssl"]
    )
]

datas = [(str(resources_dir), "resources")] + qtawesome_datas
datas.append((str(project_root / "qt.conf"), "."))

hiddenimports = [
    "PySide6.QtNetwork",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
] + qtawesome_hiddenimports

block_cipher = None

plugins_dir = Path(PySide6.__file__).parent / "Qt" / "plugins"
tls_dir = plugins_dir / "tls"

# Bundle ONLY the Schannel TLS plugin, exclude OpenSSL to prevent DLL conflicts
schannel_plugin = None
if tls_dir.exists():
    for plugin_file in tls_dir.iterdir():
        if "schannel" in plugin_file.name.lower() and plugin_file.suffix == ".dll":
            schannel_plugin = plugin_file
            break
    
    if schannel_plugin:
        # Bundle only the Schannel plugin, not the entire tls directory
        datas.append((str(schannel_plugin), "PySide6/Qt/plugins/tls"))
    else:
        # Fallback: bundle entire tls directory but we'll filter binaries later
        datas.append((str(tls_dir), "PySide6/Qt/plugins/tls"))


a = Analysis(
    [str(entry_point)],
    pathex=[str(src_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "pyi_force_schannel.py")],
    excludes=[
        # Exclude OpenSSL-related modules to prevent any bundling
        "OpenSSL",
        "cryptography.hazmat.bindings.openssl",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# POST-ANALYSIS: Aggressively filter out any OpenSSL binaries that made it through
a.binaries = [
    (name, path, typecode)
    for name, path, typecode in a.binaries
    if not any(
        excluded in name.lower() or excluded in Path(path).name.lower()
        for excluded in ["libssl", "libcrypto", "openssl", "ssleay", "libeay"]
    )
]

# Also filter datas to remove any OpenSSL TLS plugin that isn't Schannel
a.datas = [
    (dest, source, typecode)
    for dest, source, typecode in a.datas
    if not (
        "tls" in dest.lower()
        and "openssl" in Path(source).name.lower()
    )
]

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="wogger-pro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=["PySide6/Qt/plugins/tls/*"],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(resources_dir / "wogger.ico"),
)
