"""Persistence for conversations, uploaded files and per-user settings.

Everything an account owns lives under data/users/<user_id>/. That layout is
what keeps one person's conversations out of another's reach: the API layer
passes in the authenticated user id, and every path is built from ids that are
validated first. An id that reaches this module has already been checked against
a strict pattern, so it cannot escape its directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import USERS_DIR

# Ids are generated here as 16 hex characters. Anything else is rejected rather
# than escaped -- the app never produces one, so a mismatch means tampering.
_ID_RE = re.compile(r"^[0-9a-f]{16}$")


class InvalidId(ValueError):
    """Raised when an id would not be safe to use as a path component."""


def _checked(identifier: str) -> str:
    if not isinstance(identifier, str) or not _ID_RE.match(identifier):
        raise InvalidId(f"非法 id：{identifier!r}")
    return identifier


@dataclass
class FileRecord:
    """What we know about one uploaded file, plus where its artifacts live."""

    file_id: str
    filename: str
    kind: str
    text: str = ""
    images: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    uploaded_at: float = field(default_factory=time.time)

    @property
    def image_paths(self) -> list[Path]:
        return [Path(p) for p in self.images]


@dataclass
class Message:
    role: str
    content: str
    file_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class Conversation:
    id: str
    title: str = "新对话"
    created_at: float = field(default_factory=time.time)
    messages: list[Message] = field(default_factory=list)


@dataclass
class UserSettings:
    """Per-account overrides. The API key is stored encrypted."""

    base_url: str = ""
    model: str = ""
    api_key_encrypted: str = ""

    @property
    def has_key(self) -> bool:
        return bool(self.api_key_encrypted)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


# --------------------------------------------------------------------------
# per-user directories
# --------------------------------------------------------------------------


def user_dir(user_id: str) -> Path:
    return USERS_DIR / _checked(user_id)


def conversations_dir(user_id: str) -> Path:
    return user_dir(user_id) / "conversations"


def uploads_dir(user_id: str) -> Path:
    return user_dir(user_id) / "uploads"


def cache_root(user_id: str) -> Path:
    return user_dir(user_id) / "cache"


def file_dir(user_id: str, file_id: str) -> Path:
    return cache_root(user_id) / _checked(file_id)


def ensure_user_dirs(user_id: str) -> None:
    for path in (
        user_dir(user_id),
        conversations_dir(user_id),
        uploads_dir(user_id),
        cache_root(user_id),
    ):
        path.mkdir(parents=True, exist_ok=True)


def remove_user_data(user_id: str) -> None:
    """Delete everything an account owns. Used when an admin removes a user."""
    target = USERS_DIR / _checked(user_id)
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


def user_storage_bytes(user_id: str) -> int:
    root = USERS_DIR / _checked(user_id)
    if not root.is_dir():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------


def save_upload(user_id: str, file_id: str, filename: str, payload: bytes) -> Path:
    """Store an upload under its own folder, keeping the original filename.

    The name matters: the extractors report `path.name` back as the display name,
    so renaming the file on disk would lose what the user called it.
    """
    ensure_user_dirs(user_id)
    target_dir = uploads_dir(user_id) / _checked(file_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / Path(filename).name  # .name also strips any path components
    dest.write_bytes(payload)
    return dest


def save_file_record(user_id: str, record: FileRecord) -> None:
    target = file_dir(user_id, record.file_id)
    target.mkdir(parents=True, exist_ok=True)
    (target / "meta.json").write_text(
        json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_file_record(user_id: str, file_id: str) -> FileRecord | None:
    try:
        meta_path = file_dir(user_id, file_id) / "meta.json"
    except InvalidId:
        return None
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return FileRecord(**data)


def load_file_records(user_id: str, file_ids: list[str]) -> list[FileRecord]:
    records = []
    for file_id in file_ids:
        record = load_file_record(user_id, file_id)
        if record is not None:
            records.append(record)
    return records


def list_file_records(user_id: str) -> list[FileRecord]:
    """Every file this account has ever uploaded, newest first.

    Backs the file library: uploads are kept rather than consumed by the next
    message, so the listing has to survive restarts and page reloads.
    """
    root = cache_root(user_id)
    if not root.is_dir():
        return []

    records: list[FileRecord] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        record = load_file_record(user_id, entry.name)
        if record is not None:
            records.append(record)
    return sorted(records, key=lambda item: item.uploaded_at, reverse=True)


def file_summary(record: FileRecord, *, has_reading: bool = False) -> dict:
    """A listing row: no extracted text, just enough to draw the entry.

    `has_reading` says whether this one file already has a stored reading of its
    own. The library shows it so a person can tell which drawings are free to
    reopen and which ones would spend a model call.
    """
    first_image = ""
    for path in record.image_paths:
        if path.is_file():
            first_image = f"/api/files/{record.file_id}/images/0"
            break

    return {
        "file_id": record.file_id,
        "filename": record.filename,
        "kind": record.kind,
        "uploaded_at": record.uploaded_at,
        "has_text": bool(record.text.strip()),
        "image_count": len(record.images),
        "thumbnail": first_image,
        "warnings": record.warnings,
        "has_reading": has_reading,
    }


def reading_exists(user_id: str, file_id: str) -> bool:
    """Whether this file has been read on its own, with no other file in the set."""
    return load_extraction(user_id, [file_id]) is not None


def delete_file_record(user_id: str, file_id: str) -> FileRecord | None:
    """Drop one uploaded file, returning what it was, or None if nothing was there.

    Three things go: the uploaded bytes, the parse cache behind the record, and
    every stored extraction that had this file in its set. That last part is not
    optional housekeeping -- an extraction is keyed on the whole set, so once one
    member is gone the saved table describes a set that can never be asked for
    again, and would sit on disk forever.

    An id that could not name a stored file is answered with None rather than
    raised: the caller turns that into a 404, which is also what a malformed id
    deserves.
    """
    try:
        file_id = _checked(file_id)
    except InvalidId:
        return None

    record = load_file_record(user_id, file_id)
    if record is None:
        return None

    for path in (uploads_dir(user_id) / file_id, file_dir(user_id, file_id)):
        shutil.rmtree(path, ignore_errors=True)

    for path in extractions_dir(user_id).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if file_id in (data.get("file_ids") or []):
            path.unlink(missing_ok=True)

    return record


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------


def _conversation_path(user_id: str, conversation_id: str) -> Path:
    return conversations_dir(user_id) / f"{_checked(conversation_id)}.json"


def save_conversation(user_id: str, conversation: Conversation) -> None:
    ensure_user_dirs(user_id)
    payload = {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "messages": [asdict(m) for m in conversation.messages],
    }
    _conversation_path(user_id, conversation.id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_conversation(user_id: str, conversation_id: str) -> Conversation | None:
    try:
        path = _conversation_path(user_id, conversation_id)
    except InvalidId:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return Conversation(
        id=data["id"],
        title=data.get("title", "新对话"),
        created_at=data.get("created_at", time.time()),
        messages=[Message(**m) for m in data.get("messages", [])],
    )


def list_conversations(user_id: str) -> list[dict]:
    directory = conversations_dir(user_id)
    if not directory.is_dir():
        return []

    summaries = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        summaries.append(
            {
                "id": data.get("id", path.stem),
                "title": data.get("title", "新对话"),
                "created_at": data.get("created_at", 0),
                "message_count": len(data.get("messages", [])),
            }
        )
    return sorted(summaries, key=lambda item: item["created_at"], reverse=True)


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    try:
        path = _conversation_path(user_id, conversation_id)
    except InvalidId:
        return False
    if path.is_file():
        path.unlink()
        return True
    return False


def conversation_stats(user_id: str) -> dict:
    """Counts only -- never message text. Used by the admin usage panel."""
    conversations = list_conversations(user_id)
    return {
        "conversations": len(conversations),
        "messages": sum(item["message_count"] for item in conversations),
    }


# --------------------------------------------------------------------------
# per-user settings
# --------------------------------------------------------------------------


def _settings_path(user_id: str) -> Path:
    return user_dir(user_id) / "settings.json"


def load_user_settings(user_id: str) -> UserSettings:
    path = _settings_path(user_id)
    if not path.is_file():
        return UserSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return UserSettings()
    return UserSettings(
        base_url=data.get("base_url", ""),
        model=data.get("model", ""),
        api_key_encrypted=data.get("api_key_encrypted", ""),
    )


def save_user_settings(user_id: str, settings: UserSettings) -> None:
    ensure_user_dirs(user_id)
    _settings_path(user_id).write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# extracted data (the side panel)
# --------------------------------------------------------------------------


def extractions_dir(user_id: str) -> Path:
    return user_dir(user_id) / "extractions"


def extraction_key(file_ids: list[str]) -> str:
    """A filename-safe id for one set of files.

    Keyed on the files rather than the conversation: the extracted data
    describes the drawings, and it should still be there whether it was pulled
    up from a chat or straight from the file library.
    """
    joined = ",".join(sorted(file_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def save_extraction(user_id: str, file_ids: list[str], payload: dict) -> None:
    """Keep one extraction per file set, so a refresh does not lose it."""
    directory = extractions_dir(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "file_ids": sorted(file_ids), "saved_at": time.time()}
    (directory / f"{extraction_key(file_ids)}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_extraction(user_id: str, file_ids: list[str]) -> dict | None:
    if not file_ids:
        return None
    path = extractions_dir(user_id) / f"{extraction_key(file_ids)}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def delete_extraction(user_id: str, file_ids: list[str]) -> None:
    if not file_ids:
        return
    (extractions_dir(user_id) / f"{extraction_key(file_ids)}.json").unlink(missing_ok=True)
