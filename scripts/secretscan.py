"""Scan everything that would be committed, looking for real secrets.

    .venv\\Scripts\\python.exe scripts\\_secret_scan.py

The values to look for are read from .env at run time rather than written into
this file: a scanner that hard-codes the keys it hunts for is itself a leak, and
it also goes stale the moment a key is rotated.

Run before the first commit and before every push.
"""

import os
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT / ".env"

# Mirrors .gitignore so the scan covers exactly what a commit would contain.
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "data", "backups", ".pytest_cache",
             ".vscode", ".idea", "node_modules"}
SKIP_FILES = {".env"}

TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".txt", ".json", ".bat", ".ps1",
                 ".ini", ".cfg", ".toml", ".yml", ".yaml", ".example"}

# Shapes worth flagging even when the exact value is unknown.
PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "疑似 API Key"),
    (re.compile(r"\btp-[A-Za-z0-9]{20,}"), "疑似 Token Plan Key"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "疑似 Google Key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}"), "疑似 GitHub Token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "疑似 Slack Token"),
]

# Anything shorter than this is too generic to match on (e.g. "true", "8000").
MIN_SECRET_LENGTH = 8

# Only variables whose *name* marks them as credentials. Values like AI_BASE_URL
# or HOST are configuration and legitimately appear all over the code -- flagging
# those buries the real hits in noise, and nobody reads a scan that always fails.
SECRET_NAME_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "INVITE",
                     "CREDENTIAL", "AUTH")


def secrets_from_env() -> dict[str, str]:
    """Credential-looking values from .env, plus this machine's user name."""
    found: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not any(hint in key.upper() for hint in SECRET_NAME_HINTS):
                continue
            if len(value) >= MIN_SECRET_LENGTH:
                found[key] = value

    username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(username) >= 4:
        found["系统用户名"] = username
        found["用户目录"] = f"Users\\{username}"
        found["用户目录（正斜杠）"] = f"Users/{username}"
    return found


def included_files() -> list[Path]:
    found = []
    for path in PROJECT.rglob("*"):
        if not path.is_file():
            continue
        if set(path.parts) & SKIP_DIRS:
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".gitignore":
            continue
        found.append(path)
    return found


def main() -> int:
    secrets = secrets_from_env()
    files = included_files()

    print(f"待提交文件：{len(files)} 个")
    print(f"比对目标  ：{len(secrets)} 个来自 .env 的值 + 用户名")
    for key in secrets:
        print(f"  · {key}")
    print()

    problems = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(PROJECT)

        for label, value in secrets.items():
            if value in text:
                problems.append((relative, label, value[:12] + "…"))

        for pattern, label in PATTERNS:
            for match in pattern.finditer(text):
                problems.append((relative, label, match.group()[:40]))

    if not problems:
        print("扫描通过：没有发现任何密钥、邮箱、用户名或绝对路径。")
        return 0

    print(f"发现 {len(problems)} 处问题，**不要提交**：")
    for relative, label, sample in problems:
        print(f"  {relative}  ->  {label}  ({sample})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
