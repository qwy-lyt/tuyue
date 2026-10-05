@echo off
REM 一键启动：双击即可，然后浏览器打开 http://127.0.0.1:8000
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] 首次运行，正在创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo 创建失败：请确认已安装 Python 3.11 或更高版本。
        pause
        exit /b 1
    )
    echo [2/2] 正在安装依赖，这一步比较慢，请耐心等待...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist ".env" (
    echo.
    echo 提示：还没有 .env 文件。请复制 .env.example 为 .env 并填入 API Key。
    echo.
)

echo 启动中... 浏览器访问 http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
