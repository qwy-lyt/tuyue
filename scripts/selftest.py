"""Smoke test: build sample files in memory and push them through the extractors.

Run it after changing anything in app/processing/ -- it needs no network and no
API key, so it is the fast way to tell whether the parsing pipeline still works.

    .venv\\Scripts\\python.exe scripts\\selftest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.processing import dispatch  # noqa: E402


def make_dxf(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.layers.add("墙体")
    doc.layers.add("标注")
    msp = doc.modelspace()

    msp.add_lwpolyline(
        [(0, 0), (5000, 0), (5000, 3000), (0, 3000), (0, 0)],
        dxfattribs={"layer": "墙体"},
    )
    msp.add_text("混凝土强度 C30", dxfattribs={"layer": "标注", "height": 200}).set_placement((500, 500))
    msp.add_mtext("标高 ±0.000", dxfattribs={"layer": "标注"}).set_location((500, 900))

    dim = msp.add_linear_dim(base=(0, -400), p1=(0, 0), p2=(5000, 0), dxfattribs={"layer": "标注"})
    dim.render()

    block = doc.blocks.new("门-900")
    block.add_line((0, 0), (900, 0))
    msp.add_blockref("门-900", (1000, 1000))
    msp.add_blockref("门-900", (3000, 1000))

    doc.saveas(path)


def make_text_pdf(path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "施工合同", fontsize=20, fontname="china-s")
    page.insert_text((72, 150), "第三条 付款方式：合同签订后 7 日内支付 30% 预付款。", fontsize=12, fontname="china-s")
    page.insert_text((72, 180), "剩余款项于验收合格后 30 日内结清。", fontsize=12, fontname="china-s")
    doc.save(path)
    doc.close()


def make_scanned_pdf(path: Path) -> None:
    """A page with no text layer at all -- forces the render-as-image path."""
    import pymupdf
    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (1000, 700), "white")
    ImageDraw.Draw(canvas).rectangle([40, 40, 960, 660], outline="black", width=3)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        canvas.save(handle.name)
        image_path = handle.name

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(50, 50, 545, 400), filename=image_path)
    doc.save(path)
    doc.close()
    Path(image_path).unlink(missing_ok=True)


def report(label: str, extracted) -> bool:
    ok = extracted.has_content
    print(f"\n=== {label} ===")
    print(f"  类型        : {extracted.kind}")
    print(f"  文字长度    : {len(extracted.text)}")
    print(f"  生成图片    : {len(extracted.images)} 张")
    print(f"  元数据      : {extracted.meta}")
    for warning in extracted.warnings:
        print(f"  ! 提示      : {warning}")
    if extracted.text:
        preview = extracted.text[:220].replace("\n", "\n                ")
        print(f"  文字预览    :\n                {preview}")
    print(f"  结果        : {'通过' if ok else '失败（没有提取到任何内容）'}")
    return ok


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="selftest_") as tmp:
        root = Path(tmp)
        results = []

        dxf_path = root / "sample.dxf"
        make_dxf(dxf_path)
        results.append(report("DXF 图纸", dispatch(dxf_path, "t_dxf", root / "w_dxf")))

        text_pdf = root / "contract.pdf"
        make_text_pdf(text_pdf)
        results.append(report("文字型 PDF", dispatch(text_pdf, "t_pdf", root / "w_pdf")))

        scanned_pdf = root / "scanned.pdf"
        make_scanned_pdf(scanned_pdf)
        results.append(report("扫描型 PDF", dispatch(scanned_pdf, "t_scan", root / "w_scan")))

    passed = sum(results)
    print(f"\n{'=' * 46}\n通过 {passed}/{len(results)} 项")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
