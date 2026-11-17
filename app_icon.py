"""Shared high-resolution application icon for window, tray and executable."""

from __future__ import annotations

from functools import lru_cache

from PIL import Image

from resources import resource_path


ICON_PNG = resource_path("assets", "aurora-icon.png")
ICON_ICO = ICON_PNG.with_suffix(".ico")


@lru_cache(maxsize=8)
def icon_image(size: int = 64) -> Image.Image:
    with Image.open(ICON_PNG) as source:
        return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
