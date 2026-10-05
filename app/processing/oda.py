"""Wrapper around ODA File Converter.

DWG is a closed binary format and no Python library reads it directly. The ODA
File Converter (free, from opendesign.com) is the usual escape hatch: it is a
GUI application that also accepts a folder-to-folder batch command line.

    ODAFileConverter <in_dir> <out_dir> <version> <type> <recurse> <audit> [filter]

It only speaks in directories, so single files are staged into a scratch folder.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import settings

EXE_NAME = "ODAFileConverter.exe"
TIMEOUT_SECONDS = 180

# Newest first: a newer writer still produces files every reader understands.
OUTPUT_VERSIONS = ("ACAD2018", "ACAD2013", "ACAD2010")


def _candidate_paths() -> list[Path]:
    found: list[Path] = []

    if settings.oda_converter_path:
        configured = Path(settings.oda_converter_path)
        # Accept either the executable itself or the folder holding it.
        found.append(configured if configured.suffix.lower() == ".exe" else configured / EXE_NAME)

    roots = [
        Path(r"C:\Program Files\ODA"),
        Path(r"C:\Program Files (x86)\ODA"),
        Path.home() / "AppData" / "Local" / "ODA",
    ]
    for root in roots:
        if root.is_dir():
            # Install folders are versioned, e.g. "ODAFileConverter 25.4.0".
            found.extend(sorted(root.glob(f"*/{EXE_NAME}"), reverse=True))
            found.append(root / EXE_NAME)

    on_path = shutil.which(EXE_NAME)
    if on_path:
        found.append(Path(on_path))

    return found


def find_converter() -> Path | None:
    """Return the converter executable, or None when it is not installed."""
    for candidate in _candidate_paths():
        if candidate.is_file():
            return candidate
    return None


def is_available() -> bool:
    return find_converter() is not None


def install_hint() -> str:
    return (
        "未检测到 ODA File Converter，DWG 无法解析。"
        "请到 https://www.opendesign.com/guestfiles/oda_file_converter 免费注册下载安装，"
        "装好后重启本服务即可自动识别（也可在 .env 里用 ODA_CONVERTER_PATH 指定路径）。"
    )


def convert(source: Path, target_suffix: str = ".dxf", out_dir: Path | None = None) -> Path:
    """Convert one DWG into `target_suffix`, returning the new file's path.

    The converter only works folder-to-folder, so it runs inside a scratch
    directory that is always removed afterwards -- the result is copied out to
    `out_dir` (or a standalone temp file when that is omitted) before we clean up.

    Raises RuntimeError with a user-facing message when the converter is missing
    or produced nothing.
    """
    converter = find_converter()
    if converter is None:
        raise RuntimeError(install_hint())

    target_type = target_suffix.lstrip(".").upper()
    if target_type not in {"DXF", "DWG"}:
        raise ValueError(f"unsupported target type: {target_suffix}")

    staging = Path(tempfile.mkdtemp(prefix="oda_"))
    last_error = ""
    try:
        in_dir = staging / "in"
        out_dir_for_run = staging / "out"
        in_dir.mkdir()
        out_dir_for_run.mkdir()
        shutil.copy2(source, in_dir / source.name)

        for version in OUTPUT_VERSIONS:
            # The converter is a Qt GUI app; running it from its own directory
            # keeps it from failing to find its plugins.
            command = [
                str(converter),
                str(in_dir),
                str(out_dir_for_run),
                version,
                target_type,
                "0",  # recurse
                "1",  # audit / repair, which rescues slightly damaged files
                f"*{source.suffix}",
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(converter.parent),
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_SECONDS,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired:
                last_error = f"ODA File Converter 超时（>{TIMEOUT_SECONDS}s）"
                continue

            produced = sorted(out_dir_for_run.glob(f"*{target_suffix}"))
            if produced:
                return _place_result(produced[0], source, target_suffix, out_dir)

            last_error = (
                (completed.stderr or completed.stdout or "").strip()
                or f"返回码 {completed.returncode}"
            )
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    raise RuntimeError(f"DWG 转换失败：{last_error}")


def _place_result(produced: Path, source: Path, target_suffix: str, out_dir: Path | None) -> Path:
    """Copy the converted file somewhere the caller controls."""
    if out_dir is None:
        handle, temp_name = tempfile.mkstemp(prefix="oda_result_", suffix=target_suffix)
        os.close(handle)
        destination = Path(temp_name)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        destination = out_dir / f"{source.stem}{target_suffix}"

    shutil.copy2(produced, destination)
    return destination
