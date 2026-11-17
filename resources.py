"""Locate bundled files both in the source tree and in a PyInstaller build."""

from __future__ import annotations

from pathlib import Path
import sys


def resource_root() -> Path:
    """Return the directory that contains bundled assets and vendor runtimes."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)
