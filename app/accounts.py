"""Accounts, sessions, invite codes and the audit trail.

Identity lives in one SQLite file (stdlib -- nothing to install or run). The
actual content -- conversations, uploaded files -- stays as plain files under
data/users/<id>/, so this module only owns who you are and what you may do.

A note on invite codes: they are stored in clear text rather than hashed, on
purpose. The admin panel has to be able to show a code again so it can be
re-shared, and a code that only exists as a one-time display is useless for a
handful of friends. Treat a code as a door key you hand out by hand.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .config import DB_PATH, settings

# scrypt parameters. n=2**14 with r=8 needs ~16 MiB of memory per hash, which is
# a deliberate cost: it makes offline guessing expensive without being slow
# enough to annoy anyone logging in.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16
KEY_BYTES = 32

MIN_PASSWORD_LENGTH = 10
MAX_USERNAME_LENGTH = 32

LOGIN_WINDOW_SECONDS = 15 * 60
# Per account, so guessing one password is cut off quickly.
MAX_LOGIN_ATTEMPTS = 5
# Per address across all accounts, to catch someone cycling through usernames.
# Set well above the per-account limit so normal fumbling never trips it.
MAX_ATTEMPTS_PER_ADDRESS = 20

# A max_uses of zero (or anything below) means the code never runs out.
UNLIMITED_USES = 0

SESSION_TOKEN_BYTES = 32

# Rejected outright -- long enough to pass the length rule but trivially guessed.
WEAK_PASSWORDS = {
    "1234567890", "qwertyuiop", "password12", "password123", "admin12345",
    "123456789a", "aaaaaaaaaa", "1111111111", "qwerty12345", "iloveyou12",
}


class AuthError(Exception):
    """A failure that is safe to show the person who caused it."""


# --------------------------------------------------------------------------
# database plumbing
# --------------------------------------------------------------------------

SCHEMA = """
-- `email` is a leftover from the email-verification feature and is no longer
-- read or written. It stays because dropping a column risks losing data, and
-- this project only ever migrates additively.
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email         TEXT NOT NULL DEFAULT '',
    password_hash BLOB NOT NULL,
    salt          BLOB NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    last_login    REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS login_attempts (
    username TEXT NOT NULL,
    ip       TEXT NOT NULL,
    at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_lookup ON login_attempts(username, ip, at);

CREATE TABLE IF NOT EXISTS invite_codes (
    code       TEXT PRIMARY KEY,
    note       TEXT NOT NULL DEFAULT '',
    created_by TEXT,
    created_at REAL NOT NULL,
    expires_at REAL,
    max_uses   INTEGER NOT NULL DEFAULT 1,
    uses       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     REAL NOT NULL,
    actor  TEXT,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ip     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at DESC);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """A fresh connection per operation -- simple and thread-safe."""
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# Columns a newer version expects but an older database may not have yet.
#
# CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so adding a
# field used to mean rebuilding the table -- which is how real accounts get
# destroyed. Instead, register the new column here and init_db() adds it in
# place. This list is additive only: never drop or retype a column through it.
_REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    "users": {
        "email": "ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''",
    },
}


def _migrate(connection: sqlite3.Connection) -> list[str]:
    """Add any missing columns, in place. Returns what was changed."""
    applied: list[str] = []
    for table, columns in _REQUIRED_COLUMNS.items():
        present = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not present:
            continue  # brand new database; the CREATE above already covers it
        for name, statement in columns.items():
            if name not in present:
                connection.execute(statement)
                applied.append(f"{table}.{name}")
    return applied


def init_db() -> list[str]:
    """Create tables if absent, then bring older ones up to date.

    Returns the list of columns that were added, so the caller can log it.
    """
    with _connect() as connection:
        connection.executescript(SCHEMA)
        return _migrate(connection)


def new_id() -> str:
    return secrets.token_hex(8)


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
        maxmem=SCRYPT_MAXMEM,
    )


def check_password_strength(password: str) -> None:
    """Raise AuthError with a readable reason when the password is too weak."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")
    if password.strip() == "":
        raise AuthError("密码不能全是空白字符")
    if password.lower() in WEAK_PASSWORDS:
        raise AuthError("这个密码太常见了，换一个")
    if len(set(password)) < 4:
        raise AuthError("密码里重复字符太多，换一个复杂点的")


def check_username(username: str) -> None:
    if not username:
        raise AuthError("用户名不能为空")
    if len(username) > MAX_USERNAME_LENGTH:
        raise AuthError(f"用户名不能超过 {MAX_USERNAME_LENGTH} 个字符")
    if not all(character.isalnum() or character in "_-." for character in username):
        raise AuthError("用户名只能用字母、数字、下划线、短横线和点")


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


@dataclass
class User:
    id: str
    username: str
    email: str
    is_admin: bool
    disabled: bool
    created_at: float
    last_login: float | None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> "User":
        return User(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            is_admin=bool(row["is_admin"]),
            disabled=bool(row["disabled"]),
            created_at=row["created_at"],
            last_login=row["last_login"],
        )


def count_users() -> int:
    with _connect() as connection:
        return connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def get_user(user_id: str) -> User | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User._from_row(row) if row else None


def get_user_by_name(username: str) -> User | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
    return User._from_row(row) if row else None


def list_users() -> list[User]:
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [User._from_row(row) for row in rows]


def create_user(username: str, password: str, *, is_admin: bool | None = None) -> User:
    """Create an account. The very first account always becomes an admin."""
    check_username(username)
    check_password_strength(password)

    salt = secrets.token_bytes(SALT_BYTES)
    password_hash = _derive(password, salt)
    user_id = new_id()

    with _connect() as connection:
        existing = connection.execute(
            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if existing:
            raise AuthError("这个用户名已经被占用了")

        if is_admin is None:
            is_admin = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0

        connection.execute(
            "INSERT INTO users (id, username, password_hash, salt, is_admin, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, password_hash, salt, int(is_admin), time.time()),
        )

    created = get_user(user_id)
    assert created is not None
    return created


def verify_password(username: str, password: str) -> User | None:
    """Return the user when the password matches, else None.

    Always runs the key derivation, even for unknown usernames, so that response
    timing does not reveal which accounts exist.
    """
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()

    salt = row["salt"] if row else b"\x00" * SALT_BYTES
    expected = row["password_hash"] if row else b"\x00" * KEY_BYTES
    candidate = _derive(password, salt)

    if not hmac.compare_digest(candidate, expected) or row is None:
        return None
    return User._from_row(row)


def set_password(user_id: str, password: str) -> None:
    check_password_strength(password)
    salt = secrets.token_bytes(SALT_BYTES)
    with _connect() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
            (_derive(password, salt), salt, user_id),
        )


def set_disabled(user_id: str, disabled: bool) -> None:
    with _connect() as connection:
        connection.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id))
        if disabled:
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def delete_user(user_id: str) -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))


def count_admins(*, excluding: str | None = None) -> int:
    query = "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND disabled = 0"
    params: tuple = ()
    if excluding:
        query += " AND id != ?"
        params = (excluding,)
    with _connect() as connection:
        return connection.execute(query, params).fetchone()["n"]


def promote(user_id: str, is_admin: bool) -> None:
    with _connect() as connection:
        connection.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))


def touch_login(user_id: str) -> None:
    with _connect() as connection:
        connection.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user_id))




# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_session(user_id: str) -> str:
    """Return a fresh session token. Only its hash is persisted."""
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    now = time.time()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, now, now + settings.session_days * 86400),
        )
    return token


def resolve_session(token: str) -> User | None:
    if not token:
        return None
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (_token_hash(token),)
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] < time.time():
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
            return None
        user_row = connection.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()

    if user_row is None or user_row["disabled"]:
        return None
    return User._from_row(user_row)


def destroy_session(token: str) -> None:
    if not token:
        return
    with _connect() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def destroy_user_sessions(user_id: str) -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_expired_sessions() -> int:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        return cursor.rowcount


# --------------------------------------------------------------------------
# login throttling
# --------------------------------------------------------------------------


def record_failed_login(username: str, ip: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO login_attempts (username, ip, at) VALUES (?, ?, ?)",
            (username.lower(), ip, time.time()),
        )
        # Opportunistic cleanup, so the table cannot grow without bound.
        connection.execute("DELETE FROM login_attempts WHERE at < ?", (time.time() - 86400,))


def is_throttled(username: str, ip: str) -> bool:
    """Should this login attempt be refused before it is even checked?

    Two limits apply. One guards a single account against guessing. The other
    guards the address itself: without it, an attacker who simply varies the
    username gets unlimited tries, since each name starts with a clean count.
    """
    since = time.time() - LOGIN_WINDOW_SECONDS
    with _connect() as connection:
        per_account = connection.execute(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE username = ? AND ip = ? AND at > ?",
            (username.lower(), ip, since),
        ).fetchone()["n"]
        if per_account >= MAX_LOGIN_ATTEMPTS:
            return True

        per_address = connection.execute(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE ip = ? AND at > ?",
            (ip, since),
        ).fetchone()["n"]
    return per_address >= MAX_ATTEMPTS_PER_ADDRESS


def clear_failed_logins(username: str, ip: str) -> None:
    with _connect() as connection:
        connection.execute(
            "DELETE FROM login_attempts WHERE username = ? AND ip = ?",
            (username.lower(), ip),
        )


def clear_address_attempts(ip: str) -> None:
    """Wipe every failure from one address.

    Needed by the test scripts, whose deliberate failures would otherwise
    accumulate across runs until the next run is refused before it starts.
    """
    with _connect() as connection:
        connection.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))


# --------------------------------------------------------------------------
# invite codes
# --------------------------------------------------------------------------


def seed_bootstrap_invite() -> bool:
    """Make the .env invite code usable, so the first admin can register.

    It gets no usage cap. This is the operator's own code -- counting down from
    ten and then refusing to let them in is friction with no security value,
    since the code lives in .env and can be rotated there at any time.
    """
    code = settings.invite_code.strip()
    if not code:
        return False

    with _connect() as connection:
        existing = connection.execute(
            "SELECT max_uses FROM invite_codes WHERE code = ?", (code,)
        ).fetchone()
        if existing:
            if existing["max_uses"] != UNLIMITED_USES:
                connection.execute(
                    "UPDATE invite_codes SET max_uses = ? WHERE code = ?", (UNLIMITED_USES, code)
                )
                return True
            return False

        connection.execute(
            "INSERT INTO invite_codes (code, note, created_by, created_at, max_uses)"
            " VALUES (?, ?, NULL, ?, ?)",
            (code, "来自 .env 的引导码", time.time(), UNLIMITED_USES),
        )
    return True


def create_invite(
    note: str, created_by: str, max_uses: int = 1, expires_in_days: int | None = None
) -> str:
    """Mint an invite code. Pass UNLIMITED_USES (0) for no cap."""
    code = secrets.token_urlsafe(12)
    expires_at = time.time() + expires_in_days * 86400 if expires_in_days else None
    with _connect() as connection:
        connection.execute(
            "INSERT INTO invite_codes (code, note, created_by, created_at, expires_at, max_uses)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (code, note, created_by, time.time(), expires_at, max_uses),
        )
    return code


def list_invites() -> list[dict]:
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM invite_codes ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def revoke_invite(code: str) -> bool:
    with _connect() as connection:
        return connection.execute("DELETE FROM invite_codes WHERE code = ?", (code,)).rowcount > 0


def _invite_is_usable(row: sqlite3.Row | None) -> bool:
    if row is None:
        return False
    if row["expires_at"] is not None and row["expires_at"] < time.time():
        return False
    if row["max_uses"] <= UNLIMITED_USES:
        return True  # 0 means no cap
    return row["uses"] < row["max_uses"]


def redeem_invite(code: str) -> None:
    """Consume one use of an invite code, or raise AuthError."""
    candidate = (code or "").strip()
    with _connect() as connection:
        row = connection.execute("SELECT * FROM invite_codes", ).fetchall()
        match = next((item for item in row if hmac.compare_digest(item["code"], candidate)), None)
        if not _invite_is_usable(match):
            raise AuthError("邀请码无效或已用尽")
        connection.execute(
            "UPDATE invite_codes SET uses = uses + 1 WHERE code = ?", (match["code"],)
        )


# --------------------------------------------------------------------------
# audit log
# --------------------------------------------------------------------------


def audit(action: str, *, actor: str | None = None, detail: str = "", ip: str = "") -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO audit_log (at, actor, action, detail, ip) VALUES (?, ?, ?, ?, ?)",
            (time.time(), actor, action, detail, ip),
        )


def recent_audit(limit: int = 200) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM audit_log ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]
