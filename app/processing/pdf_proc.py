"""PDF extraction.

Two very different documents arrive as PDF: text documents (specs, contracts)
and CAD exports where the content lives in the drawing, not in a text layer.
We extract the text layer, then decide page by page whether that text is enough
to answer from -- rendering costs request payload, so we only pay where the text
alone obviously cannot answer.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image

from ..config import settings
from . import ExtractedFile
from .imaging import save_capped_jpeg

MAX_TEXT_CHARS = 40_000
RENDER_DPI = 140

# A page below this many characters has no usable text layer at all.
MIN_TEXT_CHARS = 25
# CAD sheets carry sparse annotation on top of thousands of vector paths.
VECTOR_PATH_THRESHOLD = 150
# Scanned pages with a thin OCR layer still need to be seen, not read.
OCR_TEXT_CEILING = 200


def _needs_rendering(page: pymupdf.Page, text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_CHARS:
        return True

    try:
        if len(page.get_drawings()) > VECTOR_PATH_THRESHOLD:
            return True
    except Exception:
        pass

    if len(stripped) < OCR_TEXT_CEILING and page.get_images():
        return True
    return False


def _render_page(page: pymupdf.Page) -> Image.Image:
    pixmap = page.get_pixmap(dpi=RENDER_DPI, colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def extract(path: Path, file_id: str, workdir: Path) -> ExtractedFile:
    result = ExtractedFile(file_id=file_id, filename=path.name, kind="pdf")

    with pymupdf.open(path) as doc:
        page_count = doc.page_count
        result.meta["pages"] = page_count

        page_texts = [page.get_text("text").strip() for page in doc]
        total_chars = sum(len(t) for t in page_texts)
        result.meta["text_chars"] = total_chars

        render_targets = [
            index
            for index, (page, text) in enumerate(zip(doc, page_texts))
            if _needs_rendering(page, text)
        ]
        result.meta["rendered_pages"] = len(render_targets)

        budget = settings.max_images_per_turn
        if len(render_targets) > budget:
            result.warnings.append(
                f"有 {len(render_targets)} 页需要看图，为控制体积只渲染了前 {budget} 页"
                f"（第 {'、'.join(str(i + 1) for i in render_targets[:budget])} 页）。"
                "如需查看后面的页面，请单独上传那几页。"
            )

        for index in render_targets[:budget]:
            image = _render_page(doc[index])
            dest = workdir / f"page_{index + 1:03d}.jpg"
            result.images.append(save_capped_jpeg(image, dest))

        if total_chars:
            blocks = [
                f"===== 第 {index} 页 =====\n{text}"
                for index, text in enumerate(page_texts, start=1)
                if text
            ]
            digest = "\n\n".join(blocks)
            if len(digest) > MAX_TEXT_CHARS:
                digest = digest[:MAX_TEXT_CHARS] + "\n\n...（文本过长，已截断）"
                result.warnings.append("文档文本超出长度上限，已截断，部分内容未送出。")
            result.text = digest
        elif not render_targets:
            result.warnings.append("这份 PDF 既没有可提取的文字，也无法渲染页面。")

    return result
