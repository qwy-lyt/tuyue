"""Thin client over an OpenAI-compatible chat API (DeepSeek by default).

Two jobs: assemble the request payload from a conversation, and stream the reply
back. The interesting part is the budget -- images are inlined as base64, and the
API caps a request body at 48 MiB, so images are attached newest-first and the
oldest ones quietly fall back to their text digest.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from collections.abc import Iterator
from pathlib import Path

from openai import OpenAI

from . import secrets_box, store
from .config import settings
from .store import Conversation, FileRecord, Message, load_file_records

FILE_TEXT_BUDGET = 150_000


class AIConfigError(RuntimeError):
    """Raised when the account has no usable API key."""


def credentials_for(user_id: str) -> tuple[OpenAI, str]:
    """Build a client from the account's own stored key, and its model choice.

    Every account brings its own key -- there is deliberately no shared fallback,
    because that would let one person's usage spend another person's credit.
    """
    stored = store.load_user_settings(user_id)
    api_key = secrets_box.decrypt(stored.api_key_encrypted)
    if not api_key:
        raise AIConfigError(
            "你还没有配置 API Key。请点右上角的「设置」，填入自己的 DeepSeek Key。"
        )

    client = OpenAI(
        api_key=api_key,
        base_url=stored.base_url or settings.base_url,
        timeout=settings.request_timeout,
    )
    return client, (stored.model or settings.model)


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _file_header(record: FileRecord) -> str:
    return f"\n\n--- 文件：{record.filename} ---\n"


def _render_text_block(record: FileRecord) -> str:
    """The text half of an attachment: digest plus any warnings worth surfacing."""
    parts = [_file_header(record)]
    if record.text.strip():
        parts.append(record.text)
    if not record.text.strip():
        parts.append("（该文件没有可提取的文字内容，请参考随附的图片。）")
    if record.warnings:
        parts.append("\n[解析提示] " + "；".join(record.warnings))
    return "\n".join(parts)


def _select_images(user_id: str, messages: list[Message]) -> tuple[dict[int, list[Path]], list[str]]:
    """Decide which messages keep their images, newest turn first.

    Returns the chosen paths per message index, plus warnings for anything dropped.
    """
    budget = settings.max_images_per_turn
    chosen: dict[int, list[Path]] = {}
    dropped: list[str] = []

    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "user" or not message.file_ids:
            continue

        for record in load_file_records(user_id, message.file_ids):
            for path in record.image_paths:
                if budget > 0 and path.is_file():
                    chosen.setdefault(index, []).append(path)
                    budget -= 1
                elif path.is_file():
                    dropped.append(record.filename)

    if dropped:
        unique = sorted(set(dropped))
        return chosen, [
            f"图片数量超出单轮上限（{settings.max_images_per_turn} 张），"
            f"以下文件的图片未随本轮发送：{'、'.join(unique)}。"
            "它们的文字内容仍然可用，如需看图请单独追问。"
        ]
    return chosen, []


def build_messages(user_id: str, conversation: Conversation) -> tuple[list[dict], list[str]]:
    """Turn a stored conversation into an API payload for this account."""
    messages = conversation.messages
    images_by_message, notices = _select_images(user_id, messages)

    budget = FILE_TEXT_BUDGET
    text_blocks: dict[int, list[FileRecord]] = {}
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "user" or not message.file_ids:
            continue
        for record in load_file_records(user_id, message.file_ids):
            cost = len(record.text)
            if cost > budget:
                notices.append(
                    f"上下文长度接近上限，「{record.filename}」的文字内容未完整送出，"
                    "建议开一个新对话再上传。"
                )
                continue
            budget -= cost
            text_blocks.setdefault(index, []).append(record)

    payload: list[dict] = [{"role": "system", "content": settings.system_prompt}]

    for index, message in enumerate(messages):
        attachments = text_blocks.get(index, [])
        images = images_by_message.get(index, [])

        if message.role != "user" or (not attachments and not images):
            payload.append({"role": message.role, "content": message.content})
            continue

        content: list[dict] = []
        text = message.content
        for record in attachments:
            text += _render_text_block(record)
        if text.strip():
            content.append({"type": "text", "text": text})
        for path in images:
            content.append({"type": "image_url", "image_url": {"url": _data_url(path)}})
        payload.append({"role": "user", "content": content})

    return payload, notices


def stream_reply(user_id: str, conversation: Conversation) -> Iterator[tuple[str, str]]:
    """Yield ('delta', text) while streaming, then ('done', full_text)."""
    client, model = credentials_for(user_id)

    payload, notices = build_messages(user_id, conversation)
    for notice in notices:
        yield "notice", notice

    stream = client.chat.completions.create(
        model=model,
        messages=payload,
        stream=True,
    )

    collected: list[str] = []
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            collected.append(delta.content)
            yield "delta", delta.content

    yield "done", "".join(collected)


# --------------------------------------------------------------------------
# structured extraction
# --------------------------------------------------------------------------

EXTRACTION_CATEGORIES = ("尺寸标注", "技术要求", "图纸信息", "图层与块")

# Kept deliberately bossy. The model has to return JSON that the table can sort
# on, so anything conversational in the reply would break parsing.
_EXTRACTION_INSTRUCTION = """请仔细观察附图中的图纸，把其中可以核对的数据整理成 JSON。

只输出一个 JSON 对象。不要输出任何解释、前言、结语，也不要包在 markdown 代码块里。

格式严格如下：

{"items": [
  {"category": "尺寸标注", "name": "底板总长", "value": 60, "unit": "mm",
   "detail": "60", "note": "", "source": "文件名.pdf"}
]}

字段说明：
- category：只能是「尺寸标注」「技术要求」「图纸信息」「图层与块」这四个之一。
  · 尺寸标注 = 图纸上标出的尺寸、直径、螺纹、倒角、形位公差等
  · 技术要求 = 技术要求栏里的文字条款，逐条列出
  · 图纸信息 = 标题栏里的内容。**必须逐项去标题栏里找下面这些字段**，找到就填，
    找不到就跳过——不要因为某个字段很常见就以为已经填过了：
    零件名称 / 图号 / 材料 / 比例 / 张数 / 重量 / 表面处理 / 单位名称 /
    设计 / 校对 / 审核 / 批准 / 日期。
    注意：标题栏里不少格子是**竖排或转了 90 度**的（材料、图号、张数常这样），
    字也偏小。请把这些旋转的字也读出来，必要时在脑中把图转过来看。
    材料通常写作 Q235、45 钢、2mm厚不锈钢板、6061 这类，不要漏。
  · 图层与块 = 图层名、块名及其数量（仅 DWG/DXF 有，没有就留空）
- name：这一项是什么，用简短中文，例如「底板总长」「未注倒角」
- value：**能读出数字的填数字**（不带单位、不带公差），读不出数字的填 null。
  例如「40 +0.16/0」填 40；「M6X0.75螺纹」填 null；「2XØ5.5」填 5.5。
- unit：单位，通常是 mm；没有单位就填空字符串
- detail：图纸上的原始写法，如实照抄，例如「40 +0.16/0」
- note：需要补充说明的才填，例如「焊后加工」，否则空字符串
- source：这一项来自哪个文件，填文件名

尽量把图纸上的数据都提取出来，不要遗漏。最多 300 条。"""


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    cleaned = re.sub(r"^```[A-Za-z]*\s*", "", cleaned)
    return re.sub(r"\s*```$", "", cleaned).strip()


def _parse_items(raw: str) -> list[dict]:
    """Turn the model's reply into rows, tolerating the usual JSON sloppiness."""
    text = _strip_code_fence(raw)
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the outermost braces: models sometimes wrap the object in
        # a sentence even when told not to.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                data = None

    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("items", [])
    if not isinstance(data, list):
        return []

    rows: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("category", "")).strip()
        if category not in EXTRACTION_CATEGORIES:
            category = "图纸信息" if not category else category

        raw_value = entry.get("value")
        value: float | None
        if isinstance(raw_value, (int, float)):
            value = float(raw_value)
        else:
            # "40 +0.16/0" and friends: take the leading number if there is one.
            match = re.search(r"-?\d+(?:\.\d+)?", str(raw_value or ""))
            value = float(match.group()) if match else None

        rows.append({
            "category": category,
            "name": str(entry.get("name", "")).strip(),
            "value": value,
            "unit": str(entry.get("unit", "") or "").strip(),
            "detail": str(entry.get("detail", "") or "").strip(),
            "note": str(entry.get("note", "") or "").strip(),
            "source": str(entry.get("source", "") or "").strip(),
        })
    return rows[:300]


def extract_items(user_id: str, file_ids: list[str]) -> tuple[list[dict], list[str]]:
    """Ask the model to return one file set's contents as sortable rows.

    A second, separate call rather than a post-processing step on the chat
    answer: the answer is prose meant for a person, and prose is a poor input
    for a table.
    """
    client, model = credentials_for(user_id)

    budget = settings.max_images_per_turn
    records = store.load_file_records(user_id, file_ids)
    if not records:
        raise AIConfigError("找不到要提取的文件")

    notices: list[str] = []
    if len(records) > budget:
        notices.append(
            f"本轮有 {len(records)} 个文件，只提取了前 {budget} 个的图片内容。"
        )

    text = _EXTRACTION_INSTRUCTION
    for record in records[:budget]:
        text += _render_text_block(record)

    content: list[dict] = [{"type": "text", "text": text}]
    for record in records[:budget]:
        for path in record.image_paths:
            if budget <= 0:
                break
            if path.is_file():
                content.append({"type": "image_url", "image_url": {"url": _data_url(path)}})
                budget -= 1

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一个图纸数据提取工具，只输出 JSON。"},
            {"role": "user", "content": content},
        ],
        stream=False,
    )
    raw = response.choices[0].message.content or ""
    items = _parse_items(raw)
    if not items:
        notices.append("模型没有返回可解析的数据，可能是图纸内容过少或格式不符合要求。")
    return items, notices


def verify_credentials(user_id: str) -> str:
    """One cheap round-trip so the settings panel can confirm a key works."""
    client, model = credentials_for(user_id)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=4,
    )
    return response.choices[0].message.content or ""
