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

datas = [(str(resources_dir), "resources")] + qtawesome_datas

hiddenimports = [
    "PySide6.QtNetwork",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
] + qtawesome_hiddenimports

block_cipher = None

plugins_dir = Path(PySide6.__file__).parent / "Qt" / "plugins"
tls_dir = plugins_dir / "tls"
if tls_dir.exists():
    datas.append((str(tls_dir), "tls"))


a = Analysis(
    [str(entry_point)],
    pathex=[str(src_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "pyi_force_schannel.py")],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(resources_dir / "wogger.ico"),
)
