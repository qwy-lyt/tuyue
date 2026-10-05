"""Shared helper: an HTTP client that is already signed in.

The chat API now requires an account, so every test script needs one. Rather
than hard-code credentials, this looks for YUE_TEST_USER / YUE_TEST_PASSWORD in
the environment and otherwise registers a throwaway account with the bootstrap
invite code and deletes it again on cleanup.

The temporary account is reported if it happens to become the first user, since
the first registrant is the administrator -- a test must never quietly take that
role away from the person who is supposed to own the instance.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import accounts, store  # noqa: E402
from app.config import settings  # noqa: E402

BASE = "http://127.0.0.1:8000"
TEMP_USER = "selftest_temp"
TEMP_PASSWORD = "selftest-password-2026"
TEST_INVITE_NOTE = "自动化测试专用（可随时删除）"


class SignInError(RuntimeError):
    pass


def test_invite_code() -> str:
    """An invite code reserved for the test scripts.

    Registering through the API would otherwise spend a use of the operator's
    own invite code, and a few regression runs would quietly exhaust it. So the
    scripts mint their own, marked so it is obvious where it came from.
    """
    accounts.init_db()
    for invite in accounts.list_invites():
        if invite["note"] == TEST_INVITE_NOTE and invite["uses"] < invite["max_uses"]:
            return invite["code"]
    return accounts.create_invite(TEST_INVITE_NOTE, "scripts", max_uses=10000)


def _sign_in(client: httpx.Client, username: str, password: str) -> None:
    """Log in with a password.

    On an instance with a mail server configured this cannot complete on its
    own -- the second factor arrives by email. Use smtptest.py for that, or
    point these scripts at an instance with email codes switched off.
    """
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password, "code": ""}
    )

    if response.status_code == 200 and response.json().get("status") == "code_required":
        raise SignInError(
            "这个实例启用了邮箱验证码，脚本无法自动收信。\n"
            "请用脚本 smtptest.py 测验证码流程，或临时清空 .env 里的 SMTP 配置再跑本脚本。"
        )

    if response.status_code != 200:
        raise SignInError(f"以 {username} 登录失败：{response.text[:200]}")


def signed_in_client(base: str = BASE) -> httpx.Client:
    """Return a client holding a valid session, and register cleanup for it."""
    client = httpx.Client(base_url=base, timeout=300.0)

    username = os.environ.get("YUE_TEST_USER", "").strip()
    password = os.environ.get("YUE_TEST_PASSWORD", "").strip()
    if username and password:
        _sign_in(client, username, password)
        return client

    # No credentials supplied: make a temporary account of our own.
    accounts.init_db()
    existing = accounts.get_user_by_name(TEMP_USER)
    if existing:
        store.remove_user_data(existing.id)
        accounts.delete_user(existing.id)

    was_empty = accounts.count_users() == 0
    invite = test_invite_code()

    response = client.post(
        "/api/auth/register",
        json={"username": TEMP_USER, "password": TEMP_PASSWORD, "invite_code": invite},
    )
    if response.status_code != 200:
        raise SignInError(f"创建测试账号失败：{response.text[:200]}")

    if was_empty:
        print(
            "注意：数据库原本没有账号，这个临时账号暂时是管理员。"
            "脚本结束时会删掉它，你的正式注册仍然是首位管理员。"
        )

    return client


def cleanup_temp_user() -> None:
    user = accounts.get_user_by_name(TEMP_USER)
    if user:
        store.remove_user_data(user.id)
        accounts.delete_user(user.id)
