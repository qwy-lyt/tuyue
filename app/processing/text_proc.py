"""Plain-text sidecars (.txt/.md/.csv) that often accompany a drawing set."""

from __future__ import annotations

from pathlib import Path

from . import ExtractedFile

MAX_TEXT_CHARS = 40_000


def extract(path: Path, file_id: str, workdir: Path) -> ExtractedFile:
    result = ExtractedFile(file_id=file_id, filename=path.name, kind="text")

    text = ""
    for encoding in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
            result.meta["encoding"] = encoding
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n...（内容过长，已截断）"
        result.warnings.append("文本超出长度上限，已截断。")

    result.text = text
    return result
