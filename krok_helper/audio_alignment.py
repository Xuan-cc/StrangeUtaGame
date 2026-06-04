from __future__ import annotations

import array
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from krok_helper.errors import ExportCancelled, ProcessingError
from krok_helper.ffmpeg import _build_subprocess_kwargs, find_tool, probe_media, run_command
from krok_helper.types import Logger


WAVEFORM_SAMPLE_RATE = 8_000
WAVEFORM_PEAKS_PER_SECOND = 80
AUTO_ALIGN_SEARCH_SECONDS = 6.0
ALIGNED_VIDEO_EXTENSION = ".mp4"
DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE = "{video_name}_aligned"
DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE = "{audio_name}_aligned"
ENCODE_MODE_SOFTWARE = "software"
ENCODE_MODE_HARDWARE = "hardware"
LEAD_FILL_BLACK = "black"
LEAD_FILL_WHITE = "white"
LEAD_FILL_FREEZE = "freeze"
FORCED_OUTPUT_WIDTH = 1920
FORCED_OUTPUT_HEIGHT = 1080
FORCED_OUTPUT_FPS = "60"
COMMON_VIDEO_ENCODERS = {
    "h264": "libx264",
    "hevc": "libx265",
    "mpeg4": "mpeg4",
    "vp8": "libvpx",
    "vp9": "libvpx-vp9",
}
NVENC_VIDEO_ENCODERS = {
    "h264": "h264_nvenc",
    "hevc": "hevc_nvenc",
}
COMMON_AUDIO_ENCODERS = {
    "aac": "aac",
    "flac": "flac",
    "mp3": "libmp3lame",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
    "pcm_s32le": "pcm_s32le",
}


def _lead_fill_label(mode: str) -> str:
    if mode == LEAD_FILL_WHITE:
        return "前白"
    if mode == LEAD_FILL_FREEZE:
        return "首帧定格"
    return "前黑"


@dataclass
class WaveformData:
    path: Path
    duration: float
    peaks_per_second: int
    peaks: list[float]


@dataclass
class AutoAlignResult:
    target_offset_seconds: float
    media_offset_seconds: float
    confidence: float
    score: float
    second_score: float
    overlap_seconds: float
    search_seconds: float


@dataclass
class AlignmentPreviewProcess:
    ffmpeg_process: subprocess.Popen
    ffplay_process: subprocess.Popen

    def is_running(self) -> bool:
        return self.ffplay_process.poll() is None

    def stop(self) -> None:
        for process in (self.ffplay_process, self.ffmpeg_process):
            if process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


def format_offset(seconds: float) -> str:
    sign = "+" if seconds >= 0 else "-"
    return f"{sign}{abs(seconds):.3f}s"


def default_aligned_video_path(video_path: Path) -> Path:
    stem = DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE.format(video_name=video_path.stem)
    return video_path.with_name(f"{stem}{ALIGNED_VIDEO_EXTENSION}")


def default_aligned_audio_path(audio_path: Path) -> Path:
    stem = DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE.format(audio_name=audio_path.stem)
    return audio_path.with_name(f"{stem}.wav")


def _build_waveform_command(ffmpeg_path: str, media_path: Path) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(media_path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        str(WAVEFORM_SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]


def _format_seconds_for_ffmpeg(seconds: float) -> str:
    text = f"{max(0.0, seconds):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _preview_input_args(media_path: Path, timeline_offset: float, preview_start: float) -> tuple[list[str], float]:
    source_start = max(0.0, preview_start - timeline_offset)
    preview_delay = max(0.0, timeline_offset - preview_start)
    args: list[str] = []
    if source_start > 0.001:
        args.extend(["-ss", _format_seconds_for_ffmpeg(source_start)])
    args.extend(["-i", str(media_path)])
    return args, preview_delay


def _preview_filter(input_index: int, preview_delay: float, label: str) -> str:
    filters = [f"[{input_index}:a:0]asetpts=PTS-STARTPTS"]
    if preview_delay > 0.001:
        delay_ms = max(0, int(round(preview_delay * 1000)))
        filters.append(f"adelay={delay_ms}:all=1")
    return ",".join(filters) + f"[{label}]"


def _probe_payload(ffprobe_path: str, media_path: Path) -> dict:
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
        raise ProcessingError(f"无法读取媒体参数: {media_path.name}\n{result.stderr.strip()}")
    return json.loads(result.stdout or "{}")


def _first_video_stream(payload: dict) -> dict:
    for stream in payload.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    raise ProcessingError("源视频里没有检测到视频流。")


def _audio_streams(payload: dict) -> list[dict]:
    return [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"]


def _subtitle_streams(payload: dict) -> list[dict]:
    return [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "subtitle"]


def _stream_count(payload: dict, codec_type: str) -> int:
    return sum(1 for stream in payload.get("streams", []) if stream.get("codec_type") == codec_type)


def _duration_from_payload(payload: dict) -> float | None:
    format_info = payload.get("format", {})
    raw_duration = format_info.get("duration")
    if raw_duration not in (None, "", "N/A"):
        try:
            duration = float(raw_duration)
            if duration > 0:
                return duration
        except (TypeError, ValueError):
            pass

    durations: list[float] = []
    for stream in payload.get("streams", []):
        raw_stream_duration = stream.get("duration")
        if raw_stream_duration in (None, "", "N/A"):
            continue
        try:
            duration = float(raw_stream_duration)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            durations.append(duration)

    if durations:
        return max(durations)
    return None


def _parse_fraction(raw_value: str | None, fallback: str = "30") -> str:
    if not raw_value or raw_value == "0/0":
        return fallback
    return raw_value


def _channel_layout(audio_stream: dict) -> str:
    layout = str(audio_stream.get("channel_layout") or "").strip()
    if layout and layout != "unknown":
        return layout

    channels_raw = audio_stream.get("channels")
    try:
        channels = int(channels_raw)
    except (TypeError, ValueError):
        channels = 2
    if channels == 1:
        return "mono"
    if channels == 2:
        return "stereo"
    return f"{channels}c"


def _video_encoding_options(video_stream: dict, encode_mode: str = ENCODE_MODE_SOFTWARE) -> list[str]:
    video_codec = str(video_stream.get("codec_name") or "h264")
    if encode_mode == ENCODE_MODE_HARDWARE:
        return [
            "-c:v",
            NVENC_VIDEO_ENCODERS.get(video_codec, "h264_nvenc"),
            "-preset",
            "p1",
            "-rc",
            "constqp",
            "-qp",
            "18",
        ]

    video_encoder = COMMON_VIDEO_ENCODERS.get(video_codec, "libx264")
    options = ["-c:v", video_encoder]

    if video_encoder == "libx264":
        profile = str(video_stream.get("profile") or "").strip().lower()
        if profile and "baseline" not in profile:
            options.extend(["-profile:v", profile.replace(" ", "")])
        options.extend(["-preset", "veryfast", "-crf", "18"])
    elif video_encoder == "libx265":
        options.extend(["-preset", "veryfast", "-crf", "23"])

    return options


def _audio_encoding_options(audio_stream: dict, stream_index: int | None = None) -> list[str]:
    audio_codec = str(audio_stream.get("codec_name") or "aac")
    audio_encoder = COMMON_AUDIO_ENCODERS.get(audio_codec, "aac")
    suffix = "" if stream_index is None else f":a:{stream_index}"
    options = [f"-c:a{suffix}", audio_encoder]

    sample_rate = audio_stream.get("sample_rate")
    if sample_rate:
        options.extend([f"-ar:a{suffix}", str(sample_rate)])
    channels = audio_stream.get("channels")
    if channels:
        options.extend([f"-ac:a{suffix}", str(channels)])
    bit_rate = audio_stream.get("bit_rate")
    if bit_rate and audio_encoder in {"aac", "libmp3lame", "libopus", "libvorbis"}:
        options.extend([f"-b:a{suffix}", str(bit_rate)])

    return options


def _samples_from_pcm(raw: bytes) -> array.array:
    samples = array.array("h")
    if raw:
        samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _build_peaks(samples: array.array, window_size: int) -> list[float]:
    peaks: list[float] = []
    max_sample = 32768.0
    for start in range(0, len(samples), window_size):
        window = samples[start : start + window_size]
        peak = max((abs(value) for value in window), default=0)
        peaks.append(min(1.0, peak / max_sample))
    return peaks


def _smooth_values(values: list[float], radius: int = 2) -> list[float]:
    if not values:
        return []
    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        smoothed.append(sum(values[start:end]) / max(1, end - start))
    return smoothed


def _normalize_envelope(values: list[float]) -> list[float]:
    smoothed = _smooth_values(values)
    if not smoothed:
        return []

    mean = sum(smoothed) / len(smoothed)
    centered = [value - mean for value in smoothed]
    energy = sum(value * value for value in centered)
    if energy <= 1e-9:
        return []
    scale = energy ** 0.5
    return [value / scale for value in centered]


def _correlation_score(reference: list[float], target: list[float], offset_steps: int) -> tuple[float, int]:
    reference_start = max(0, offset_steps)
    target_start = max(0, -offset_steps)
    overlap = min(len(reference) - reference_start, len(target) - target_start)
    if overlap <= 0:
        return -1.0, 0

    score = 0.0
    for index in range(overlap):
        score += reference[reference_start + index] * target[target_start + index]
    return score, overlap


def _slice_waveform_peaks(
    waveform: WaveformData,
    start_seconds: float,
) -> tuple[list[float], float]:
    start_seconds = max(0.0, start_seconds)
    if start_seconds >= waveform.duration:
        return [], 0.0

    start_index = max(0, int(start_seconds * waveform.peaks_per_second))
    sliced = waveform.peaks[start_index:]
    duration = max(0.0, waveform.duration - start_seconds)
    return sliced, duration


def estimate_waveform_alignment(
    video_waveform: WaveformData,
    audio_waveform: WaveformData,
    *,
    target_track: str,
    search_seconds: float = AUTO_ALIGN_SEARCH_SECONDS,
    video_start_seconds: float = 0.0,
    audio_start_seconds: float = 0.0,
) -> AutoAlignResult:
    if not video_waveform.peaks or not audio_waveform.peaks:
        raise ProcessingError("没有可用于自动对齐的波形数据。")

    peaks_per_second = min(video_waveform.peaks_per_second, audio_waveform.peaks_per_second)
    if peaks_per_second <= 0:
        raise ProcessingError("波形分辨率无效，无法自动对齐。")
    if video_waveform.peaks_per_second != audio_waveform.peaks_per_second:
        raise ProcessingError("两条波形的分辨率不一致，无法自动对齐。")

    sliced_video_peaks, sliced_video_duration = _slice_waveform_peaks(video_waveform, video_start_seconds)
    sliced_audio_peaks, sliced_audio_duration = _slice_waveform_peaks(audio_waveform, audio_start_seconds)
    reference = _normalize_envelope(sliced_audio_peaks)
    target = _normalize_envelope(sliced_video_peaks)
    if not reference or not target:
        raise ProcessingError("波形能量变化太少，无法自动对齐。")

    max_offset_steps = max(1, int(round(search_seconds * peaks_per_second)))
    min_overlap_steps = max(
        1,
        int(round(min(10.0, min(sliced_video_duration, sliced_audio_duration) * 0.25) * peaks_per_second)),
    )
    best_offset_steps = 0
    best_score = -1.0
    second_score = -1.0
    best_overlap = 0
    exclusion_steps = max(1, int(round(0.5 * peaks_per_second)))

    for offset_steps in range(-max_offset_steps, max_offset_steps + 1):
        score, overlap = _correlation_score(reference, target, offset_steps)
        if overlap < min_overlap_steps:
            continue
        if score > best_score:
            if abs(offset_steps - best_offset_steps) > exclusion_steps:
                second_score = best_score
            best_score = score
            best_offset_steps = offset_steps
            best_overlap = overlap
        elif abs(offset_steps - best_offset_steps) > exclusion_steps and score > second_score:
            second_score = score

    if best_score <= -1.0 or best_overlap <= 0:
        raise ProcessingError("未能在搜索范围内找到可靠的自动对齐位置。")

    delta_media_offset_seconds = best_offset_steps / peaks_per_second
    current_media_offset_seconds = audio_start_seconds - video_start_seconds
    media_offset_seconds = current_media_offset_seconds + delta_media_offset_seconds
    target_offset_seconds = media_offset_seconds if target_track == "video" else -media_offset_seconds
    separation = max(0.0, best_score - max(second_score, 0.0))
    confidence = max(0.0, min(1.0, best_score * 0.75 + separation * 0.5))

    return AutoAlignResult(
        target_offset_seconds=target_offset_seconds,
        media_offset_seconds=media_offset_seconds,
        confidence=confidence,
        score=best_score,
        second_score=max(second_score, 0.0),
        overlap_seconds=best_overlap / peaks_per_second,
        search_seconds=search_seconds,
    )


def extract_waveform(
    media_path: Path,
    ffmpeg_dir: Path | None,
    logger: Logger,
    *,
    label: str,
) -> WaveformData:
    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffprobe_path = find_tool("ffprobe.exe", ffmpeg_dir)
    info = probe_media(ffprobe_path, media_path)
    if info.audio_streams == 0:
        raise ProcessingError(f"{label} 里没有检测到音频流。")

    logger(f"正在生成 {label} 波形: {media_path.name}")
    result = subprocess.run(
        _build_waveform_command(ffmpeg_path, media_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        **_build_subprocess_kwargs(),
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ProcessingError(f"{label} 波形生成失败: {media_path.name}\n{stderr}")

    samples = _samples_from_pcm(result.stdout)
    if not samples:
        raise ProcessingError(f"{label} 没有可用于绘制波形的音频采样。")

    window_size = max(1, WAVEFORM_SAMPLE_RATE // WAVEFORM_PEAKS_PER_SECOND)
    peaks = _build_peaks(samples, window_size)
    duration = info.duration or (len(samples) / WAVEFORM_SAMPLE_RATE)
    logger(f"{label} 波形完成: {len(peaks)} 个峰值点，时长 {duration:.3f}s")
    return WaveformData(
        path=media_path,
        duration=duration,
        peaks_per_second=WAVEFORM_PEAKS_PER_SECOND,
        peaks=peaks,
    )


def _raise_if_export_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise ExportCancelled("已停止导出。")


def _remove_incomplete_output(output_path: Path, logger: Logger) -> None:
    if not output_path.exists():
        return
    try:
        output_path.unlink()
        logger(f"已清理未完成的输出文件: {output_path}")
    except OSError as exc:
        logger(f"清理未完成的输出文件失败: {output_path} ({exc})")


def export_aligned_audio(
    audio_path: Path,
    output_path: Path,
    offset_seconds: float,
    ffmpeg_dir: Path | None,
    logger: Logger,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_process_started: Callable[[subprocess.Popen | None], None] | None = None,
) -> Path:
    _raise_if_export_cancelled(should_cancel)
    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffprobe_path = find_tool("ffprobe.exe", ffmpeg_dir)
    media_info = probe_media(ffprobe_path, audio_path)
    source_payload = _probe_payload(ffprobe_path, audio_path)
    if media_info.audio_streams == 0:
        raise ProcessingError(f"原唱音源里没有检测到音频流: {audio_path.name}")

    if output_path.suffix.lower() != ".wav":
        output_path = output_path.with_suffix(".wav")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger(f"导出对齐音频: {output_path.name}")
    logger(f"原唱音源偏移: {format_offset(offset_seconds)}")
    if offset_seconds < 0:
        logger(f"处理方式: 裁掉原唱音源开头 {abs(offset_seconds):.3f}s")
    elif offset_seconds > 0:
        logger(f"处理方式: 给原唱音源前面补 {offset_seconds:.3f}s 静音")
    else:
        logger("处理方式: 不改变时间轴，仅按目标格式重新封装")
    logger("音频格式: 导出 WAV PCM，保留未压缩音频形态")

    try:
        run_command(
            build_aligned_audio_command(
                ffmpeg_path=ffmpeg_path,
                audio_path=audio_path,
                output_path=output_path,
                offset_seconds=offset_seconds,
                source_payload=source_payload,
            ),
            logger,
            should_cancel=should_cancel,
            on_process_started=on_process_started,
        )
    except ExportCancelled:
        _remove_incomplete_output(output_path, logger)
        raise
    except ProcessingError as exc:
        raise ProcessingError(f"导出对齐音频失败: {output_path.name}\n{exc}") from exc

    if not output_path.is_file() or os.path.getsize(output_path) == 0:
        raise ProcessingError(f"导出失败，未生成有效文件: {output_path}")

    logger(f"对齐音频导出完成: {output_path}")
    return output_path


def build_aligned_audio_command(
    ffmpeg_path: str,
    audio_path: Path,
    output_path: Path,
    offset_seconds: float,
    *,
    source_payload: dict | None = None,
) -> list[str]:
    if output_path.suffix.lower() != ".wav":
        output_path = output_path.with_suffix(".wav")

    command = [ffmpeg_path, "-y", "-hide_banner"]
    if offset_seconds < 0:
        command.extend(["-ss", f"{abs(offset_seconds):.6f}"])

    command.extend(
        [
            "-i",
            str(audio_path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
        ]
    )

    if offset_seconds > 0:
        delay_ms = max(0, int(round(offset_seconds * 1000)))
        command.extend(["-af", f"adelay={delay_ms}:all=1"])

    audio_streams = _audio_streams(source_payload) if source_payload is not None else []
    first_audio_stream = audio_streams[0] if audio_streams else {}
    source_codec = str(first_audio_stream.get("codec_name") or "").lower()
    sample_format = str(first_audio_stream.get("sample_fmt") or "").lower()
    sample_bits = str(
        first_audio_stream.get("bits_per_raw_sample")
        or first_audio_stream.get("bits_per_sample")
        or ""
    ).lower()
    pcm_codec = "pcm_s16le"
    if "dbl" in sample_format or "pcm_f64" in source_codec or sample_bits == "64":
        pcm_codec = "pcm_f64le"
    elif "flt" in sample_format or "pcm_f32" in source_codec:
        pcm_codec = "pcm_f32le"
    elif sample_bits == "24" or "s24" in sample_format:
        pcm_codec = "pcm_s24le"
    elif sample_bits == "32" or "s32" in sample_format:
        pcm_codec = "pcm_s32le"
    elif sample_bits == "8" or "u8" in sample_format:
        pcm_codec = "pcm_u8"
    command.extend(["-c:a", pcm_codec])

    sample_rate = first_audio_stream.get("sample_rate")
    if sample_rate:
        command.extend(["-ar", str(sample_rate)])
    channels = first_audio_stream.get("channels")
    if channels:
        command.extend(["-ac", str(channels)])

    command.extend(["-f", "wav"])
    command.append(str(output_path))
    return command


def export_aligned_video_v2(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    offset_seconds: float,
    ffmpeg_dir: Path | None,
    logger: Logger,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_process_started: Callable[[subprocess.Popen | None], None] | None = None,
    encode_mode: str = ENCODE_MODE_SOFTWARE,
    lead_fill_color: str = LEAD_FILL_BLACK,
    force_1080p60: bool = False,
    output_duration_seconds: float | None = None,
    use_source_video_audio: bool = False,
) -> Path:
    _raise_if_export_cancelled(should_cancel)
    if encode_mode not in {ENCODE_MODE_SOFTWARE, ENCODE_MODE_HARDWARE}:
        encode_mode = ENCODE_MODE_SOFTWARE

    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffprobe_path = find_tool("ffprobe.exe", ffmpeg_dir)
    video_info = probe_media(ffprobe_path, video_path)
    source_payload = _probe_payload(ffprobe_path, video_path)
    audio_info = probe_media(ffprobe_path, audio_path)
    audio_payload = _probe_payload(ffprobe_path, audio_path)
    if video_info.video_streams == 0:
        raise ProcessingError(f"Subtitle video has no video stream: {video_path.name}")
    if audio_info.audio_streams == 0:
        raise ProcessingError(f"Source audio has no audio stream: {audio_path.name}")
    if use_source_video_audio and video_info.audio_streams == 0:
        raise ProcessingError(f"Source video has no audio stream to keep: {video_path.name}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _raise_if_export_cancelled(should_cancel)

    logger(f"Export aligned video: {output_path.name}")
    logger(f"Subtitle video offset: {format_offset(offset_seconds)}")
    if use_source_video_audio:
        logger("Keep audio track from trimmed subtitle video")
    else:
        logger(f"Replace audio track with: {audio_path.name}")
    if offset_seconds > 0:
        logger(f"Lead-in fill mode: {_lead_fill_label(lead_fill_color)}")
    if force_1080p60:
        logger("视频输出: 强制重编码为 1920x1080 / 60fps")
    if output_duration_seconds is not None:
        logger(f"视频尾部裁剪: 输出时长限制为 {output_duration_seconds:.3f}s")
    if offset_seconds < 0:
        logger(f"处理方式: 裁掉字幕视频开头 {abs(offset_seconds):.3f}s，并重编码导出")
    elif offset_seconds > 0:
        logger(f"处理方式: 给字幕视频前面补 {offset_seconds:.3f}s {lead_fill_color} 前导画面，并重编码导出")
    else:
        logger("处理方式: 不改时间轴，直接重编码导出视频")

    video_stream = _first_video_stream(source_payload)
    video_codec = str(video_stream.get("codec_name") or "h264")
    frame_rate = _parse_fraction(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
    audio_streams = _audio_streams(audio_payload)
    source_video_audio_streams = _audio_streams(source_payload)
    output_audio_streams = source_video_audio_streams if use_source_video_audio else audio_streams
    audio_codec = str(output_audio_streams[0].get("codec_name") or "aac") if output_audio_streams else "none"
    encode_label = "硬编快速" if encode_mode == ENCODE_MODE_HARDWARE else "软编省空间"
    logger(
        "重编码策略: 使用滤镜处理时间轴，"
        f"编码模式={encode_label}，video={video_codec}, fps={frame_rate}, audio={audio_codec}"
    )

    command = build_aligned_video_command(
        ffmpeg_path=ffmpeg_path,
        video_path=video_path,
        audio_path=audio_path,
        output_path=output_path,
        offset_seconds=offset_seconds,
        source_payload=source_payload,
        audio_payload=audio_payload,
        encode_mode=encode_mode,
        lead_fill_color=lead_fill_color,
        force_1080p60=force_1080p60,
        output_duration_seconds=output_duration_seconds,
        use_source_video_audio=use_source_video_audio,
    )
    try:
        run_command(
            command,
            logger,
            should_cancel=should_cancel,
            on_process_started=on_process_started,
        )
    except ProcessingError as exc:
        if encode_mode == ENCODE_MODE_HARDWARE:
            logger(f"硬编失败，自动改用软编重试: {exc}")
            fallback_command = build_aligned_video_command(
                ffmpeg_path=ffmpeg_path,
                video_path=video_path,
                audio_path=audio_path,
                output_path=output_path,
                offset_seconds=offset_seconds,
                source_payload=source_payload,
                audio_payload=audio_payload,
                encode_mode=ENCODE_MODE_SOFTWARE,
                lead_fill_color=lead_fill_color,
                force_1080p60=force_1080p60,
                output_duration_seconds=output_duration_seconds,
                use_source_video_audio=use_source_video_audio,
            )
            try:
                run_command(
                    fallback_command,
                    logger,
                    should_cancel=should_cancel,
                    on_process_started=on_process_started,
                )
            except ProcessingError as fallback_exc:
                raise ProcessingError(
                    f"导出对齐视频失败: {output_path.name}\n{fallback_exc}"
                ) from fallback_exc
        else:
            raise ProcessingError(f"导出对齐视频失败: {output_path.name}\n{exc}") from exc

    if not output_path.is_file() or os.path.getsize(output_path) == 0:
        raise ProcessingError(f"导出失败，未生成有效文件: {output_path}")

    logger(f"对齐视频导出完成: {output_path}")
    return output_path


def build_alignment_preview_command(
    ffmpeg_path: str,
    video_path: Path,
    audio_path: Path,
    offset_seconds: float,
    *,
    target_track: str,
    preview_start_seconds: float = 0.0,
) -> list[str]:
    preview_start_seconds = max(0.0, preview_start_seconds)
    video_offset = offset_seconds if target_track == "video" else 0.0
    audio_offset = offset_seconds if target_track == "audio" else 0.0
    video_args, video_delay = _preview_input_args(video_path, video_offset, preview_start_seconds)
    audio_args, audio_delay = _preview_input_args(audio_path, audio_offset, preview_start_seconds)
    filter_graph = ";".join(
        [
            _preview_filter(0, video_delay, "video_preview"),
            _preview_filter(1, audio_delay, "audio_preview"),
            "[video_preview][audio_preview]amix=inputs=2:duration=longest:dropout_transition=0,volume=0.5[out]",
        ]
    )

    return [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "error",
        *video_args,
        *audio_args,
        "-filter_complex",
        filter_graph,
        "-map",
        "[out]",
        "-vn",
        "-sn",
        "-dn",
        "-f",
        "wav",
        "pipe:1",
    ]


def start_alignment_preview(
    video_path: Path,
    audio_path: Path,
    offset_seconds: float,
    ffmpeg_dir: Path | None,
    logger: Logger,
    *,
    target_track: str,
    preview_start_seconds: float = 0.0,
) -> AlignmentPreviewProcess:
    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffplay_path = find_tool("ffplay.exe", ffmpeg_dir)
    moving_label = "字幕视频音轨" if target_track == "video" else "原唱音源"
    logger(
        f"播放预览: 从 {preview_start_seconds:.3f}s 开始，"
        f"{moving_label}偏移 {format_offset(offset_seconds)}"
    )
    logger("预览混音: 字幕视频音轨 + 原唱音源")

    ffmpeg_command = build_alignment_preview_command(
        ffmpeg_path=ffmpeg_path,
        video_path=video_path,
        audio_path=audio_path,
        offset_seconds=offset_seconds,
        target_track=target_track,
        preview_start_seconds=preview_start_seconds,
    )
    ffplay_command = [
        ffplay_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nodisp",
        "-autoexit",
        "-i",
        "pipe:0",
    ]

    ffmpeg_process = subprocess.Popen(
        ffmpeg_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        **_build_subprocess_kwargs(),
    )
    assert ffmpeg_process.stdout is not None
    try:
        ffplay_process = subprocess.Popen(
            ffplay_command,
            stdin=ffmpeg_process.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_build_subprocess_kwargs(),
        )
    except Exception:
        ffmpeg_process.terminate()
        raise
    finally:
        ffmpeg_process.stdout.close()

    return AlignmentPreviewProcess(
        ffmpeg_process=ffmpeg_process,
        ffplay_process=ffplay_process,
    )


def build_aligned_video_command(
    ffmpeg_path: str,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    offset_seconds: float,
    *,
    source_payload: dict | None = None,
    audio_payload: dict | None = None,
    encode_mode: str = ENCODE_MODE_SOFTWARE,
    lead_fill_color: str = LEAD_FILL_BLACK,
    force_1080p60: bool = False,
    output_duration_seconds: float | None = None,
    use_source_video_audio: bool = False,
) -> list[str]:
    command = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
    ]
    if output_duration_seconds is not None:
        output_duration_seconds = max(0.001, output_duration_seconds)
    if offset_seconds < 0:
        command.extend(["-ss", f"{abs(offset_seconds):.6f}"])

    video_stream = _first_video_stream(source_payload) if source_payload is not None else {}
    audio_source_payload = source_payload if use_source_video_audio else audio_payload
    audio_streams = _audio_streams(audio_source_payload) if audio_source_payload is not None else []
    first_audio_stream = audio_streams[0] if audio_streams else {}
    frame_rate = _parse_fraction(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
    target_frame_rate = FORCED_OUTPUT_FPS if force_1080p60 else frame_rate
    pixel_format = str(video_stream.get("pix_fmt") or "yuv420p")

    subtitle_input_index = 0
    if offset_seconds > 0:
        subtitle_input_index = 1
        command.extend(
            [
                "-i",
                str(video_path),
                "-itsoffset",
                f"{offset_seconds:.6f}",
                "-i",
                str(video_path),
            ]
        )
        if lead_fill_color == LEAD_FILL_FREEZE:
            video_filter = (
                f"[0:v:0]tpad=start_duration={offset_seconds:.6f}:start_mode=clone,"
                "setpts=PTS-STARTPTS"
            )
        else:
            video_filter = (
                f"[0:v:0]tpad=start_duration={offset_seconds:.6f}:start_mode=add:color={lead_fill_color},"
                "setpts=PTS-STARTPTS"
            )
    else:
        command.extend(["-i", str(video_path)])
        video_filter = "[0:v:0]setpts=PTS-STARTPTS"
    if not use_source_video_audio:
        command.extend(["-i", str(audio_path)])

    if force_1080p60:
        video_filter += (
            f",scale={FORCED_OUTPUT_WIDTH}:{FORCED_OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={FORCED_OUTPUT_WIDTH}:{FORCED_OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    video_filter += f",fps=fps={target_frame_rate}[v]"
    filters = [video_filter]
    maps = ["-map", "[v]"]
    if use_source_video_audio:
        audio_input_index = 0
        audio_filter = f"[{audio_input_index}:a:0]asetpts=PTS-STARTPTS"
        if offset_seconds > 0:
            delay_ms = max(0, int(round(offset_seconds * 1000)))
            audio_filter += f",adelay={delay_ms}:all=1"
        audio_filter += "[a]"
    else:
        audio_input_index = subtitle_input_index + 1
        audio_filter = f"[{audio_input_index}:a:0]asetpts=PTS-STARTPTS[a]"
    filters.append(audio_filter)
    maps.extend(["-map", "[a]"])

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
        ]
    )
    if output_duration_seconds is not None:
        command.extend(["-t", f"{output_duration_seconds:.6f}"])
    command.extend(
        [
            *maps,
            "-map",
            f"{subtitle_input_index}:s?",
            "-map",
            f"{subtitle_input_index}:d?",
            "-map",
            f"{subtitle_input_index}:t?",
            "-map_metadata",
            "0",
        ]
    )
    command.extend(_video_encoding_options(video_stream, encode_mode))
    command.extend(["-pix_fmt", pixel_format, "-r", target_frame_rate])
    command.extend(_audio_encoding_options(first_audio_stream))
    command.extend(["-c:s", "copy", "-c:d", "copy", "-c:t", "copy", str(output_path)])
    return command


def export_aligned_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    offset_seconds: float,
    ffmpeg_dir: Path | None,
    logger: Logger,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_process_started: Callable[[subprocess.Popen | None], None] | None = None,
    encode_mode: str = ENCODE_MODE_SOFTWARE,
    lead_fill_color: str = LEAD_FILL_BLACK,
    force_1080p60: bool = False,
    output_duration_seconds: float | None = None,
    use_source_video_audio: bool = False,
) -> Path:
    return export_aligned_video_v2(
        video_path=video_path,
        audio_path=audio_path,
        output_path=output_path,
        offset_seconds=offset_seconds,
        ffmpeg_dir=ffmpeg_dir,
        logger=logger,
        should_cancel=should_cancel,
        on_process_started=on_process_started,
        encode_mode=encode_mode,
        lead_fill_color=lead_fill_color,
        force_1080p60=force_1080p60,
        output_duration_seconds=output_duration_seconds,
        use_source_video_audio=use_source_video_audio,
    )
