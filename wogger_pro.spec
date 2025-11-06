# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for Wogger Pro."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all  # type: ignore[import]

project_root = Path(sys.argv[0]).resolve().parent
src_root = project_root / "src"
entry_point = src_root / "wogger_pro" / "__main__.py"
resources_dir = project_root / "resources"


def _collect_openssl_binaries() -> list[tuple[str, str]]:
    binaries: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Prefer an explicit list provided by the build pipeline
    dll_list = os.environ.get("OPENSSL_DLL_LIST", "").strip()
    if dll_list:
        for line in dll_list.splitlines():
            candidate = Path(line).expanduser().resolve()
            if not candidate.exists() or candidate.suffix.lower() != ".dll":
                continue
            key = candidate.name.lower()
            if key in seen:
                continue
            seen.add(key)
            binaries.append((str(candidate), "."))

    # Fallback search across common locations on GitHub runners.
    if not binaries:
        patterns = ("libssl-3*.dll", "libcrypto-3*.dll")
        search_roots: set[Path] = {
            Path(sys.base_prefix) / "DLLs",
            Path(sys.exec_prefix) / "DLLs",
        }

        python_location = os.environ.get("PythonLocation")
        if python_location:
            base = Path(python_location)
            search_roots.update({
                base,
                base / "DLLs",
                base / "bin",
            })

        for root in list(search_roots):
            if not root.exists():
                continue
            for pattern in patterns:
                for dll in root.rglob(pattern):
                    resolved = dll.resolve()
                    key = resolved.name.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    binaries.append((str(resolved), "."))

    has_ssl = any("libssl-3" in path.lower() for path, _ in binaries)
    has_crypto = any("libcrypto-3" in path.lower() for path, _ in binaries)
    if not (has_ssl and has_crypto):
        raise SystemExit(
            "Unable to locate required OpenSSL runtime libraries. "
            "Set OPENSSL_DLL_LIST or ensure libssl-3* and libcrypto-3* DLLs are discoverable."
        )

    return binaries


qtawesome_datas, qtawesome_binaries, qtawesome_hiddenimports = collect_all("qtawesome")

openssl_binaries = _collect_openssl_binaries()

binaries = openssl_binaries + qtawesome_binaries

datas = [(str(resources_dir), "resources")] + qtawesome_datas

hiddenimports = [
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
] + qtawesome_hiddenimports

block_cipher = None


a = Analysis(
    [str(entry_point)],
    pathex=[str(src_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
