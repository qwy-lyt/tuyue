"""Handle images uploaded directly (screenshots, exported sheet photos)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import ExtractedFile
from .imaging import save_capped_jpeg


def extract(path: Path, file_id: str, workdir: Path) -> ExtractedFile:
    result = ExtractedFile(file_id=file_id, filename=path.name, kind="image")

    try:
        with Image.open(path) as image:
            image.load()
            result.meta["size"] = f"{image.width}×{image.height}"
            result.meta["mode"] = image.mode
            dest = workdir / "image.jpg"
            save_capped_jpeg(image, dest)
            result.images.append(dest)
    except Exception as exc:
        result.warnings.append(f"图片读取失败：{exc}")

    return result
