"""Live end-to-end test: upload real files, ask real questions, check the answers.

Unlike httptest.py, this spends actual API credits. It is the only test that
proves the model actually reads what we extract -- it plants known facts in each
file and then checks whether those facts come back in the answer.

    .venv\\Scripts\\python.exe scripts\\livetest.py
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import closing
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _client import SignInError, cleanup_temp_user, signed_in_client  # noqa: E402

BASE = "http://127.0.0.1:8000"
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

failures: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if passed else 'FAIL'}] {label}")
    if not passed:
        failures.append(label)
        if detail:
            print(f"         {detail}")


def load_font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_spec_pdf(path: Path) -> None:
    """A text-layer PDF carrying three facts only obtainable by reading it."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    lines = [
        "A 栋结构设计说明",
        "三层层高为 4.2 米，外墙厚度 300 毫米。",
        "混凝土设计强度等级为 C35。",
    ]
    for index, line in enumerate(lines):
        page.insert_text((72, 100 + index * 30), line, fontsize=14, fontname="china-s")
    doc.save(path)
    doc.close()


def make_scan_pdf(path: Path) -> None:
    """An image-only PDF: no text layer, so only a vision model can read it."""
    import pymupdf
    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([20, 20, 1180, 380], outline="black", width=4)
    draw.text((60, 80), "设备编号：SB-2024-118", fill="black", font=load_font(48))
    draw.text((60, 200), "安装位置：地下一层泵房", fill="black", font=load_font(48))

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        canvas.save(handle.name)
        image_path = handle.name

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(40, 60, 555, 230), filename=image_path)
    doc.save(path)
    doc.close()
    Path(image_path).unlink(missing_ok=True)


def make_drawing_dxf(path: Path) -> None:
    """A DXF whose annotations exist nowhere except in the geometry and text."""
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.layers.add("墙体")
    doc.layers.add("标注")
    msp = doc.modelspace()

    msp.add_lwpolyline(
        [(0, 0), (8400, 0), (8400, 6000), (0, 6000), (0, 0)], dxfattribs={"layer": "墙体"}
    )
    msp.add_line((4200, 0), (4200, 6000), dxfattribs={"layer": "墙体"})
    msp.add_text("会议室 A", dxfattribs={"layer": "标注", "height": 260}).set_placement((1200, 3000))
    msp.add_text("设备间 B", dxfattribs={"layer": "标注", "height": 260}).set_placement((5200, 3000))
    msp.add_text("耐火等级 二级", dxfattribs={"layer": "标注", "height": 220}).set_placement((1200, 1000))

    doc.saveas(path)


def make_real_dwg(dxf_path: Path, target_dir: Path) -> Path:
    """Produce a genuine DWG by asking the ODA converter to write one.

    Sample DWG files are awkward to ship, but the converter works both ways --
    so a DXF we control becomes a real AutoCAD binary we can feed back through
    the upload path.
    """
    from app.processing import oda

    if not oda.is_available():
        raise RuntimeError("未安装 ODA File Converter")

    return oda.convert(dxf_path, ".dwg", out_dir=target_dir)


def upload(client: httpx.Client, path: Path) -> dict:
    with path.open("rb") as handle:
        response = client.post(
            "/api/upload", files={"file": (path.name, handle, "application/octet-stream")}
        )
    response.raise_for_status()
    return response.json()


def ask(client: httpx.Client, question: str, file_id: str) -> str:
    """Send one question and reassemble the streamed answer."""
    answer: list[str] = []
    errors: list[str] = []
    with client.stream("POST", "/api/chat", json={"message": question, "file_ids": [file_id]}) as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            import json

            event = json.loads(line[6:])
            if event["type"] == "delta":
                answer.append(event["text"])
            elif event["type"] == "error":
                errors.append(event["text"])
    if errors:
        raise RuntimeError("；".join(errors))
    return "".join(answer)


def ensure_api_key(client: httpx.Client) -> bool:
    """Make sure the signed-in account has a key; borrow .env's if it has none.

    This exists so the script works out of the box on a fresh install. It is the
    operator's own key from .env, used only for this test.
    """
    from app.config import settings

    if client.get("/api/settings").json()["has_key"]:
        return True
    if not settings.api_key:
        return False

    client.post(
        "/api/settings",
        json={"base_url": "", "model": "", "api_key": settings.api_key},
    )
    print("（这个测试账号没有 Key，临时借用了 .env 里的那把）\n")
    return True


def main() -> int:
    try:
        client = signed_in_client(BASE)
    except SignInError as exc:
        print(f"登录失败：{exc}")
        return 1

    try:
        return _run(client)
    finally:
        client.close()
        cleanup_temp_user()


def _run(client: httpx.Client) -> int:
    # closing() rather than `with client:` -- the client is already open, and
    # httpx refuses to be entered twice.
    with closing(client):
        status = client.get("/api/status").json()
        if not ensure_api_key(client):
            print("账号没有 API Key，.env 里也没有可借用的。")
            print("请在网页的「设置」里填一个，或用 YUE_TEST_USER / YUE_TEST_PASSWORD 指定一个有 Key 的账号。")
            return 1
        print(f"模型：{status['model']}\n")

        with tempfile.TemporaryDirectory(prefix="live_") as tmp:
            root = Path(tmp)

            # --- 1. text PDF -------------------------------------------------
            print("1) 文字型 PDF —— 考验文字提取链路")
            pdf = root / "结构说明.pdf"
            make_spec_pdf(pdf)
            record = upload(client, pdf)
            print(f"   提取 {len(record['text'])} 字, {len(record['images'])} 张图")
            answer = ask(client, "这份文档里，三层层高和外墙厚度分别是多少？", record["file_id"])
            print(f"   模型回答：{answer[:200]}")
            check("答出了层高 4.2 米", "4.2" in answer, answer[:200])
            check("答出了墙厚 300 毫米", "300" in answer, answer[:200])

            # --- 2. scanned PDF ----------------------------------------------
            print("\n2) 扫描型 PDF —— 考验视觉识图链路")
            scan = root / "设备标签.pdf"
            make_scan_pdf(scan)
            record = upload(client, scan)
            print(f"   提取 {len(record['text'])} 字, {len(record['images'])} 张图")
            check("确认没有文字层（靠看图）", len(record["text"]) == 0, record["text"][:120])
            answer = ask(client, "图里的设备编号和安装位置分别是什么？", record["file_id"])
            print(f"   模型回答：{answer[:200]}")
            check("读出了设备编号", "SB-2024-118" in answer.replace(" ", ""), answer[:200])
            check("读出了安装位置", "泵房" in answer, answer[:200])

            # --- 3. DXF ------------------------------------------------------
            print("\n3) DXF 图纸 —— 考验图纸解析 + 渲染链路")
            dxf = root / "平面图.dxf"
            make_drawing_dxf(dxf)
            record = upload(client, dxf)
            print(f"   提取 {len(record['text'])} 字, {len(record['images'])} 张图, 模式 {record['meta'].get('mode')}")
            answer = ask(client, "这张图纸里有几个房间？分别叫什么名字？", record["file_id"])
            print(f"   模型回答：{answer[:250]}")
            check("答出了会议室 A", "会议室" in answer, answer[:250])
            check("答出了设备间 B", "设备间" in answer, answer[:250])

            # --- 4. real DWG -------------------------------------------------
            print("\n4) 原生 DWG —— 考验 ODA 转换链路")
            if status["oda_available"]:
                try:
                    dwg = make_real_dwg(dxf, root)
                except Exception as exc:
                    check("生成测试用 DWG", False, str(exc))
                else:
                    with dwg.open("rb") as handle:
                        magic = handle.read(6)
                    print(f"   生成 DWG {dwg.stat().st_size} 字节, 文件头 {magic.decode('ascii', 'replace')}")
                    check("确实是 AutoCAD 二进制", magic.startswith(b"AC10"), repr(magic))

                    record = upload(client, dwg)
                    print(f"   提取 {len(record['text'])} 字, {len(record['images'])} 张图, 模式 {record['meta'].get('mode')}")
                    check("走的是完整解析而非降级预览", record["meta"].get("mode") == "dxf",
                          str(record["meta"]))
                    check("没有 ODA 相关警告",
                          not any("ODA" in w for w in record["warnings"]),
                          str(record["warnings"]))

                    answer = ask(client, "这张图纸里有几个房间？分别叫什么名字？", record["file_id"])
                    print(f"   模型回答：{answer[:250]}")
                    check("答出了会议室 A", "会议室" in answer, answer[:250])
                    check("答出了设备间 B", "设备间" in answer, answer[:250])
            else:
                print("   跳过：未安装 ODA File Converter，DWG 无法完整解析")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("全部通过 —— 所有链路都能让模型真正读懂文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
