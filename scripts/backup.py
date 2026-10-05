"""Snapshot accounts and user data.

    .venv\\Scripts\\python.exe scripts\\backup.py

Writes a timestamped zip into `backups/` -- deliberately *outside* `data/`, so
that clearing the data directory does not take the backups with it.

Run this before anything that touches stored data. It is cheap and there is no
way to recover an account that was never backed up.
"""

from __future__ import annotations

import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# Imported for the path constants only -- this must not depend on the running
# server, and must work even when the server is stopped.
from app.config import DATA_DIR  # noqa: E402

BACKUP_DIR = PROJECT / "backups"


def collect() -> list[Path]:
    """Everything worth keeping: the account database and every user's folder.

    The parse caches under users/<id>/cache are included even though they can be
    regenerated -- regenerating means asking people to upload their files again,
    which is exactly what a backup is meant to avoid.
    """
    files: list[Path] = []
    database = DATA_DIR / "accounts.db"
    if database.is_file():
        files.append(database)
    # SQLite may have a write-ahead log alongside the database; capture it too
    # so the snapshot is consistent.
    for suffix in ("-wal", "-shm"):
        sidecar = DATA_DIR / f"accounts.db{suffix}"
        if sidecar.is_file():
            files.append(sidecar)

    users = DATA_DIR / "users"
    if users.is_dir():
        files.extend(path for path in users.rglob("*") if path.is_file())
    return files


def main() -> int:
    files = collect()
    if not files:
        print("没有可备份的内容：既没有账号数据库，也没有用户数据。")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"yue-backup-{stamp}.zip"

    total = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            # Keep the data/ prefix so a restore is a straight unzip.
            archive.write(path, arcname=Path("data") / path.relative_to(DATA_DIR))
            total += path.stat().st_size

    print(f"已备份 {len(files)} 个文件（原始大小 {total / 1024:.1f} KB）")
    print(f"备份位置：{target}")
    print(f"压缩后大小：{target.stat().st_size / 1024:.1f} KB")

    account_db = DATA_DIR / "accounts.db"
    if account_db.is_file():
        print("\n提醒：这个备份包含所有人的密码哈希和加密后的 API Key。请妥善保管。")

    existing = sorted(BACKUP_DIR.glob("yue-backup-*.zip"))
    if len(existing) > 1:
        print(f"\nbackups/ 目录下现有 {len(existing)} 个备份，最早的是 {existing[0].name}。")
        print("备份不会被自动删除，积多了可以自己清理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
