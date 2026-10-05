"""The model services a user can pick from in the settings panel.

Purely a convenience: every provider here speaks the OpenAI Chat Completions
format, so the picker only fills in a base URL and a model id. Anyone can still
type their own values instead -- see the "custom" entry at the end.

`vision` records whether the model accepts images. This app renders PDF pages
and CAD drawings to pictures, so a model without it can still answer from the
extracted text but will never see the drawing itself. The settings panel warns
about that rather than silently producing worse answers.
"""

from __future__ import annotations

PROVIDERS: list[dict] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "key_url": "https://platform.deepseek.com/api_keys",
        "note": "本项目的默认服务，价格便宜，图纸识别效果好。",
        "models": [
            {"id": "deepseek-flash", "name": "deepseek-flash", "vision": True},
            {"id": "deepseek-v4-pro", "name": "deepseek-v4-pro", "vision": True},
        ],
    },
    {
        "id": "xiaomi-mimo",
        "name": "小米 MiMo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "key_url": "https://mimo.mi.com",
        "note": "小米开放平台。2.5 / 2.6 系列原生支持图像输入。",
        "models": [
            {"id": "mimo-v2.5", "name": "mimo-v2.5", "vision": True},
            {"id": "mimo-v2.5-pro", "name": "mimo-v2.5-pro", "vision": True},
            {"id": "mimo-v2.6-pro", "name": "mimo-v2.6-pro", "vision": True},
            {"id": "mimo-v2.6-flash", "name": "mimo-v2.6-flash", "vision": True},
            {"id": "mimo-v2-omni", "name": "mimo-v2-omni", "vision": True},
        ],
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "note": "国内可直连。",
        "models": [
            {"id": "glm-5.3", "name": "glm-5.3", "vision": True},
            {"id": "glm-5.3-flash", "name": "glm-5.3-flash", "vision": True},
        ],
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "key_url": "https://platform.openai.com/api-keys",
        "note": "需要可用的网络环境。",
        "models": [
            {"id": "gpt-4o", "name": "gpt-4o", "vision": True},
            {"id": "gpt-4o-mini", "name": "gpt-4o-mini", "vision": True},
        ],
    },
    {
        "id": "custom",
        "name": "自定义",
        "base_url": "",
        "key_url": "",
        "note": "任何 OpenAI 兼容的接口都可以，自己填地址和模型名。",
        "models": [],
    },
]


def catalog() -> list[dict]:
    return PROVIDERS


def match_provider(base_url: str) -> str:
    """Which provider a stored base URL belongs to, or "custom" if none match.

    Used so the picker shows the right selection when the settings panel opens;
    matching is on the normalised URL so a trailing slash does not matter.
    """
    target = (base_url or "").strip().rstrip("/").lower()
    if not target:
        return "custom"
    for provider in PROVIDERS:
        candidate = provider["base_url"].rstrip("/").lower()
        if candidate and candidate == target:
            return provider["id"]
    return "custom"


def find_model(provider_id: str, model_id: str) -> dict | None:
    for provider in PROVIDERS:
        if provider["id"] != provider_id:
            continue
        for model in provider["models"]:
            if model["id"] == model_id:
                return model
    return None


def model_supports_vision(base_url: str, model_id: str) -> bool | None:
    """True/False when we recognise the model, None when we do not."""
    found = find_model(match_provider(base_url), model_id)
    return None if found is None else bool(found["vision"])
