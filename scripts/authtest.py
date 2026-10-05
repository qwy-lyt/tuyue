"""End-to-end check of the account system, over HTTP against a running server.

    .venv\\Scripts\\python.exe scripts\\authtest.py

Covers registration, two-factor enrolment, session handling, per-account data
isolation, throttling and admin permissions. It creates its own accounts and
removes them again.

The admin checks need the very first account to be one of ours, because the
first registrant is the one who becomes an administrator. On a database that
already has accounts, those checks are reported as skipped instead of failing.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _client import test_invite_code  # noqa: E402

from app import accounts  # noqa: E402

# A code of our own, so a regression run never spends one of the operator's.
INVITE = ""

BASE = "http://127.0.0.1:8000"
ADMIN_NAME = "authtest_admin"
MEMBER_NAME = "authtest_member"
PASSWORD = "probe-password-2026"

failures: list[str] = []
skipped: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if passed else 'FAIL'}] {label}")
    if not passed:
        failures.append(label)
        if detail:
            print(f"         {detail}")


def skip(label: str, why: str) -> None:
    print(f"  [SKIP] {label} —— {why}")
    skipped.append(label)


def fresh_client() -> httpx.Client:
    """A client with its own cookie jar, i.e. its own browser session."""
    return httpx.Client(base_url=BASE, timeout=120.0)


# Every name this script registers, including the ones it only ever registers
# unsuccessfully.
ALL_TEST_NAMES = (ADMIN_NAME, MEMBER_NAME, "authtest_nobody", "authtest_weak", "authtest_third")


def cleanup() -> None:
    from app import store

    for name in ALL_TEST_NAMES:
        user = accounts.get_user_by_name(name)
        if user:
            store.remove_user_data(user.id)
            accounts.delete_user(user.id)

    # A run's own deliberate failures count toward the login throttle, so after
    # a few runs the next one starts already locked out and its first checks
    # fail for the wrong reason. Clear the whole address, not just the names
    # listed above: checks that use a deliberately absent username count too.
    accounts.clear_address_attempts("127.0.0.1")


def register(client: httpx.Client, username: str, invite: str, password: str = PASSWORD):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "invite_code": invite},
    )


def login(client: httpx.Client, username: str, password: str = PASSWORD):
    """Sign in, adding an email code when the server has a mail server set up."""
    response = client.post("/api/auth/login", json={"username": username, "password": password})

    # With SMTP configured, the first call only triggers the code. The email
    # path itself is covered by smtptest.py; here we just need a session.
    if response.status_code == 200 and response.json().get("status") == "code_required":
        raise RuntimeError(
            "这个实例启用了邮箱验证码，authtest 无法自动取件。"
            "请改用 smtptest.py，或临时清空 .env 里的 SMTP 配置。"
        )
    return response


def check_unlimited_invites() -> None:
    """An invite with max_uses 0 must never run out.

    Also checks the opposite: a capped code still stops at its cap, so the
    change cannot have quietly made every code unlimited.
    """
    print("预备检查 · 邀请码的次数限制")
    code = accounts.create_invite("authtest-unlimited", "authtest", max_uses=accounts.UNLIMITED_USES)
    try:
        for _ in range(25):
            accounts.redeem_invite(code)
        row = next((i for i in accounts.list_invites() if i["code"] == code), None)
        check("无上限的码可以反复使用", row is not None and row["uses"] == 25,
              str(row and row["uses"]))
        check("用完之后依然是可用状态",
              row is not None and row["max_uses"] == accounts.UNLIMITED_USES
              and accounts._invite_is_usable(row),
              str(row))
    finally:
        accounts.revoke_invite(code)

    capped = accounts.create_invite("authtest-capped", "authtest", max_uses=2)
    try:
        accounts.redeem_invite(capped)
        accounts.redeem_invite(capped)
        exhausted = False
        try:
            accounts.redeem_invite(capped)
        except accounts.AuthError:
            exhausted = True
        check("有上限的码仍然会被用尽", exhausted, "第三次竟然也通过了")
    finally:
        accounts.revoke_invite(capped)
    print()


def main() -> int:
    try:
        return _run()
    finally:
        # Always, including the early exits below. A test that leaves accounts
        # behind is worse than one that fails, because the leftovers are silent.
        cleanup()


def _run() -> int:
    global INVITE
    INVITE = test_invite_code()
    check_unlimited_invites()
    cleanup()
    existing_users = accounts.count_users()
    can_test_admin = existing_users == 0

    if not can_test_admin:
        print(f"数据库里已有 {existing_users} 个账号，管理相关的检查会跳过。\n")

    with fresh_client() as admin, fresh_client() as member, fresh_client() as anon:
        # --- status ------------------------------------------------------
        print("1) 站点状态")
        status = anon.get("/api/status").json()
        check("注册是开放的（有可用邀请码）", status["registration_open"], str(status))

        # --- unauthenticated access --------------------------------------
        print("\n2) 未登录时的访问控制")
        for path in ("/api/conversations", "/api/settings", "/api/files/whatever"):
            response = anon.get(path)
            check(f"{path} 返回 401", response.status_code == 401, str(response.status_code))
        check("管理接口返回 401", anon.get("/api/admin/users").status_code == 401)

        # --- registration -------------------------------------------------
        print("\n3) 注册")
        bad = register(anon, "authtest_nobody", "definitely-wrong-code")
        check("邀请码错误被拒", bad.status_code == 400, bad.text[:120])

        weak = register(anon, "authtest_weak", INVITE, "short")
        check("弱密码被拒", weak.status_code == 400, weak.text[:120])

        admin_response = register(admin, ADMIN_NAME, INVITE)
        check("注册成功", admin_response.status_code == 200, admin_response.text[:200])
        if admin_response.status_code != 200:
            print("\n无法继续：注册失败。")
            return 1

        admin_info = admin_response.json()
        check("注册后直接登录", admin.cookies.get("yue_session") is not None)
        check("注册响应包含身份信息",
              {"id", "username", "is_admin"} <= set(admin_info), str(admin_info))
        if can_test_admin:
            check("首位注册者成为管理员", admin_info["is_admin"] is True, str(admin_info))

        duplicate = register(anon, ADMIN_NAME, INVITE)
        check("重名注册被拒", duplicate.status_code == 400, duplicate.text[:120])

        # --- session + login ----------------------------------------------
        print("\n4) 登录与会话")
        wrong = anon.post("/api/auth/login", json={"username": ADMIN_NAME, "password": "wrong-password-123"})
        check("密码错误被拒", wrong.status_code == 401, str(wrong.status_code))

        check(
            "账号不存在时提示相同（不泄露账号是否存在）",
            anon.post("/api/auth/login", json={"username": "no-such-user", "password": PASSWORD}).json()
            == wrong.json(),
        )

        good = login(anon, ADMIN_NAME)
        check("密码正确即可登录", good.status_code == 200, good.text[:200])
        check("登录后可访问自己的数据", anon.get("/api/conversations").status_code == 200)

        anon.post("/api/auth/logout")
        check("退出后会话失效", anon.get("/api/conversations").status_code == 401)

        # --- second account + isolation ------------------------------------
        print("\n5) 多用户隔离")
        invite = None
        if can_test_admin:
            created = admin.post(
                "/api/admin/invites", json={"note": "authtest", "max_uses": 1, "expires_in_days": 1}
            )
            if created.status_code == 200:
                invite = created.json()["code"]
            else:
                check("管理员生成邀请码", False, created.text[:150])

        if invite is None:
            skip("多用户隔离", "需要管理员权限生成邀请码")
        else:
            member_response = register(member, MEMBER_NAME, invite)
            check("用管理员发的邀请码注册成功", member_response.status_code == 200, member_response.text[:200])

            check("邀请码用尽后失效", register(anon, "authtest_third", invite).status_code == 400)

            # The admin writes a conversation; the member must not see it.
            admin.post("/api/chat", json={"message": "隔离检查用的对话"})
            admin_list = admin.get("/api/conversations").json()
            check("管理员有自己的对话", len(admin_list) >= 1, str(admin_list)[:150])
            if admin_list:
                target_id = admin_list[0]["id"]
                check("成员看不到管理员的对话", member.get(f"/api/conversations/{target_id}").status_code == 404)
                check("成员列表里没有别人的对话", member.get("/api/conversations").json() == [])
                check("成员删不掉别人的对话", member.delete(f"/api/conversations/{target_id}").status_code == 404)

            # --- admin permissions -----------------------------------------
            print("\n6) 管理权限")
            check("普通用户访问管理接口被拒", member.get("/api/admin/users").status_code == 403)
            check(
                "普通用户不能创建账号",
                member.post("/api/admin/users", json={"username": "x", "password": "y"}).status_code == 403,
            )

            if can_test_admin:
                users = admin.get("/api/admin/users").json()
                names = [u["username"] for u in users]
                check("管理员能看到账号列表", ADMIN_NAME in names and MEMBER_NAME in names, str(names))
                check("列表不含对话内容", all("messages" in u and isinstance(u["messages"], int) for u in users))

                me = next(u for u in users if u["username"] == ADMIN_NAME)
                check(
                    "最后一个管理员不能停用自己",
                    admin.post(
                        f"/api/admin/users/{me['id']}/disabled", json={"disabled": True}
                    ).status_code == 400,
                )

                audit = admin.get("/api/admin/audit").json()
                actions = {entry["action"] for entry in audit}
                check("审计日志记录了登录和注册", {"login", "register"} <= actions, str(sorted(actions)))

                # --- path traversal ------------------------------------------
                print("\n7) 路径穿越")
                evil = admin.get("/api/conversations/..%2F..%2Fescape").status_code
                check("URL 编码的穿越被拒", evil in (400, 404), str(evil))
                check("非法 id 直接返回 404", admin.get("/api/files/not-an-id").status_code == 404)

                # --- throttling ----------------------------------------------
                print("\n8) 登录限流")
                with fresh_client() as throttle:
                    statuses = []
                    for _ in range(7):
                        response = throttle.post(
                            "/api/auth/login",
                            json={"username": MEMBER_NAME, "password": "wrong-password-123"},
                        )
                        statuses.append(response.status_code)
                    check("连续失败后触发 429", 429 in statuses, str(statuses))
                    if 429 in statuses:
                        first_429 = statuses.index(429)
                        check("第 5 次之后才开始限流", first_429 >= 5, f"首个 429 在第 {first_429 + 1} 次")
                        accounts.clear_failed_logins(MEMBER_NAME, "127.0.0.1")

            member.post("/api/auth/logout")

    # Cleanup happens in main()'s finally, so it also runs on early exits.
    print()
    if skipped:
        print(f"跳过 {len(skipped)} 项：")
        for name in skipped:
            print(f"  - {name}")
    if failures:
        print(f"失败 {len(failures)} 项：")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
