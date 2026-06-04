from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from krok_helper.errors import ExportCancelled, ProcessingError
from krok_helper.models import MediaInfo
from krok_helper.types import Logger


def _build_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _find_tool_in_dir(directory: Path, tool_name: str) -> str | None:
    candidates = [
        directory / tool_name,
        directory / "bin" / tool_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def find_tool(tool_name: str, ffmpeg_dir: Path | None = None) -> str:
    resolved = shutil.which(tool_name)
    if resolved:
        return resolved

    if ffmpeg_dir is not None:
        candidate = _find_tool_in_dir(ffmpeg_dir, tool_name)
        if candidate:
            return candidate

    raise ProcessingError(
        f"找不到 {tool_name}。请先确认系统环境变量 PATH 中可用，"
        "或者在界面里选择 ffmpeg 所在文件夹。"
    )


def describe_tool_source(tool_path: str, ffmpeg_dir: Path | None = None) -> str:
    resolved = Path(tool_path).resolve()

    if ffmpeg_dir is not None:
        try:
            ffmpeg_dir_resolved = ffmpeg_dir.resolve()
            if resolved.is_relative_to(ffmpeg_dir_resolved):
                return f"FFmpeg 来源: 所选目录 {ffmpeg_dir_resolved}"
        except Exception:
            pass

    return "FFmpeg 来源: 系统环境变量 PATH"


def probe_media(ffprobe_path: str, media_path: Path) -> MediaInfo:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(media_path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **_build_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise ProcessingError(f"无法读取媒体信息: {media_path.name}\n{result.stderr.strip()}")

    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams", [])
    format_info = payload.get("format", {})

    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]

    duration_raw = format_info.get("duration")
    duration = float(duration_raw) if duration_raw not in (None, "N/A", "") else 0.0

    sample_rate = None
    channels = None
    if audio_streams:
        first_audio = audio_streams[0]
        sample_rate_raw = first_audio.get("sample_rate")
        channels_raw = first_audio.get("channels")
        sample_rate = int(sample_rate_raw) if sample_rate_raw not in (None, "N/A", "") else None
        channels = int(channels_raw) if channels_raw not in (None, "N/A", "") else None

    return MediaInfo(
        path=media_path,
        duration=duration,
        video_streams=len(video_streams),
        audio_streams=len(audio_streams),
        subtitle_streams=len(subtitle_streams),
        sample_rate=sample_rate,
        channels=channels,
    )


def terminate_process(process: subprocess.Popen, *, timeout: float = 1.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def run_command(
    command: list[str],
    logger: Logger,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_process_started: Callable[[subprocess.Popen | None], None] | None = None,
) -> None:
    logger("执行命令:")
    logger(" ".join(f'"{part}"' if " " in part else part for part in command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_build_subprocess_kwargs(),
    )
    if on_process_started is not None:
        on_process_started(process)

    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                logger(line)
            if should_cancel is not None and should_cancel() and process.poll() is None:
                terminate_process(process)

        return_code = process.wait()
    finally:
        if on_process_started is not None:
            on_process_started(None)

    if should_cancel is not None and should_cancel():
        raise ExportCancelled("已停止导出。")
    if return_code != 0:
        raise ProcessingError(f"ffmpeg 执行失败，退出码: {return_code}")
