"""Check the file library: listing, and above all the delete path.

    .venv\\Scripts\\python.exe scripts\\filetest.py

Uploads a small generated drawing, stores a reading for it, then removes it and
verifies that everything derived from it went too -- the uploaded bytes, the
parse cache, and the saved reading that had this file in its set. Also checks
that another account can neither read nor delete it.

No model call is made anywhere in this script, so it costs nothing to run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _client import (  # noqa: E402
    BASE,
    TEMP_USER,
    SignInError,
    cleanup_temp_user,
    signed_in_client,
    test_invite_code,
)

from app import accounts, store  # noqa: E402

SECOND_NAME = "filetest_other"
SECOND_PASSWORD = "probe-password-2026"
FILENAME = "删除测试.pdf"

failures: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if passed else 'FAIL'}] {label}")
    if not passed:
        failures.append(label)
        if detail:
            print(f"        {detail}")


def make_pdf() -> bytes:
    """A one-page PDF built in memory, so the test needs no sample on disk."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 96), "FILE LIBRARY TEST")
    payload = document.tobytes()
    document.close()
    return payload


def other_account() -> httpx.Client:
    """A second signed-in account, to prove one user's file is out of reach."""
    accounts.init_db()
    existing = accounts.get_user_by_name(SECOND_NAME)
    if existing:
        store.remove_user_data(existing.id)
        accounts.delete_user(existing.id)

    client = httpx.Client(base_url=BASE, timeout=60.0)
    response = client.post(
        "/api/auth/register",
        json={
            "username": SECOND_NAME,
            "password": SECOND_PASSWORD,
            "invite_code": test_invite_code(),
        },
    )
    if response.status_code != 200:
        raise SignInError(f"创建第二个账号失败：{response.text[:200]}")
    return client


def drop_second_account() -> None:
    user = accounts.get_user_by_name(SECOND_NAME)
    if user:
        store.remove_user_data(user.id)
        accounts.delete_user(user.id)


def _run(client: httpx.Client) -> int:
    user = accounts.get_user_by_name(TEMP_USER)
    if user is None:
        print("找不到临时测试账号，无法检查落盘情况")
        return 1

    # --- upload ---------------------------------------------------------
    uploaded = client.post(
        "/api/upload", files={"file": (FILENAME, make_pdf(), "application/pdf")}
    )
    check("上传返回 200", uploaded.status_code == 200, uploaded.text[:200])
    if uploaded.status_code != 200:
        return 1
    file_id = uploaded.json()["file_id"]

    listing = client.get("/api/files").json()
    check("新文件出现在文件库", any(row["file_id"] == file_id for row in listing))

    upload_dir = store.uploads_dir(user.id) / file_id
    cache_dir = store.file_dir(user.id, file_id)
    check("原件已落盘", upload_dir.is_dir())
    check("解析缓存已落盘", (cache_dir / "meta.json").is_file())

    # A reading written straight to disk: this test is about deleting one, and
    # asking the model for a real one would cost a call that proves nothing here.
    store.save_extraction(
        user.id, [file_id], {"items": [{"name": "占位", "value": 1}], "notices": []}
    )
    stored = store.extractions_dir(user.id) / f"{store.extraction_key([file_id])}.json"
    check("已存下一份识别结果", stored.is_file())
    check(
        "接口能读回这份结果",
        bool(client.get(f"/api/extract?file_ids={file_id}").json().get("items")),
    )

    # --- malformed ids --------------------------------------------------
    malformed = client.delete("/api/files/not-an-id")
    check("畸形 id 返回 404 而不是崩", malformed.status_code == 404, str(malformed.status_code))
    check(
        "带路径的 id 不被接受",
        client.delete("/api/files/..%2f..%2faccounts").status_code in (400, 404),
    )

    # --- another account -------------------------------------------------
    other = other_account()
    try:
        check("别的账号读不到这个文件", other.get(f"/api/files/{file_id}").status_code == 404)
        check("别的账号删不掉这个文件", other.delete(f"/api/files/{file_id}").status_code == 404)
        check("被拒之后原件仍在", upload_dir.is_dir())
    finally:
        other.close()

    # --- delete ---------------------------------------------------------
    removed = client.delete(f"/api/files/{file_id}")
    check("删除返回 200", removed.status_code == 200, removed.text[:200])
    check(
        "返回值带上原文件名",
        removed.status_code == 200 and removed.json().get("filename") == FILENAME,
        removed.text[:200],
    )

    check(
        "文件库不再列出它",
        all(row["file_id"] != file_id for row in client.get("/api/files").json()),
    )
    check("记录返回 404", client.get(f"/api/files/{file_id}").status_code == 404)
    check("图片返回 404", client.get(f"/api/files/{file_id}/images/0").status_code == 404)
    check("原件已从磁盘删除", not upload_dir.exists())
    check("解析缓存已从磁盘删除", not cache_dir.exists())
    check("引用它的识别结果一并删除", not stored.is_file())
    check("再删一次返回 404", client.delete(f"/api/files/{file_id}").status_code == 404)
    check(
        "删除动作进了审计日志",
        any(entry["action"] == "delete_file" for entry in accounts.recent_audit(10)),
    )
    return 0


def main() -> int:
    try:
        client = signed_in_client()
    except SignInError as exc:
        print(exc)
        return 1

    try:
        return _run(client)
    finally:
        client.close()
        drop_second_account()
        cleanup_temp_user()
        print()
        if failures:
            print(f"失败 {len(failures)} 项：")
            for name in failures:
                print(f"  - {name}")
        else:
            print("全部通过")


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 and not failures else 1)
