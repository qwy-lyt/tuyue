"""End-to-end HTTP check against a running server.

Start the server first, then:

    .venv\\Scripts\\python.exe scripts\\httptest.py

Exercises every endpoint the browser touches, signed in as a temporary account
(see _client.py). Set YUE_TEST_USER and YUE_TEST_PASSWORD to use a real one
instead; otherwise a throwaway account is created and removed again.
"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _client import SignInError, cleanup_temp_user, signed_in_client  # noqa: E402
from selftest import make_dxf  # noqa: E402

BASE = "http://127.0.0.1:8000"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main() -> int:
    try:
        client = signed_in_client(BASE)
    except SignInError as exc:
        print(f"登录失败：{exc}")
        return 1

    try:
        return _run(client)
    finally:
        cleanup_temp_user()


def _run(client: httpx.Client) -> int:
    # closing() rather than `with client:` -- the client is already open, and
    # httpx refuses to be entered twice.
    with closing(client):
        # --- static UI -------------------------------------------------
        page = client.get("/")
        check("首页返回 HTML", page.status_code == 200 and "<title>" in page.text, str(page.status_code))
        check("前端脚本可访问", client.get("/static/app.js").status_code == 200)
        check("样式表可访问", client.get("/static/style.css").status_code == 200)

        # --- status ----------------------------------------------------
        status = client.get("/api/status").json()
        print(f"       模型={status['model']} ODA={status['oda_available']} 注册开放={status['registration_open']}")
        check("status 字段完整", {"model", "oda_available", "supported", "registration_open"} <= set(status))
        if status["oda_available"]:
            check("ODA 已就绪且不显示安装提示", status["oda_hint"] == "", repr(status["oda_hint"]))
        else:
            check("ODA 缺失时给出中文安装提示", "ODA File Converter" in status["oda_hint"],
                  repr(status["oda_hint"][:60]))

        # --- model provider picker -------------------------------------
        catalog = client.get("/api/providers").json()
        ids = [item["id"] for item in catalog]
        check("服务商列表可读取", len(catalog) >= 3, str(ids))
        check("列表里有小米 MiMo", "xiaomi-mimo" in ids, str(ids))

        mimo = next((item for item in catalog if item["id"] == "xiaomi-mimo"), None)
        check("MiMo 的接口地址正确",
              mimo and mimo["base_url"] == "https://api.xiaomimimo.com/v1",
              str(mimo and mimo.get("base_url")))
        check("MiMo 的模型标注了支持看图",
              bool(mimo) and all(model["vision"] for model in mimo["models"]),
              str(mimo and mimo.get("models")))

        # Switching to MiMo should persist and be recognised on the way back.
        client.post("/api/settings", json={
            "base_url": mimo["base_url"], "model": "mimo-v2.5", "api_key": None,
        })
        picked = client.get("/api/settings").json()
        check("选择 MiMo 后能被识别出来", picked["provider"] == "xiaomi-mimo", str(picked))
        check("识别出它支持看图", picked["vision"] is True, str(picked))

        # Anything unrecognised must fall back to the custom entry.
        client.post("/api/settings", json={
            "base_url": "https://example.invalid/v1", "model": "some-model", "api_key": None,
        })
        custom = client.get("/api/settings").json()
        check("未知地址归为自定义", custom["provider"] == "custom", str(custom))
        check("未知模型不谎报支持看图", custom["vision"] is None, str(custom))

        # Back to the default so the rest of the run behaves normally.
        client.post("/api/settings", json={"base_url": "", "model": "", "api_key": ""})

        # --- upload ----------------------------------------------------
        with tempfile.TemporaryDirectory() as tmp:
            dxf_path = Path(tmp) / "测试图纸.dxf"
            make_dxf(dxf_path)
            with dxf_path.open("rb") as handle:
                uploaded = client.post("/api/upload", files={"file": ("测试图纸.dxf", handle, "application/dxf")})
        check("上传 DXF 返回 200", uploaded.status_code == 200, uploaded.text[:200])
        record = uploaded.json()
        file_id = record["file_id"]
        print(f"       解析结果: 文字 {len(record['text'])} 字, 图片 {len(record['images'])} 张, 模式 {record['meta'].get('mode')}")
        check("提取到文字", len(record["text"]) > 0)
        check("渲染出图纸", len(record["images"]) > 0)
        check("中文文件名保留", record["filename"] == "测试图纸.dxf", record["filename"])

        # --- file record + image serving -------------------------------
        fetched = client.get(f"/api/files/{file_id}")
        check("可回读文件记录", fetched.status_code == 200 and fetched.json()["file_id"] == file_id)

        image = client.get(f"/api/files/{file_id}/images/0")
        check("图片可访问且是 JPEG", image.status_code == 200 and image.content[:2] == b"\xff\xd8")
        check("不存在的图片返回 404", client.get(f"/api/files/{file_id}/images/99").status_code == 404)

        # --- rejected upload -------------------------------------------
        bad = client.post("/api/upload", files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")})
        check("不支持的类型被拒绝", bad.status_code == 400, str(bad.status_code))

        # --- chat ------------------------------------------------------
        with client.stream(
            "POST",
            "/api/chat",
            json={"message": "这份图纸里有哪些房间？", "file_ids": [file_id]},
        ) as response:
            check("chat 返回 event-stream", response.headers.get("content-type", "").startswith("text/event-stream"))
            events = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        kinds = [event["type"] for event in events]
        print(f"       事件序列: {kinds}")
        check("先发 start 事件", kinds and kinds[0] == "start")

        # The temporary account deliberately has no API key of its own, so the
        # expected outcome is the guidance message pointing at the settings panel.
        check("未配置 Key 时返回明确错误", "error" in kinds, str(kinds))
        error_text = next((e["text"] for e in events if e["type"] == "error"), "")
        check("错误提示指向设置面板", "设置" in error_text, error_text[:100])

        # --- conversations ---------------------------------------------
        listing = client.get("/api/conversations").json()
        check("对话列表可读取", isinstance(listing, list) and len(listing) >= 1, str(listing)[:120])

        conversation_id = next((e["conversation_id"] for e in events if e["type"] == "start"), None)
        if conversation_id:
            detail = client.get(f"/api/conversations/{conversation_id}")
            check("对话详情可读取", detail.status_code == 200 and len(detail.json()["messages"]) >= 1)
            check("用户消息已落库", detail.json()["messages"][0]["role"] == "user")
            check("删除对话成功", client.delete(f"/api/conversations/{conversation_id}").status_code == 200)
            check("删除后返回 404", client.get(f"/api/conversations/{conversation_id}").status_code == 404)

    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
