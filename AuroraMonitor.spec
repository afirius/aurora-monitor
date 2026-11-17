# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: one self-contained AuroraMonitor.exe with no console."""

from pathlib import Path
import os

ROOT = Path(os.path.abspath(SPECPATH))
LHM = ROOT / "vendor" / "lhm"


def lhm_runtime_files():
    """Bundle the managed LibreHardwareMonitor runtime, not its GUI or symbols."""
    files = []
    for path in LHM.iterdir():
        if path.is_file() and path.suffix.lower() in {".dll", ".config", ".xml"}:
            files.append((str(path), "vendor/lhm"))
        elif path.is_dir() and path.name in {
                "de", "es", "fr", "it", "ja", "pl", "ru", "sv", "tr",
                "zh-CN", "zh-Hant"}:
            files.append((str(path), f"vendor/lhm/{path.name}"))
    return files


datas = [
    (str(ROOT / "assets"), "assets"),
    *lhm_runtime_files(),
    (str(ROOT / "vendor" / "LibreHardwareMonitor-LICENSE.txt"), "vendor"),
    (str(ROOT / "vendor" / "PawnIO-LICENSE.txt"), "vendor"),
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "pystray._win32",
        "winrt.windows.media.control",
        "winrt.windows.storage.streams",
        "clr",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["unittest", "test", "tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AuroraMonitor",
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
    icon=str(ROOT / "assets" / "aurora-icon.ico"),
    version=str(ROOT / "version_info.txt"),
)
