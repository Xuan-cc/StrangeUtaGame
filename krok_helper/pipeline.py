from __future__ import annotations

import subprocess
from pathlib import Path
from string import Formatter
from tempfile import TemporaryDirectory
from typing import Callable

from krok_helper.config import DURATION_WARNING_SECONDS, MIN_HIRES_SAMPLE_RATE
from krok_helper.errors import ExportCancelled, ProcessingError
from krok_helper.ffmpeg import describe_tool_source, find_tool, probe_media, run_command
from krok_helper.models import MediaInfo
from krok_helper.types import Logger


DEFAULT_AUDIO_TITLE_TEMPLATE = "Hi-Res Audio (FLAC 32bit/{sample_rate}Hz)"
OUTPUT_NAME_MODE_FIXED = "fixed"
OUTPUT_NAME_MODE_TEMPLATE = "template"
OUTPUT_NAME_MODE_VIDEO_NAME = "video_name"
DEFAULT_ON_NAME_TEMPLATE = "{video_name}_on"
DEFAULT_OFF_NAME_TEMPLATE = "{video_name}_off"
SUPPORTED_TEMPLATE_FIELDS = {"video_name"}
WINDOWS_INVALID_FILENAME_CHARS = '<>:"/\\|?*'
FORMATTER = Formatter()


def format_duration(seconds: float) -> str:
    seconds = max(0, seconds)
    whole = int(seconds)
    milliseconds = int(round((seconds - whole) * 1000))
    minutes, sec = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{sec:02d}.{milliseconds:03d}"


def log_media_summary(logger: Logger, label: str, info: MediaInfo) -> None:
    parts = [
        f"{label}: {info.path.name}",
        f"时长 {format_duration(info.duration)}",
        f"视频流 {info.video_streams}",
        f"音频流 {info.audio_streams}",
        f"字幕流 {info.subtitle_streams}",
    ]
    if info.sample_rate:
        parts.append(f"采样率 {info.sample_rate}Hz")
    if info.channels:
        parts.append(f"声道 {info.channels}")
    logger(" | ".join(parts))


def warn_duration_mismatch(
    logger: Logger,
    video_info: MediaInfo,
    audio_info: MediaInfo,
    label: str,
) -> None:
    delta = abs(video_info.duration - audio_info.duration)
    if delta > DURATION_WARNING_SECONDS:
        logger(
            f"警告: {label} 与字幕视频的时长相差 {delta:.2f} 秒，"
            "程序会继续处理，但建议你确认素材是否对齐。"
        )


def log_audio_format_mismatch(
    logger: Logger,
    on_vocal_info: MediaInfo | None,
    off_vocal_info: MediaInfo | None,
) -> None:
    if on_vocal_info is None or off_vocal_info is None:
        return

    if on_vocal_info.path.suffix.lower() == off_vocal_info.path.suffix.lower():
        return

    logger("检测到原唱和伴奏的文件格式不一致，将先分别标准化为临时 FLAC，再进行封装。")


def validate_output_name_template(template: str, label: str) -> str:
    normalized = template.strip()
    if normalized.lower().endswith(".mkv"):
        normalized = normalized[:-4].rstrip()

    if not normalized:
        raise ProcessingError(f"{label} 输出模板不能为空。")

    if "/" in normalized or "\\" in normalized:
        raise ProcessingError(f"{label} 输出模板不能包含路径分隔符。")

    for _, field_name, _, _ in FORMATTER.parse(normalized):
        if field_name and field_name not in SUPPORTED_TEMPLATE_FIELDS:
            raise ProcessingError(
                f"{label} 输出模板包含不支持的占位符: {field_name}。"
                "当前只支持 {video_name}。"
            )

    return normalized


def render_output_stem(template: str, video_path: Path, label: str) -> str:
    normalized = validate_output_name_template(template, label)
    try:
        rendered = normalized.format(video_name=video_path.stem).strip()
    except Exception as exc:  # noqa: BLE001
        raise ProcessingError(f"{label} 输出模板无法生成文件名: {exc}") from exc

    rendered = rendered.rstrip(". ")
    if not rendered:
        raise ProcessingError(f"{label} 输出模板生成的文件名为空。")

    invalid_chars = sorted({char for char in rendered if char in WINDOWS_INVALID_FILENAME_CHARS})
    if invalid_chars:
        joined = " ".join(invalid_chars)
        raise ProcessingError(f"{label} 输出文件名包含非法字符: {joined}")

    return rendered


def build_audio_normalization_command(
    ffmpeg_path: str,
    audio_path: Path,
    output_path: Path,
    sample_rate: int,
) -> list[str]:
    return [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-i",
        str(audio_path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-c:a",
        "flac",
        "-compression_level",
        "12",
        "-ar",
        str(sample_rate),
        "-sample_fmt",
        "s32",
        "-ac",
        "2",
        str(output_path),
    ]


def build_mux_command(
    ffmpeg_path: str,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    audio_title: str,
) -> list[str]:
    return [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0",
        "-map",
        "-0:a",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:s",
        "copy",
        "-c:d",
        "copy",
        "-c:t",
        "copy",
        "-c:a",
        "copy",
        "-map_metadata",
        "-1",
        "-metadata:s:a:0",
        f"title={audio_title}",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def normalize_audio(
    ffmpeg_path: str,
    logger: Logger,
    audio_info: MediaInfo,
    output_path: Path,
    label: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_process_started: Callable[[subprocess.Popen | None], None] | None = None,
) -> int:
    if should_cancel is not None and should_cancel():
        raise ExportCancelled("生成已取消。")
    target_sample_rate = max(audio_info.sample_rate or 0, MIN_HIRES_SAMPLE_RATE)
    logger(f"开始预处理 {label}: 统一为 Hi-Res FLAC 32bit / {target_sample_rate}Hz / 2ch")

    command = build_audio_normalization_command(
        ffmpeg_path=ffmpeg_path,
        audio_path=audio_info.path,
        output_path=output_path,
        sample_rate=target_sample_rate,
    )
    try:
        run_command(command, logger, should_cancel=should_cancel, on_process_started=on_process_started)
    except ExportCancelled:
        raise
    except ProcessingError as exc:
        raise ProcessingError(f"{label} 预处理失败: {audio_info.path.name}\n{exc}") from exc

    if should_cancel is not None and should_cancel():
        raise ExportCancelled("生成已取消。")
    logger(f"{label} 预处理完成: {output_path.name}")
    return target_sample_rate


def mux_output(
    ffmpeg_path: str,
    logger: Logger,
    video_info: MediaInfo,
    normalized_audio_path: Path,
    output_path: Path,
    label: str,
    sample_rate: int,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_process_started: Callable[[subprocess.Popen | None], None] | None = None,
) -> Path:
    if should_cancel is not None and should_cancel():
        raise ExportCancelled("生成已取消。")
    logger(f"开始封装 {label}: 写入标准化音频流")

    command = build_mux_command(
        ffmpeg_path=ffmpeg_path,
        video_path=video_info.path,
        audio_path=normalized_audio_path,
        output_path=output_path,
        audio_title=DEFAULT_AUDIO_TITLE_TEMPLATE.format(sample_rate=sample_rate),
    )
    try:
        run_command(command, logger, should_cancel=should_cancel, on_process_started=on_process_started)
    except ExportCancelled:
        raise
    except ProcessingError as exc:
        raise ProcessingError(f"{label} 封装失败: {output_path.name}\n{exc}") from exc

    if should_cancel is not None and should_cancel():
        raise ExportCancelled("生成已取消。")
    logger(f"生成完成: {output_path.name}")
    return output_path


def process_output(
    ffmpeg_path: str,
    logger: Logger,
    video_info: MediaInfo,
    audio_info: MediaInfo,
    output_path: Path,
    temp_audio_path: Path,
    label: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_process_started: Callable[[subprocess.Popen | None], None] | None = None,
) -> Path:
    target_sample_rate = normalize_audio(
        ffmpeg_path=ffmpeg_path,
        logger=logger,
        audio_info=audio_info,
        output_path=temp_audio_path,
        label=label,
        should_cancel=should_cancel,
        on_process_started=on_process_started,
    )
    return mux_output(
        ffmpeg_path=ffmpeg_path,
        logger=logger,
        video_info=video_info,
        normalized_audio_path=temp_audio_path,
        output_path=output_path,
        label=label,
        sample_rate=target_sample_rate,
        should_cancel=should_cancel,
        on_process_started=on_process_started,
    )


def resolve_output_dir(video_path: Path, output_dir: Path | None = None) -> Path:
    return output_dir if output_dir is not None else video_path.parent


def resolve_output_paths(
    video_path: Path,
    output_dir: Path,
    output_name_mode: str,
    on_name_template: str | None = None,
    off_name_template: str | None = None,
    *,
    include_on: bool = True,
    include_off: bool = True,
) -> tuple[Path | None, Path | None]:
    if not include_on and not include_off:
        raise ProcessingError("至少需要生成原唱或伴奏中的一个输出文件。")
    if output_name_mode == OUTPUT_NAME_MODE_FIXED:
        return (
            output_dir / "on_vocal.mkv" if include_on else None,
            output_dir / "off_vocal.mkv" if include_off else None,
        )

    if output_name_mode == OUTPUT_NAME_MODE_VIDEO_NAME:
        on_name_template = DEFAULT_ON_NAME_TEMPLATE
        off_name_template = DEFAULT_OFF_NAME_TEMPLATE
        output_name_mode = OUTPUT_NAME_MODE_TEMPLATE

    if output_name_mode == OUTPUT_NAME_MODE_TEMPLATE:
        on_output: Path | None = None
        off_output: Path | None = None

        if include_on:
            on_template = on_name_template or DEFAULT_ON_NAME_TEMPLATE
            on_stem = render_output_stem(on_template, video_path, "原唱")
            on_output = output_dir / f"{on_stem}.mkv"

        if include_off:
            off_template = off_name_template or DEFAULT_OFF_NAME_TEMPLATE
            off_stem = render_output_stem(off_template, video_path, "伴奏")
            off_output = output_dir / f"{off_stem}.mkv"

        return on_output, off_output

    raise ProcessingError(f"不支持的输出命名模式: {output_name_mode}")


def run_pipeline(
    video_path: Path,
    on_vocal_path: Path | None,
    off_vocal_path: Path | None,
    output_dir: Path | None,
    ffmpeg_dir: Path | None,
    output_name_mode: str,
    on_name_template: str | None,
    off_name_template: str | None,
    logger: Logger,
    should_cancel: Callable[[], bool] | None = None,
    on_process_started: Callable[[subprocess.Popen | None], None] | None = None,
) -> list[Path]:
    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffprobe_path = find_tool("ffprobe.exe", ffmpeg_dir)

    logger(f"FFmpeg: {ffmpeg_path}")
    logger(f"FFprobe: {ffprobe_path}")
    logger(describe_tool_source(ffmpeg_path, ffmpeg_dir))
    logger("正在分析输入文件...")
    if should_cancel is not None and should_cancel():
        raise ExportCancelled("生成已取消。")

    if on_vocal_path is None and off_vocal_path is None:
        raise ProcessingError("至少需要提供原唱音频或伴奏音频中的一个。")

    video_info = probe_media(ffprobe_path, video_path)
    on_vocal_info = probe_media(ffprobe_path, on_vocal_path) if on_vocal_path is not None else None
    off_vocal_info = probe_media(ffprobe_path, off_vocal_path) if off_vocal_path is not None else None

    if video_info.video_streams == 0:
        raise ProcessingError("字幕视频里没有检测到视频流。")
    if on_vocal_info is not None and on_vocal_info.audio_streams == 0:
        raise ProcessingError("原唱无损文件里没有检测到音频流。")
    if off_vocal_info is not None and off_vocal_info.audio_streams == 0:
        raise ProcessingError("伴奏无损文件里没有检测到音频流。")

    log_media_summary(logger, "字幕视频", video_info)
    if on_vocal_info is not None:
        log_media_summary(logger, "原唱无损", on_vocal_info)
    if off_vocal_info is not None:
        log_media_summary(logger, "伴奏无损", off_vocal_info)
    log_audio_format_mismatch(logger, on_vocal_info, off_vocal_info)

    if on_vocal_info is not None:
        warn_duration_mismatch(logger, video_info, on_vocal_info, "原唱无损")
    if off_vocal_info is not None:
        warn_duration_mismatch(logger, video_info, off_vocal_info, "伴奏无损")
    if should_cancel is not None and should_cancel():
        raise ExportCancelled("生成已取消。")

    output_dir = resolve_output_dir(video_path, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    on_output, off_output = resolve_output_paths(
        video_path,
        output_dir,
        output_name_mode,
        on_name_template=on_name_template,
        off_name_template=off_name_template,
        include_on=on_vocal_info is not None,
        include_off=off_vocal_info is not None,
    )
    logger(f"输出命名模式: {output_name_mode}")
    target_names = [path.name for path in (on_output, off_output) if path is not None]
    logger(f"目标文件名: {' / '.join(target_names)}")

    with TemporaryDirectory(prefix="krok-helper-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        outputs: list[Path] = []

        if on_vocal_info is not None and on_output is not None:
            outputs.append(
                process_output(
                    ffmpeg_path,
                    logger,
                    video_info,
                    on_vocal_info,
                    on_output,
                    temp_dir / "on_vocal.normalized.flac",
                    "On Vocal",
                    should_cancel=should_cancel,
                    on_process_started=on_process_started,
                )
            )

        if off_vocal_info is not None and off_output is not None:
            outputs.append(
                process_output(
                    ffmpeg_path,
                    logger,
                    video_info,
                    off_vocal_info,
                    off_output,
                    temp_dir / "off_vocal.normalized.flac",
                    "Off Vocal",
                    should_cancel=should_cancel,
                    on_process_started=on_process_started,
                )
            )

    if should_cancel is not None and should_cancel():
        raise ExportCancelled("生成已取消。")
    logger(f"输出目录: {output_dir}")
    logger("全部处理完成。")
    return outputs
