"""视频音频提取模块。

使用 FFmpeg 命令行从视频/音频文件中提取音频，压缩为 MP3 临时文件。
FFmpeg 路径优先使用用户在「设置-关于」中配置的路径，未配置则使用环境变量。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

LoadProgressCallback = Callable[[str, float], None]  # (stage, 0.0~1.0)

VIDEO_EXTENSIONS = {
    # 常见视频容器
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".3gp", ".vob", ".mts", ".m2ts",
    ".rm", ".rmvb", ".asf", ".f4v", ".ogv",
    # 仍需 FFmpeg 的音频容器（BASS 无对应插件）
    ".dts",
}

_MP3_QUALITY = 128  # kbps
_TARGET_SAMPLE_RATE = 44100  # Hz
_CACHE_DIR_NAME = ".cache"


def _get_cache_dir() -> Path:
    """获取提取音频的存放目录（程序所在目录下的 .cache/extracted 子文件夹）。

    注意：必须放在 .cache 的子目录里，而不是 .cache 根目录。TSM 引擎加载时会调用
    clear_cache() 用 glob(".cache/*.mp3") 非递归删除 .cache 根目录下的所有 mp3——
    若提取音频直接放在 .cache 根目录，加载视频后切换引擎重载时该文件已被删除，导致
    "找不到 ffmpeg 提取的音频文件"。放到子目录可避开这次清理，使其在切换引擎/重载时
    仍然有效。
    """
    env_dir = os.environ.get("SUG_CACHE_DIR")
    if env_dir:
        cache_dir = Path(env_dir) / "extracted"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
    program_dir = Path(sys.argv[0]).resolve().parent
    cache_dir = program_dir / _CACHE_DIR_NAME / "extracted"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def is_video_file(file_path: str) -> bool:
    """判断文件扩展名是否为视频/音频容器格式。"""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def get_ffmpeg_path() -> str:
    """获取 FFmpeg 可执行文件路径。

    优先使用用户在「设置-关于」中配置的路径，若未配置则返回 'ffmpeg'（依赖环境变量）。
    """
    try:
        from strange_uta_game.frontend.settings.app_settings import AppSettings
        path = AppSettings().get("tools.ffmpeg_path", "")
        return path if path else "ffmpeg"
    except Exception:
        return "ffmpeg"


def is_ffmpeg_available() -> bool:
    """检测 FFmpeg 是否可用。

    若用户配置了路径，检测该文件是否存在；否则检测环境变量中的 ffmpeg。
    """
    ffmpeg = get_ffmpeg_path()
    if ffmpeg != "ffmpeg":
        return Path(ffmpeg).is_file()
    return shutil.which("ffmpeg") is not None


def clear_extracted_cache() -> None:
    """清空 .cache/extracted/ 下的所有提取文件。"""
    cache_dir = _get_cache_dir()
    if cache_dir.exists():
        for f in cache_dir.glob("*"):
            try:
                if f.is_file():
                    f.unlink()
            except Exception:
                pass


def extract_audio(video_path: str, progress_cb: Optional[LoadProgressCallback] = None) -> str:
    """从视频/音频文件中提取音频并压缩为 MP3 临时文件。

    Args:
        video_path: 视频文件路径
        progress_cb: 进度回调 (stage, 0.0~1.0)

    Returns:
        生成的临时 MP3 文件路径

    Raises:
        FileNotFoundError: 视频文件不存在
        RuntimeError: FFmpeg 不可用或提取失败
    """
    if not Path(video_path).is_file():
        raise FileNotFoundError(f"文件不存在: {video_path}")

    if not is_ffmpeg_available():
        raise RuntimeError(
            "当前环境未检测到 FFmpeg，请在「设置 → 关于」中配置 FFmpeg 可执行文件路径。"
        )

    clear_extracted_cache()

    video_stem = Path(video_path).stem
    cache_dir = _get_cache_dir()
    temp_path = str(cache_dir / f"{video_stem}.mp3")

    if progress_cb:
        progress_cb("正在提取音频...", 0.1)

    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-ab", f"{_MP3_QUALITY}k",
        "-ar", str(_TARGET_SAMPLE_RATE),
        temp_path,
    ]

    # Windows 下隐藏控制台窗口，避免 GUI 应用调用 FFmpeg 时闪出黑框
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=600,
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg 提取超时（超过 10 分钟）")
    except FileNotFoundError:
        raise RuntimeError(
            f"找不到 FFmpeg 可执行文件: {ffmpeg}，请在「设置 → 关于」中重新配置路径。"
        )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg 提取失败:\n{stderr[-800:]}")

    if not Path(temp_path).is_file():
        raise RuntimeError("FFmpeg 未生成输出文件，请确认视频文件包含音频流。")

    if progress_cb:
        progress_cb("音频提取完成", 1.0)

    return temp_path
