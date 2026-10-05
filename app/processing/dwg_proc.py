"""DWG / DXF extraction.

The good path is DWG -> DXF (via ODA File Converter) -> ezdxf, which yields the
real text layer: annotations, dimensions, block attributes, layer names. We also
render the drawing to an image so a vision model can read the geometry.

When the converter is not installed we degrade gracefully: DWG files embed a
preview bitmap in their header, which we dig out and hand over as an image
alongside a clear warning about what was lost.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

from PIL import Image

from ..config import settings
from . import ExtractedFile, oda
from .imaging import save_capped_jpeg

MAX_TEXT_CHARS = 40_000
MAX_TEXT_ITEMS = 400
RENDER_DPI = 150

# Container types worth walking into; anything else we either read directly or skip.
_TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION"}
_MAX_BLOCK_DEPTH = 6

# Unset $EXTMIN/$EXTMAX come back as a 1e20 sentinel rather than being absent.
_EXTENT_SENTINEL = 1e19

# AutoCAD's SHX fonts ('txt', 'simplex', ...) resolve to Arial inside ezdxf, and
# Arial carries no CJK glyphs -- Chinese annotations would render as empty boxes.
# Pointing the text styles at a real Chinese font is what fixes the render.
_CJK_PATTERN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
CJK_FONT_PREFERENCE = ("simhei.ttf", "msyh.ttc", "simsun.ttc", "simkai.ttf", "simfang.ttf")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


def _pick_cjk_font() -> Path | None:
    """Find a Chinese-capable font, honouring an explicit override from .env."""
    font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

    configured = settings.drawing_cjk_font.strip()
    if configured:
        for candidate in (Path(configured), font_dir / configured):
            if candidate.is_file():
                return candidate
        return None

    for name in CJK_FONT_PREFERENCE:
        candidate = font_dir / name
        if candidate.is_file():
            return candidate
    return None


def _apply_cjk_font(document, font: Path) -> None:
    """Repoint every text style at `font` so Chinese annotations stay readable."""
    in_system_fonts = font.parent == Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    # ezdxf resolves bare filenames through its font cache; a file kept elsewhere
    # has to be named by full path instead.
    name = font.name if in_system_fonts else str(font)

    for style in document.styles:
        style.dxf.font = name
        try:
            if style.dxf.hasattr("bigfont"):
                style.dxf.discard("bigfont")  # bigfont is an SHX concept, meaningless for a TTF
        except Exception:
            pass


def _is_real_extent(value) -> bool:
    try:
        return bool(value) and all(abs(float(component)) < _EXTENT_SENTINEL for component in value[:2])
    except (TypeError, ValueError, IndexError):
        return False


# --------------------------------------------------------------------------
# entity walking
# --------------------------------------------------------------------------


def _entity_text(entity) -> str:
    kind = entity.dxftype()
    if kind == "TEXT":
        return str(entity.dxf.get("text", "")).strip()
    if kind == "MTEXT":
        if hasattr(entity, "plain_text"):
            return str(entity.plain_text()).strip()
        return str(entity.text).strip()
    if kind in {"ATTRIB", "ATTDEF"}:
        tag = str(entity.dxf.get("tag", "")).strip()
        value = str(entity.dxf.get("text", "")).strip()
        return f"{tag}={value}" if tag else value
    if kind == "DIMENSION":
        override = str(entity.dxf.get("text", "")).strip()
        if override and override not in {"<>", " "}:
            return override
        try:
            measurement = entity.get_measurement()
            if isinstance(measurement, (int, float)):
                return f"{measurement:g}"
            if measurement:
                return ", ".join(f"{m:g}" for m in measurement if isinstance(m, (int, float)))
        except Exception:
            pass
        return ""
    return ""


def _walk(container, texts: list[tuple[str, str]], inserts: dict[str, int],
          block_names_seen: set[str], depth: int) -> None:
    """Collect readable text and block usage from one layout or block definition."""
    for entity in container:
        kind = entity.dxftype()
        layer = str(entity.dxf.get("layer", "0"))

        if kind in _TEXT_TYPES:
            value = _entity_text(entity)
            if value:
                texts.append((layer, value))
        elif kind == "INSERT":
            name = str(entity.dxf.get("name", "?"))
            inserts[name] = inserts.get(name, 0) + 1

            for attrib in getattr(entity, "attribs", []):
                value = _entity_text(attrib)
                if value:
                    texts.append((layer, value))

            # Block definitions carry the reusable annotation text (titles,
            # legends). Descend once per block to avoid cycles and blow-up.
            if depth < _MAX_BLOCK_DEPTH and name not in block_names_seen:
                block_names_seen.add(name)
                try:
                    block = entity.doc.blocks.get(name)
                except Exception:
                    block = None
                if block is not None:
                    _walk(block, texts, inserts, block_names_seen, depth + 1)


def _build_digest(result: ExtractedFile, doc) -> str:
    lines: list[str] = []
    version = str(doc.header.get("$ACADVER", "")).strip()
    lines.append(f"AutoCAD 版本: {version or '未知'}")

    try:
        ext_min = doc.header.get("$EXTMIN")
        ext_max = doc.header.get("$EXTMAX")
        if _is_real_extent(ext_min) and _is_real_extent(ext_max):
            lines.append(
                "图纸范围: "
                f"({ext_min[0]:.1f}, {ext_min[1]:.1f}) ~ ({ext_max[0]:.1f}, {ext_max[1]:.1f})"
            )
    except Exception:
        pass

    layer_names = [layer.dxf.name for layer in doc.layers]
    lines.append(f"图层（{len(layer_names)} 个）: " + ", ".join(layer_names[:120]))

    texts: list[tuple[str, str]] = []
    inserts: dict[str, int] = {}
    for layout in doc.layouts:
        _walk(layout, texts, inserts, set(), 0)

    result.meta["text_entities"] = len(texts)
    result.meta["layers"] = len(layer_names)
    result.meta["blocks"] = len(inserts)
    # Only the drawing's own text matters here -- our own digest labels are
    # Chinese too, so testing the assembled string would always be true.
    result.meta["cjk_text"] = any(_has_cjk(value) for _, value in texts)

    if texts:
        lines.append(f"\n文字内容（{len(texts)} 条）:")
        for layer, value in texts[:MAX_TEXT_ITEMS]:
            lines.append(f"  [{layer}] {value}")
        if len(texts) > MAX_TEXT_ITEMS:
            lines.append(f"  ...（另有 {len(texts) - MAX_TEXT_ITEMS} 条未列出）")
    else:
        lines.append("\n文字内容: 图纸中没有可提取的文字标注。")

    if inserts:
        lines.append(f"\n块引用（{len(inserts)} 种）:")
        for name, count in sorted(inserts.items(), key=lambda kv: -kv[1])[:80]:
            lines.append(f"  {name} ×{count}")

    digest = "\n".join(lines)
    if len(digest) > MAX_TEXT_CHARS:
        digest = digest[:MAX_TEXT_CHARS] + "\n...（内容过长，已截断）"
        result.warnings.append("图纸文字过多，已截断，部分标注未送出。")
    return digest


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _is_blank(image_path: Path) -> bool:
    """A uniform page means nothing was drawn -- white lines on a white canvas."""
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            low, high = image.convert("L").getextrema()
        return low == high
    except Exception:
        return False


def _render_dxf(dxf_path: Path, dest: Path, use_cjk_font: bool = False) -> tuple[bool, str]:
    """Render modelspace to a JPEG. Returns (ok, error_message).

    With `use_cjk_font`, the drawing's text styles are forced onto a Chinese
    font; without it they are left exactly as the drawing specifies them.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import ezdxf
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    except Exception as exc:  # pragma: no cover - depends on the install
        return False, f"渲染组件不可用：{exc}"

    # The default background policy follows the drawing's own background, which
    # is usually black -- and then dark entities render as light lines that are
    # invisible once we put them on a white page. Pin both sides explicitly.
    configuration = Configuration(
        background_policy=BackgroundPolicy.WHITE,
        color_policy=ColorPolicy.COLOR,
    )

    def draw(container, figure) -> None:
        axis = figure.add_axes([0, 0, 1, 1])
        backend = MatplotlibBackend(axis)
        Frontend(RenderContext(document), backend, config=configuration).draw_layout(
            container, finalize=True
        )

    figure = None
    document = None
    try:
        document = ezdxf.readfile(dxf_path)

        if use_cjk_font:
            font_path = _pick_cjk_font()
            if font_path is not None:
                _apply_cjk_font(document, font_path)

        figure = plt.figure(figsize=(16, 11), dpi=RENDER_DPI)
        draw(document.modelspace(), figure)
        figure.savefig(dest, dpi=RENDER_DPI, facecolor="#FFFFFF")

        if not _is_blank(dest):
            return True, ""

        # Empty modelspace is common when a drawing lives entirely in a layout.
        plt.close(figure)
        for layout in document.layouts:
            if layout.name.lower() == "model" or not len(layout):
                continue
            figure = plt.figure(figsize=(16, 11), dpi=RENDER_DPI)
            draw(layout, figure)
            figure.savefig(dest, dpi=RENDER_DPI, facecolor="#FFFFFF")
            if not _is_blank(dest):
                return True, ""

        return False, "图纸渲染出来是空白的，可能图形都在未支持的布局或外部参照里。"
    except Exception as exc:
        return False, f"图纸渲染失败：{exc}"
    finally:
        if figure is not None:
            plt.close(figure)


# --------------------------------------------------------------------------
# DWG embedded preview (fallback when the converter is missing)
# --------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8\xff"
_JPEG_EOI = b"\xff\xd9"


def _try_png(data: bytes, start: int) -> bytes | None:
    end = data.find(b"IEND", start)
    return data[start : end + 8] if end != -1 else None


def _try_jpeg(data: bytes, start: int) -> bytes | None:
    end = data.find(_JPEG_EOI, start + 3)
    return data[start : end + 2] if end != -1 else None


def _try_bmp(data: bytes, start: int) -> bytes | None:
    if start + 6 > len(data):
        return None
    size = int.from_bytes(data[start + 2 : start + 6], "little")
    if 0 < size <= len(data) - start:
        return data[start : start + size]
    return None


def extract_preview(dwg_path: Path, dest: Path) -> bool:
    """Pull the thumbnail DWG files embed in their header, if there is one."""
    try:
        data = dwg_path.read_bytes()
    except OSError:
        return False

    candidates: list[bytes] = []
    for extractor, signature in ((_try_png, _PNG_SIG), (_try_jpeg, _JPEG_SOI), (_try_bmp, b"BM")):
        offset = 0
        while True:
            offset = data.find(signature, offset)
            if offset == -1:
                break
            chunk = extractor(data, offset)
            if chunk and len(chunk) > 2048:
                candidates.append(chunk)
            offset += len(signature)

    for chunk in sorted(candidates, key=len, reverse=True):
        try:
            with Image.open(io.BytesIO(chunk)) as image:
                image.load()
                if min(image.size) < 80:
                    continue
                save_capped_jpeg(image, dest)
                return True
        except Exception:
            continue
    return False


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def extract(path: Path, file_id: str, workdir: Path) -> ExtractedFile:
    result = ExtractedFile(file_id=file_id, filename=path.name, kind="dwg")
    dxf_path: Path | None = None

    if path.suffix.lower() == ".dxf":
        dxf_path = path
    elif oda.is_available():
        try:
            # Keep the converted DXF beside this upload's other artifacts rather
            # than in a temp file, so it stays around for inspection.
            dxf_path = oda.convert(path, out_dir=workdir)
        except Exception as exc:
            result.warnings.append(str(exc))
    else:
        result.warnings.append(oda.install_hint())

    if dxf_path is not None:
        try:
            _extract_from_dxf(dxf_path, result, workdir)
            return result
        except Exception as exc:
            result.warnings.append(f"DXF 解析失败：{exc}")

    # Degraded path: whatever the DWG itself carries.
    preview = workdir / "preview.jpg"
    if extract_preview(path, preview):
        result.images.append(preview)
        result.meta["mode"] = "preview"
        result.warnings.append(
            "当前只能看到 DWG 内嵌的预览缩略图，分辨率有限，读不到精确的文字标注。"
            "装上 ODA File Converter 后可获得完整的文字与图层信息。"
        )
    else:
        result.meta["mode"] = "none"
        result.warnings.append("无法解析该 DWG 文件，也没有找到内嵌预览图。")

    return result


def _extract_from_dxf(dxf_path: Path, result: ExtractedFile, workdir: Path) -> None:
    import ezdxf

    document = ezdxf.readfile(dxf_path)
    result.text = _build_digest(result, document)
    result.meta["mode"] = "dxf"

    needs_cjk = bool(result.meta.get("cjk_text"))
    if needs_cjk:
        font = _pick_cjk_font()
        if font is not None:
            result.meta["cjk_font"] = font.name
        else:
            result.warnings.append(
                "图纸里有中文标注，但系统里没找到可用的中文字体，"
                "渲染出的图片里中文会显示成方框（文字提取不受影响）。"
                "可在 .env 里用 DRAWING_CJK_FONT 指定字体文件。"
            )

    rendered = workdir / "drawing.jpg"
    ok, error = _render_dxf(dxf_path, rendered, use_cjk_font=needs_cjk)
    if ok:
        result.images.append(rendered)
    else:
        result.warnings.append(error)
