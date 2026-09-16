# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the packaged headless CAN-X runtime (V0.1.1 proof).

Build with: scripts\\build-runtime.cmd
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

_project_root = Path(SPECPATH).parent  # noqa: F821  # SPECPATH is injected by PyInstaller
_runtime_root = _project_root / "runtime"

a = Analysis(
    [str(_runtime_root / "canx" / "__main__.py")],
    pathex=[str(_runtime_root)],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("uvicorn"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="canx-runtime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
