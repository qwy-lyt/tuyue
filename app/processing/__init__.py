"""File processing pipeline: turn an uploaded PDF or DWG into text + images the model can read."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractedFile:
    """Everything we managed to learn about one uploaded file."""

    file_id: str
    filename: str
    kind: str
    text: str = ""
    images: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def has_content(self) -> bool:
        return bool(self.text.strip()) or bool(self.images)


class UnsupportedFile(Exception):
    """Raised when we have no handler for the uploaded file type."""


SUPPORTED_SUFFIXES = {".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".md", ".csv"}


def dispatch(path: Path, file_id: str, workdir: Path) -> ExtractedFile:
    """Route one file to the right extractor."""
    from . import dwg_proc, image_proc, pdf_proc, text_proc

    suffix = path.suffix.lower()
    workdir.mkdir(parents=True, exist_ok=True)

    if suffix == ".pdf":
        return pdf_proc.extract(path, file_id, workdir)
    if suffix in {".dwg", ".dxf"}:
        return dwg_proc.extract(path, file_id, workdir)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return image_proc.extract(path, file_id, workdir)
    if suffix in {".txt", ".md", ".csv"}:
        return text_proc.extract(path, file_id, workdir)

    raise UnsupportedFile(f"不支持的文件类型：{suffix or '(无扩展名)'}")
