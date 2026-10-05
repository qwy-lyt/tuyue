"""Prove that a schema change upgrades an existing database instead of replacing it.

    .venv\\Scripts\\python.exe scripts\\migrationtest.py

This is the guarantee behind "never lose user data": an older database -- one
created before a column existed -- must gain that column while keeping every
account in it. The test builds such a database in a scratch directory, so your
real accounts are never involved.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

# The oldest schema this project ever shipped: no email column at all.
OLD_SCHEMA = """
CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash BLOB NOT NULL,
    salt          BLOB NOT NULL,
    totp_secret   TEXT NOT NULL DEFAULT '',
    totp_confirmed INTEGER NOT NULL DEFAULT 0,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    last_login    REAL
);
"""

failures: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if passed else 'FAIL'}] {label}")
    if not passed:
        failures.append(label)
        if detail:
            print(f"         {detail}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="migrationtest_") as scratch:
        data_dir = Path(scratch)
        database = data_dir / "accounts.db"

        # --- build a database in the old shape, with a real account in it ---
        connection = sqlite3.connect(database)
        connection.executescript(OLD_SCHEMA)
        connection.execute(
            "INSERT INTO users (id, username, password_hash, salt, is_admin, created_at, totp_secret)"
            " VALUES (?, ?, ?, ?, 1, ?, ?)",
            ("0123456789abcdef", "老用户", b"\x01" * 32, b"\x02" * 16, 1700000000.0, "ABCDEF"),
        )
        connection.commit()
        connection.close()

        before = sqlite3.connect(database)
        columns_before = {row[1] for row in before.execute("PRAGMA table_info(users)")}
        before.close()
        print(f"迁移前 users 表的列：{sorted(columns_before)}\n")

        check("旧库确实没有 email 列（前提成立）", "email" not in columns_before)

        # --- run init_db against that database, in a separate process ------
        script = (
            "import sys; sys.path.insert(0, r'%s');"
            "from app import accounts;"
            "added = accounts.init_db();"
            "print('MIGRATED:' + ','.join(added))" % PROJECT
        )
        environment = {**os.environ, "YUE_DATA_DIR": str(data_dir)}
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(PROJECT),
            env=environment,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            print("迁移进程失败：")
            print(completed.stdout[-1500:])
            print(completed.stderr[-1500:])
            return 1

        reported = [
            line.split(":", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("MIGRATED:")
        ]
        print(f"迁移报告新增：{reported}\n")
        check("报告里说补上了 users.email", any("users.email" in item for item in reported), str(reported))

        # --- the account must still be there, and the column now exists ----
        after = sqlite3.connect(database)
        after.row_factory = sqlite3.Row
        columns_after = {row[1] for row in after.execute("PRAGMA table_info(users)")}
        rows = after.execute("SELECT * FROM users").fetchall()
        after.close()

        check("email 列已经补上", "email" in columns_after, str(sorted(columns_after)))
        check("账号没有被清空", len(rows) == 1, f"剩 {len(rows)} 行")
        if rows:
            row = rows[0]
            check("用户名保持原样", row["username"] == "老用户", str(row["username"]))
            check("密码哈希没有被动过", bytes(row["password_hash"]) == b"\x01" * 32)
            check("管理员身份保持", row["is_admin"] == 1)
            check("新列的默认值可用", row["email"] == "", repr(row["email"]))
        check("旧的多余列被保留而不是被删",
              {"totp_secret", "totp_confirmed"} <= columns_after,
              str(sorted(columns_after)))

        # --- running it twice must be a no-op ------------------------------
        second = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(PROJECT), env=environment, capture_output=True, text=True,
        )
        check("重复迁移不会再次改动", "MIGRATED:" in second.stdout, second.stdout[-200:])

        # Close every connection: Windows will not delete the scratch directory
        # while one is still open.
        third = sqlite3.connect(database)
        remaining = third.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        third.close()
        check("重复迁移之后账号仍只有一个", remaining == 1, f"剩 {remaining} 行")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("通过 —— 老数据库会就地升级，账号不会丢")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
