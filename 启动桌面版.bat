@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"
python -m krok_helper

if errorlevel 1 (
    echo.
    echo 启动失败，请确认已经安装 Python 3，并且 ffmpeg / ffprobe 可用。
    pause
)
