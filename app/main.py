"""FastAPI application: accounts, chat, uploads, admin, and the static UI.

Every route that touches a person's data resolves the caller from their session
cookie first and only ever reaches into that account's own directory.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import accounts, ai, providers, secrets_box, store
from .config import BASE_DIR, ensure_dirs, settings
from .processing import SUPPORTED_SUFFIXES, UnsupportedFile, dispatch
from .processing import oda

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("yue")

STATIC_DIR = BASE_DIR / "app" / "static"
SESSION_COOKIE = "yue_session"

app = FastAPI(title="图阅", version="2.0.0")


# --------------------------------------------------------------------------
# middleware
# --------------------------------------------------------------------------


@app.middleware("http")
async def harden_responses(request: Request, call_next):
    response = await call_next(request)

    # Our JavaScript is all same-origin and there is no third-party content, so
    # the policy can stay tight. Inline styles are allowed because the UI sets a
    # few style properties directly; style injection is far less dangerous than
    # script injection, which stays locked to 'self'.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"

    if request.url.path.startswith("/static") or request.url.path in ("/", "/index.html"):
        # Revalidate instead of serving stale copies; costs a 304 when unchanged.
        # The entry point is included on purpose: the HTML is what names the
        # versioned assets, so a cached copy hands the browser yesterday's UI.
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.on_event("startup")
def _startup() -> None:
    ensure_dirs()
    migrated = accounts.init_db()
    if migrated:
        logger.info("数据库已就地升级，新增字段：%s", "、".join(migrated))
    if accounts.seed_bootstrap_invite():
        logger.info("已把 .env 里的 INVITE_CODE 设为引导邀请码")
    accounts.purge_expired_sessions()

    logger.info("模型默认=%s 接口=%s", settings.model, settings.base_url)
    if oda.is_available():
        logger.info("ODA File Converter 已就绪，DWG 可完整解析")
    else:
        logger.warning("未检测到 ODA File Converter，DWG 将退化为预览图模式")
    if not settings.cookie_secure:
        logger.info("会话 Cookie 未启用 Secure 标志——仅在隧道已加密的前提下可接受")


# --------------------------------------------------------------------------
# request helpers
# --------------------------------------------------------------------------


def client_ip(request: Request) -> str:
    """The caller's address.

    Deliberately ignores X-Forwarded-For: Radmin VPN is a network-layer tunnel,
    so the TCP source address is the peer's real address, and trusting a header
    here would let anyone forge it to dodge rate limiting.
    """
    return request.client.host if request.client else "unknown"


def check_origin(request: Request) -> None:
    """Second line of defence against CSRF, behind SameSite=Strict.

    SameSite already stops the cookie riding along on a cross-site request; this
    catches anything that slips past. Requests without an Origin (curl, the test
    scripts) are allowed through.
    """
    origin = request.headers.get("origin")
    if not origin:
        return
    host = request.headers.get("host", "")
    if urlparse(origin).netloc != host:
        raise HTTPException(403, "跨站请求被拒绝")


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/",
    )


def current_user(request: Request) -> accounts.User:
    user = accounts.resolve_session(request.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        raise HTTPException(401, "请先登录")
    return user


def require_admin(user: accounts.User = Depends(current_user)) -> accounts.User:
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


def _public_user(user: accounts.User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
    }


# --------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str
    password: str
    invite_code: str


class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/api/auth/me")
def whoami(request: Request) -> dict:
    user = accounts.resolve_session(request.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        raise HTTPException(401, "未登录")
    return _public_user(user)


@app.post("/api/auth/register")
def register(payload: RegisterRequest, request: Request, response: Response) -> dict:
    check_origin(request)
    ip = client_ip(request)

    if accounts.is_throttled(payload.username, ip):
        raise HTTPException(429, "尝试次数过多，请 15 分钟后再试")

    try:
        accounts.redeem_invite(payload.invite_code)
    except accounts.AuthError as exc:
        accounts.record_failed_login(payload.username, ip)
        accounts.audit("register_rejected", detail=f"用户名={payload.username}", ip=ip)
        raise HTTPException(400, str(exc)) from exc

    try:
        user = accounts.create_user(payload.username, payload.password)
    except accounts.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc

    token = accounts.create_session(user.id)
    set_session_cookie(response, token)
    # Registering signs you in, so it counts as a login for the admin panel's
    # "last seen" column.
    accounts.touch_login(user.id)
    accounts.audit("register", actor=user.username, detail=f"id={user.id}", ip=ip)
    logger.info("新账号 %s（%s）", user.username, "管理员" if user.is_admin else "普通用户")
    return _public_user(user)


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    check_origin(request)
    ip = client_ip(request)

    if accounts.is_throttled(payload.username, ip):
        raise HTTPException(429, "尝试次数过多，请 15 分钟后再试")

    user = accounts.verify_password(payload.username, payload.password)

    # Same message whether the account is missing, disabled or the password is
    # wrong -- the response must not reveal which.
    if user is None or user.disabled:
        accounts.record_failed_login(payload.username, ip)
        accounts.audit("login_failed", detail=f"用户名={payload.username}", ip=ip)
        raise HTTPException(401, detail={"code": "bad_credentials", "message": "用户名或密码不正确"})

    accounts.clear_failed_logins(payload.username, ip)
    accounts.touch_login(user.id)
    token = accounts.create_session(user.id)
    set_session_cookie(response, token)
    accounts.audit("login", actor=user.username, ip=ip)
    return _public_user(user)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    check_origin(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    user = accounts.resolve_session(token)
    accounts.destroy_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    if user:
        accounts.audit("logout", actor=user.username, ip=client_ip(request))
    return {"ok": True}


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


@app.get("/api/status")
def status() -> dict:
    return {
        "app_name": settings.app_name,
        "model": settings.model,
        "base_url": settings.base_url,
        "oda_available": oda.is_available(),
        "oda_hint": "" if oda.is_available() else oda.install_hint(),
        "supported": sorted(SUPPORTED_SUFFIXES),
        "max_images_per_turn": settings.max_images_per_turn,
        "max_upload_mb": settings.max_upload_mb,
        "registration_open": bool(accounts.list_invites()),
    }


# --------------------------------------------------------------------------
# per-user AI settings
# --------------------------------------------------------------------------


class SettingsRequest(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str | None = None  # None keeps the stored key, "" clears it


@app.get("/api/providers")
def list_providers(user: accounts.User = Depends(current_user)) -> list[dict]:
    """The model services the settings panel offers."""
    return providers.catalog()


@app.get("/api/settings")
def read_settings(user: accounts.User = Depends(current_user)) -> dict:
    stored = store.load_user_settings(user.id)
    effective_url = stored.base_url or settings.base_url
    effective_model = stored.model or settings.model
    return {
        "base_url": stored.base_url,
        "model": stored.model,
        "has_key": stored.has_key,
        "default_base_url": settings.base_url,
        "default_model": settings.model,
        # Which entry in the picker the stored values correspond to, so the form
        # can open on the right selection. "custom" when nothing matches.
        "provider": providers.match_provider(stored.base_url or settings.base_url),
        "vision": providers.model_supports_vision(effective_url, effective_model),
    }


@app.post("/api/settings")
def write_settings(
    payload: SettingsRequest, request: Request, user: accounts.User = Depends(current_user)
) -> dict:
    check_origin(request)
    stored = store.load_user_settings(user.id)
    stored.base_url = payload.base_url.strip()
    stored.model = payload.model.strip()

    if payload.api_key is not None:
        stored.api_key_encrypted = secrets_box.encrypt(payload.api_key.strip())

    store.save_user_settings(user.id, stored)
    accounts.audit("settings_updated", actor=user.username, ip=client_ip(request))
    return {"ok": True, "has_key": stored.has_key}


@app.post("/api/settings/verify")
def verify_settings(user: accounts.User = Depends(current_user)) -> dict:
    try:
        ai.verify_credentials(user.id)
    except ai.AIConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"调用失败：{exc}") from exc
    return {"ok": True}


# --------------------------------------------------------------------------
# uploads
# --------------------------------------------------------------------------


@app.post("/api/upload")
async def upload(
    request: Request, file: UploadFile = File(...), user: accounts.User = Depends(current_user)
) -> dict:
    check_origin(request)

    original = Path(file.filename or "upload")
    suffix = original.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(400, f"不支持的文件类型：{suffix or '(无扩展名)'}")

    limit = settings.max_upload_mb * 1024 * 1024
    payload = await file.read()
    if len(payload) > limit:
        raise HTTPException(400, f"文件超过 {settings.max_upload_mb} MB 上限")
    if not payload:
        raise HTTPException(400, "上传的文件是空的")

    file_id = store.new_id()
    stored = store.save_upload(user.id, file_id, original.name, payload)
    workdir = store.file_dir(user.id, file_id)

    try:
        extracted = await run_in_threadpool(dispatch, stored, file_id, workdir)
    except UnsupportedFile as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("处理 %s 失败", original.name)
        raise HTTPException(500, f"文件处理失败：{exc}") from exc

    record = store.FileRecord(
        file_id=extracted.file_id,
        filename=extracted.filename,
        kind=extracted.kind,
        text=extracted.text,
        images=[str(p) for p in extracted.images],
        warnings=extracted.warnings,
        meta=extracted.meta,
    )
    store.save_file_record(user.id, record)
    accounts.audit("upload", actor=user.username, detail=f"{original.name}", ip=client_ip(request))
    return asdict(record)


@app.get("/api/files")
def list_files(user: accounts.User = Depends(current_user)) -> list[dict]:
    """The account's file library: everything uploaded, newest first."""
    return [store.file_summary(record) for record in store.list_file_records(user.id)]


@app.get("/api/files/{file_id}")
def file_record(file_id: str, user: accounts.User = Depends(current_user)) -> dict:
    record = store.load_file_record(user.id, file_id)
    if record is None:
        raise HTTPException(404, "文件不存在")
    return asdict(record)


@app.delete("/api/files/{file_id}")
def remove_file(
    file_id: str, request: Request, user: accounts.User = Depends(current_user)
) -> dict:
    """Take one file out of the library.

    Conversations that quoted it keep their text; the attachment simply resolves
    to nothing, which the readers already tolerate. Only this account's own file
    can be reached -- the id is looked up inside the caller's directory.
    """
    check_origin(request)
    removed = store.delete_file_record(user.id, file_id)
    if removed is None:
        raise HTTPException(404, "文件不存在")
    accounts.audit(
        "delete_file", actor=user.username, detail=removed.filename, ip=client_ip(request)
    )
    return {"ok": True, "filename": removed.filename}


@app.get("/api/files/{file_id}/images/{index}")
def file_image(file_id: str, index: int, user: accounts.User = Depends(current_user)) -> FileResponse:
    record = store.load_file_record(user.id, file_id)
    if record is None:
        raise HTTPException(404, "文件不存在")
    paths = record.image_paths
    if index < 0 or index >= len(paths) or not paths[index].is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(paths[index], media_type="image/jpeg")


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    file_ids: list[str] = Field(default_factory=list)


@app.get("/api/conversations")
def conversations(user: accounts.User = Depends(current_user)) -> list[dict]:
    return store.list_conversations(user.id)


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, user: accounts.User = Depends(current_user)) -> dict:
    found = store.load_conversation(user.id, conversation_id)
    if found is None:
        raise HTTPException(404, "对话不存在")
    # The extracted table is keyed on the files, not the conversation, so the
    # caller fetches it separately once it knows which files were discussed.
    return {
        "id": found.id,
        "title": found.title,
        "messages": [asdict(m) for m in found.messages],
    }


@app.delete("/api/conversations/{conversation_id}")
def remove_conversation(
    conversation_id: str, request: Request, user: accounts.User = Depends(current_user)
) -> dict:
    check_origin(request)
    if not store.delete_conversation(user.id, conversation_id):
        raise HTTPException(404, "对话不存在")
    # Extracted data is deliberately left alone: it describes the files, which
    # are still in the library.
    return {"ok": True}


class ExtractRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list)


@app.get("/api/extract")
def cached_extraction(
    file_ids: str = "", user: accounts.User = Depends(current_user)
) -> dict:
    """The stored result for this file set, if one was ever produced.

    Lets the panel reopen without spending another model call: the data is keyed
    on the files, so it is found again whether it was pulled up from a chat or
    straight from the file library.
    """
    ids = [item for item in file_ids.split(",") if item]
    return store.load_extraction(user.id, ids) or {}


@app.post("/api/extract")
async def extract(
    payload: ExtractRequest, request: Request, user: accounts.User = Depends(current_user)
) -> dict:
    """Pull a file set's contents out as structured rows for the side panel.

    Runs in a worker thread: it is a plain blocking call to the model, and it
    can take a while on a dense drawing. The result is stored against the file
    set so a page refresh, or simply closing the panel, does not throw it away.
    """
    check_origin(request)
    if not payload.file_ids:
        raise HTTPException(400, "没有要提取的文件")

    try:
        items, notices = await run_in_threadpool(ai.extract_items, user.id, payload.file_ids)
    except ai.AIConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("提取数据失败")
        if getattr(exc, "status_code", None) == 402:
            raise HTTPException(402, "你的 DeepSeek 账户余额不足（HTTP 402）") from exc
        raise HTTPException(500, f"调用模型失败：{exc}") from exc

    result = {"items": items, "notices": notices, "file_ids": payload.file_ids}
    store.save_extraction(user.id, payload.file_ids, result)
    return result


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(
    payload: ChatRequest, request: Request, user: accounts.User = Depends(current_user)
) -> StreamingResponse:
    check_origin(request)

    text = payload.message.strip()
    if not text and not payload.file_ids:
        raise HTTPException(400, "消息不能为空")

    found = store.load_conversation(user.id, payload.conversation_id) if payload.conversation_id else None
    if found is None:
        found = store.Conversation(id=store.new_id())
        found.title = (text or "文件分析")[:24]

    found.messages.append(store.Message(role="user", content=text, file_ids=payload.file_ids))
    store.save_conversation(user.id, found)
    conversation_id = found.id
    user_id = user.id

    async def event_stream():
        yield _sse({"type": "start", "conversation_id": conversation_id, "title": found.title})
        answer = ""
        try:
            # The OpenAI SDK is blocking; iterate it off the event loop.
            iterator = ai.stream_reply(user_id, found)
            while True:
                kind, value = await run_in_threadpool(next, iterator, ("__end__", ""))
                if kind == "__end__":
                    break
                if kind == "notice":
                    yield _sse({"type": "notice", "text": value})
                elif kind == "delta":
                    yield _sse({"type": "delta", "text": value})
                elif kind == "done":
                    answer = value
            if answer:
                found.messages.append(store.Message(role="assistant", content=answer))
                store.save_conversation(user_id, found)
            yield _sse({"type": "end"})
        except ai.AIConfigError as exc:
            yield _sse({"type": "error", "text": str(exc)})
        except Exception as exc:
            logger.exception("对话失败")
            if getattr(exc, "status_code", None) == 402:
                yield _sse(
                    {
                        "type": "error",
                        "text": "你的 DeepSeek 账户余额不足（HTTP 402）。"
                        "请到 platform.deepseek.com 充值，或在「设置」里换一个 Key。",
                    }
                )
            else:
                yield _sse({"type": "error", "text": f"调用模型失败：{exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# admin
# --------------------------------------------------------------------------


class AdminCreateUser(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class AdminPasswordReset(BaseModel):
    password: str


class AdminDisabled(BaseModel):
    disabled: bool


class AdminInvite(BaseModel):
    note: str = ""
    max_uses: int = 1  # 0 means the code never runs out
    expires_in_days: int | None = None


@app.get("/api/admin/users")
def admin_users(admin: accounts.User = Depends(require_admin)) -> list[dict]:
    rows = []
    for user in accounts.list_users():
        stats = store.conversation_stats(user.id)
        rows.append(
            {
                "id": user.id,
                "username": user.username,
                "is_admin": user.is_admin,
                "disabled": user.disabled,
                "created_at": user.created_at,
                "last_login": user.last_login,
                "storage_bytes": store.user_storage_bytes(user.id),
                "conversations": stats["conversations"],
                "messages": stats["messages"],
            }
        )
    return rows


@app.post("/api/admin/users")
def admin_create_user(
    payload: AdminCreateUser, request: Request, admin: accounts.User = Depends(require_admin)
) -> dict:
    check_origin(request)
    try:
        user = accounts.create_user(payload.username, payload.password, is_admin=payload.is_admin)
    except accounts.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    accounts.audit(
        "admin_create_user", actor=admin.username, detail=user.username, ip=client_ip(request)
    )
    return {"id": user.id, "username": user.username}


def _guard_last_admin(target: accounts.User) -> None:
    """Never leave the system without a working administrator."""
    if target.is_admin and accounts.count_admins(excluding=target.id) == 0:
        raise HTTPException(400, "这是最后一个管理员，不能停用或删除")


@app.post("/api/admin/users/{user_id}/password")
def admin_reset_password(
    user_id: str,
    payload: AdminPasswordReset,
    request: Request,
    admin: accounts.User = Depends(require_admin),
) -> dict:
    check_origin(request)
    if accounts.get_user(user_id) is None:
        raise HTTPException(404, "用户不存在")
    try:
        accounts.set_password(user_id, payload.password)
    except accounts.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    accounts.destroy_user_sessions(user_id)
    accounts.audit("admin_reset_password", actor=admin.username, detail=user_id, ip=client_ip(request))
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/disabled")
def admin_set_disabled(
    user_id: str,
    payload: AdminDisabled,
    request: Request,
    admin: accounts.User = Depends(require_admin),
) -> dict:
    check_origin(request)
    target = accounts.get_user(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    if payload.disabled:
        _guard_last_admin(target)
    accounts.set_disabled(user_id, payload.disabled)
    accounts.audit(
        "admin_disable_user" if payload.disabled else "admin_enable_user",
        actor=admin.username,
        detail=target.username,
        ip=client_ip(request),
    )
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(
    user_id: str, request: Request, admin: accounts.User = Depends(require_admin)
) -> dict:
    check_origin(request)
    target = accounts.get_user(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    _guard_last_admin(target)

    store.remove_user_data(user_id)
    accounts.delete_user(user_id)
    accounts.audit("admin_delete_user", actor=admin.username, detail=target.username, ip=client_ip(request))
    return {"ok": True}


@app.get("/api/admin/invites")
def admin_invites(admin: accounts.User = Depends(require_admin)) -> list[dict]:
    return accounts.list_invites()


@app.post("/api/admin/invites")
def admin_create_invite(
    payload: AdminInvite, request: Request, admin: accounts.User = Depends(require_admin)
) -> dict:
    check_origin(request)
    code = accounts.create_invite(
        payload.note,
        admin.username,
        # Negative values are meaningless; clamp to "unlimited" rather than
        # accidentally creating a code that can never be redeemed.
        max_uses=max(payload.max_uses, accounts.UNLIMITED_USES),
        expires_in_days=payload.expires_in_days,
    )
    accounts.audit("admin_create_invite", actor=admin.username, detail=payload.note, ip=client_ip(request))
    return {"code": code}


@app.delete("/api/admin/invites/{code}")
def admin_revoke_invite(
    code: str, request: Request, admin: accounts.User = Depends(require_admin)
) -> dict:
    check_origin(request)
    if not accounts.revoke_invite(code):
        raise HTTPException(404, "邀请码不存在")
    accounts.audit("admin_revoke_invite", actor=admin.username, detail=code, ip=client_ip(request))
    return {"ok": True}


@app.get("/api/admin/audit")
def admin_audit(limit: int = 200, admin: accounts.User = Depends(require_admin)) -> list[dict]:
    return accounts.recent_audit(limit=min(max(limit, 1), 1000))


# --------------------------------------------------------------------------
# static UI
# --------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
