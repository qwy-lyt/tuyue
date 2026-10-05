"""Shared image helpers.

Every image that reaches the model is inlined as base64, so its size directly
counts against the API's 48 MiB request-body limit. We normalise everything to
bounded JPEGs rather than shipping whatever the renderer produced.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..config import settings


def save_capped_jpeg(image: Image.Image, dest: Path) -> Path:
    """Downscale to `image_max_px` on the long edge and write a JPEG."""
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    long_edge = max(image.size)
    if long_edge > settings.image_max_px:
        scale = settings.image_max_px / long_edge
        new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(new_size, Image.LANCZOS)

    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="JPEG", quality=settings.image_jpeg_quality, optimize=True)
    return dest


def estimate_data_url_bytes(paths: list[Path]) -> int:
    """Base64 inflates by 4/3; this is what the request body will actually weigh."""
    raw = sum(p.stat().st_size for p in paths if p.exists())
    return raw * 4 // 3
