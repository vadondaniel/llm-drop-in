# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition for LLM Drop-In.

This file is the single source of truth for the Windows distribution build.
Keep packaging choices here rather than duplicating them in shell scripts.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()
APP_NAME = "LLM-Drop-In"
ICON = ROOT / "app.ico"

# The application loads this file at runtime too, so bundle it beside the
# executable in the onedir distribution.
DATAS = [(str(ICON), ".")]

analysis = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)

# onedir is intentional: it starts faster than onefile, keeps the packaged
# application inspectable, and makes support/debugging substantially easier.
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
