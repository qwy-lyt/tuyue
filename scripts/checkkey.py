"""One cheap round-trip to confirm the configured API key actually works."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import verify_credentials  # noqa: E402
from app.config import settings  # noqa: E402

print("接口地址 :", settings.base_url)
print("模型名称 :", settings.model)
print("密钥长度 :", len(settings.api_key))
print("已配置   :", settings.configured)
print()

try:
    reply = verify_credentials()
    print("模型回复 :", repr(reply))
    print("结果     : 通过，Key 可用")
except Exception as exc:
    print("结果     : 失败")
    print("异常类型 :", type(exc).__name__)
    print("异常信息 :", str(exc)[:600])
    raise SystemExit(1)
