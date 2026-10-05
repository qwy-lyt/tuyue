"""展示模式：启动服务并开一条临时公网隧道，把网址打印出来。

    .venv\\Scripts\\python.exe demo.py

关掉这个窗口（或按 Ctrl+C）隧道就断了，公网上再也访问不到——平时的暴露面积为零。

需要一个 cloudflared.exe。没有的话脚本会提示去哪里拿；用免费的 quick tunnel
不需要注册账号、不需要域名，代价是每次启动网址都不同。
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
CLOUDFLARED = PROJECT / "bin" / "cloudflared.exe"
LOCAL = "http://127.0.0.1:8000"

URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def already_running() -> bool:
    try:
        return httpx.get(f"{LOCAL}/api/status", timeout=3).status_code == 200
    except Exception:
        return False


def start_server() -> subprocess.Popen | None:
    print("启动后端服务…")
    process = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(PROJECT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(60):
        if already_running():
            print("  服务已就绪")
            return process
        time.sleep(0.5)
    print("  服务启动失败，请单独跑一次 run.bat 看看报错")
    process.terminate()
    return None


def main() -> int:
    if not CLOUDFLARED.is_file():
        print(f"没有找到 {CLOUDFLARED}")
        print()
        print("下载方式（项目里的脚本会自动验证数字签名）：")
        print("  .venv\\Scripts\\python.exe scripts\\_install_cloudflared.py")
        return 1

    started_server = None
    if already_running():
        print("后端已在运行，直接开隧道。")
    else:
        started_server = start_server()
        if started_server is None:
            return 1

    print()
    print("开隧道（首次握手大约需要 5-20 秒）…")

    tunnel = subprocess.Popen(
        [str(CLOUDFLARED), "tunnel", "--url", LOCAL],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    public_url: str | None = None
    deadline = time.time() + 60

    while time.time() < deadline:
        line = tunnel.stdout.readline()
        if not line:
            if tunnel.poll() is not None:
                print("cloudflared 退出了，可能是网络连不上 Cloudflare。")
                print("把上面几行输出发给开发者看看。")
                return 1
            continue
        match = URL_PATTERN.search(line)
        if match:
            public_url = match.group()
            break
        # Anything else cloudflared says is diagnostic noise unless it fails.
        if "error" in line.lower() or "failed" in line.lower():
            print(f"  {line.strip()[:110]}")

    if public_url is None:
        print("等了一分钟还没拿到公网地址。")
        print("多半是 Cloudflare 在这条网络上连不通，或者是被限速了。")
        tunnel.terminate()
        return 1

    print()
    print("=" * 66)
    print()
    print(f"  公网网址：{public_url}")
    print()
    print("  把它发给要看的人即可。")
    print("  关掉本窗口（或按 Ctrl+C）隧道立即断开，网址随即失效。")
    print()
    print("  提醒：拿到这个网址的任何人现在都能打开你的站点。")
    print("       展示完请及时关闭。")
    print()
    print("=" * 66)
    print()

    # Keep relaying cloudflared's output so problems stay visible, until Ctrl+C.
    def relay() -> None:
        for line in tunnel.stdout:
            stripped = line.strip()
            if stripped and "trycloudflare.com" not in stripped:
                print(f"  [隧道] {stripped[:110]}")

    threading.Thread(target=relay, daemon=True).start()

    try:
        tunnel.wait()
    except KeyboardInterrupt:
        print("\n关闭隧道…")
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=10)
        except subprocess.TimeoutExpired:
            tunnel.kill()
        if started_server is not None:
            print("关闭本次启动的后端服务…")
            started_server.terminate()
        print("已停止。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
