"""Shared high-resolution application icon for window, tray and executable."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image


ICON_PNG = Path(__file__).resolve().parent / "assets" / "aurora-icon.png"
ICON_ICO = ICON_PNG.with_suffix(".ico")


@lru_cache(maxsize=8)
def icon_image(size: int = 64) -> Image.Image:
    with Image.open(ICON_PNG) as source:
        return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
