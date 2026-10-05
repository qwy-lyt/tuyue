"""Download cloudflared, then prove it is really Cloudflare's binary.

github.com is unreachable from this network, so the download goes through a
GitHub accelerator. A third-party proxy sits between us and the file, and this
particular binary would carry every byte of tunnelled traffic -- so the download
is only accepted if its Authenticode signature is valid and issued to
Cloudflare. A tampered file fails that check and is thrown away.
"""

import subprocess
import sys
import time
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parent.parent
BIN = PROJECT / "bin"
PARTIAL = BIN / "cloudflared.exe.part"
TARGET = BIN / "cloudflared.exe"

OFFICIAL = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
            "cloudflared-windows-amd64.exe")

# Tried in order. The first is the official host; the rest are accelerators for
# when it is blocked, and every one of them has to survive the signature check.
SOURCES = [
    OFFICIAL,
    f"https://ghfast.top/{OFFICIAL}",
    f"https://gh-proxy.com/{OFFICIAL}",
]

BIN.mkdir(parents=True, exist_ok=True)


def signature_of(path: Path) -> tuple[str, str]:
    """(status, subject) from Windows' Authenticode check."""
    script = (
        f"$s = Get-AuthenticodeSignature -FilePath '{path}'; "
        "Write-Output ($s.Status.ToString() + '|' + "
        "$(if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' }))"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=120,
    )
    output = result.stdout.strip()
    if "|" not in output:
        return "Unknown", ""
    status, _, subject = output.partition("|")
    return status.strip(), subject.strip()


def download(url: str, attempt: int) -> bool:
    print(f"  源 {url[:70]}")
    try:
        done = 0
        with httpx.stream("GET", url, timeout=180, follow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            with PARTIAL.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 512):
                    handle.write(chunk)
                    done += len(chunk)
        size_mb = PARTIAL.stat().st_size / 1024 / 1024
        if total and PARTIAL.stat().st_size != total:
            print(f"     大小不符：{size_mb:.1f} MB / {total / 1024 / 1024:.1f} MB")
            return False
        print(f"     下载完成，{size_mb:.1f} MB")
        return True
    except Exception as exc:
        print(f"     失败：{type(exc).__name__} {str(exc)[:60]}")
        return False


print("下载 cloudflared（约 50 MB）")
print()

for source in SOURCES:
    for attempt in range(1, 4):
        if download(source, attempt):
            break
        time.sleep(2)
    else:
        continue

    print("  验证数字签名…")
    status, subject = signature_of(PARTIAL)
    print(f"     状态：{status}")
    print(f"     签发者：{subject[:80] if subject else '（无）'}")

    if status == "Valid" and "cloudflare" in subject.lower():
        PARTIAL.replace(TARGET)
        print()
        print(f"签名有效且属于 Cloudflare，已安装到：{TARGET}")
        sys.exit(0)

    print("     签名不符，丢弃这个文件，换下一个源。")
    PARTIAL.unlink(missing_ok=True)
    print()

PARTIAL.unlink(missing_ok=True)
print("所有来源都失败，或下载到的文件签名不正确。")
print("没有可用文件时绝不将就使用——这个程序会经手你全部的网络流量。")
sys.exit(1)
