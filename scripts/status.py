"""Show what is in the account database, without touching it.

    .venv\\Scripts\\python.exe scripts\\status.py

Read-only. Useful before and after anything that modifies stored data, and for
answering "did that test clean up after itself?".
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app import accounts  # noqa: E402
from app.config import DATA_DIR  # noqa: E402


def stamp(value: float | None) -> str:
    if not value:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))


def main() -> int:
    database = DATA_DIR / "accounts.db"
    print(f"数据库：{database}")
    if not database.is_file():
        print("不存在（还没有人注册过）")
        return 0

    accounts.init_db()
    users = accounts.list_users()

    print(f"\n账号（{len(users)}）")
    if not users:
        print("  （空）")
    for user in users:
        flags = []
        if user.is_admin:
            flags.append("管理员")
        if user.disabled:
            flags.append("已停用")
        if user.email:
            flags.append(f"邮箱 {user.email}")
        print(f"  {user.username:<16} 创建 {stamp(user.created_at)}  "
              f"最后登录 {stamp(user.last_login)}  {' '.join(flags) or '普通'}")

    invites = accounts.list_invites()
    print(f"\n邀请码（{len(invites)}）")
    if not invites:
        print("  （空）")
        print("  提示：没有可用邀请码就无法注册新账号。")
    for invite in invites:
        expiry = stamp(invite["expires_at"]) if invite["expires_at"] else "永久"
        if invite["max_uses"] > 0:
            remaining = invite["max_uses"] - invite["uses"]
            used = f"已用 {invite['uses']}/{invite['max_uses']}"
            state = "可用" if remaining > 0 else "已用尽"
        else:
            used = f"已用 {invite['uses']}"
            state = "无上限"
        print(f"  {invite['code']:<22} {used:<18} {state}  到期 {expiry}  {invite['note']}")

    print(f"\n审计记录：{len(accounts.recent_audit(limit=1000))} 条（最近的在前，用管理后台看）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
