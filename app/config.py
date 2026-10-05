"""Application settings, loaded from the project's .env file."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Tests point this at a scratch directory so they never touch real accounts.
_data_override = os.environ.get("YUE_DATA_DIR", "").strip()
DATA_DIR = Path(_data_override) if _data_override else BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
TMP_DIR = DATA_DIR / "tmp"
USERS_DIR = DATA_DIR / "users"
DB_PATH = DATA_DIR / "accounts.db"
ENV_PATH = BASE_DIR / ".env"

# Kept only so old data directories from earlier versions are still recognised
# when they exist; new uploads and conversations live under users/.
UPLOAD_DIR = DATA_DIR / "uploads"
CONVERSATION_DIR = DATA_DIR / "conversations"

load_dotenv(ENV_PATH)


def _keep_everything_in_project() -> None:
    """Point temp files and library caches at the project folder.

    Left alone, matplotlib, ezdxf and tempfile all scatter files into the user
    profile on C:. These variables must be set before either library is first
    imported -- this module is imported before them, so this is the right place.
    """
    for path in (DATA_DIR, CACHE_DIR, TMP_DIR, USERS_DIR, UPLOAD_DIR, CONVERSATION_DIR):
        path.mkdir(parents=True, exist_ok=True)

    os.environ["TEMP"] = str(TMP_DIR)
    os.environ["TMP"] = str(TMP_DIR)
    os.environ["MPLCONFIGDIR"] = str(CACHE_DIR / "matplotlib")
    os.environ["XDG_CACHE_HOME"] = str(CACHE_DIR)
    os.environ["XDG_CONFIG_HOME"] = str(CACHE_DIR / "config")


_keep_everything_in_project()


def _text(key: str, default: str) -> str:
    value = os.getenv(key)
    return default if value is None or not value.strip() else value.strip()


def _number(key: str, default: int) -> int:
    try:
        return int(_text(key, str(default)))
    except ValueError:
        return default


def _flag(key: str, default: bool) -> bool:
    value = _text(key, "true" if default else "false").lower()
    return value in {"1", "true", "yes", "on"}


def _load_or_create_secret_key() -> str:
    """The Fernet key that protects stored user API keys.

    Kept in .env rather than data/, so that a copy of the data directory alone
    does not hand over the key needed to decrypt it.
    """
    existing = _text("SECRET_KEY", "")
    if existing:
        return existing

    generated = secrets.token_urlsafe(32)
    with ENV_PATH.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n# 首次运行时自动生成，用于加密用户自己的 API Key。删掉会导致已存的 Key 无法解密。\n"
            f"SECRET_KEY={generated}\n"
        )
    os.environ["SECRET_KEY"] = generated
    return generated


@dataclass(frozen=True)
class Settings:
    app_name: str
    api_key: str
    base_url: str
    model: str
    system_prompt: str

    # Rendering budget. Every image we hand to the model is base64-encoded inline,
    # and the API rejects request bodies over 48 MiB, so both knobs stay modest.
    max_images_per_turn: int
    image_max_px: int
    image_jpeg_quality: int

    oda_converter_path: str
    drawing_cjk_font: str
    request_timeout: int

    # Accounts and sessions.
    secret_key: str
    invite_code: str
    session_days: int
    cookie_secure: bool
    max_upload_mb: int

    host: str
    port: int

    @property
    def configured(self) -> bool:
        """False until at least one API key exists anywhere."""
        return bool(self.api_key)


def load_settings() -> Settings:
    return Settings(
        app_name=_text("APP_NAME", "图阅"),
        api_key=_text("DEEPSEEK_API_KEY", ""),
        base_url=_text("AI_BASE_URL", "https://api.deepseek.com"),
        model=_text("AI_MODEL", "deepseek-flash"),
        system_prompt=_text(
            "SYSTEM_PROMPT",
            "你是一个专业的工程文档助手，擅长阅读 PDF 文档和 CAD 图纸。"
            "回答时请基于用户上传的文件内容，引用具体的数据、标注和图层信息。"
            "如果文件中缺少回答所需的信息，请直接说明，不要臆测。",
        ),
        max_images_per_turn=_number("MAX_IMAGES_PER_TURN", 6),
        image_max_px=_number("IMAGE_MAX_PX", 1800),
        image_jpeg_quality=_number("IMAGE_JPEG_QUALITY", 82),
        oda_converter_path=_text("ODA_CONVERTER_PATH", ""),
        drawing_cjk_font=_text("DRAWING_CJK_FONT", ""),
        request_timeout=_number("REQUEST_TIMEOUT", 300),
        secret_key=_load_or_create_secret_key(),
        invite_code=_text("INVITE_CODE", ""),
        session_days=_number("SESSION_DAYS", 30),
        # Only turn this off when the app runs behind an encrypted tunnel such as
        # Radmin VPN. Over any untrusted link the cookie must be Secure.
        cookie_secure=_flag("COOKIE_SECURE", False),
        max_upload_mb=_number("MAX_UPLOAD_MB", 50),
        host=_text("HOST", "127.0.0.1"),
        port=_number("PORT", 8000),
    )


settings = load_settings()


def ensure_dirs() -> None:
    """Kept for callers that expect this hook; directories are made at import."""
    _keep_everything_in_project()
