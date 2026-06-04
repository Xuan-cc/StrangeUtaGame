from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from krok_helper.config import APP_NAME
from krok_helper.audio_alignment import (
    DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE,
    DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE,
    ENCODE_MODE_HARDWARE,
    ENCODE_MODE_SOFTWARE,
)
from krok_helper.pipeline import DEFAULT_OFF_NAME_TEMPLATE, DEFAULT_ON_NAME_TEMPLATE, OUTPUT_NAME_MODE_FIXED
from krok_helper.lyrics import DEFAULT_LYRICS_PROVIDER_IDS, LYRICS_LANGUAGE_ORIGINAL, LYRICS_PREVIEW_LINE
from krok_helper.video_download.download_task import NAMING_RULE_TITLE, SOURCE_YOUTUBE


SETTINGS_FILE_NAME = "settings.json"
ALIGN_TARGET_VIDEO = "video"
ALIGN_TARGET_AUDIO = "audio"


@dataclass
class AppSettings:
    output_name_mode: str = OUTPUT_NAME_MODE_FIXED
    on_name_template: str = DEFAULT_ON_NAME_TEMPLATE
    off_name_template: str = DEFAULT_OFF_NAME_TEMPLATE
    align_video_name_template: str = DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE
    align_audio_name_template: str = DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE
    ffmpeg_dir: str = ""
    align_target: str = ALIGN_TARGET_VIDEO
    align_encode_mode: str = ENCODE_MODE_SOFTWARE
    align_force_1080p60: bool = False
    align_export_use_video_audio: bool = False
    lyrics_source_ids: list[str] | tuple[str, ...] = DEFAULT_LYRICS_PROVIDER_IDS
    lyrics_preview_mode: str = LYRICS_PREVIEW_LINE
    lyrics_language: str = LYRICS_LANGUAGE_ORIGINAL
    lyrics_strip_intro_lines: bool = True
    video_download_save_dir: str = ""
    video_download_naming_rule: str = NAMING_RULE_TITLE
    video_download_custom_template: str = "{title}"
    video_download_merge_video_audio: bool = True
    video_download_download_thumbnail: bool = False
    video_download_download_subtitle: bool = False
    video_download_concurrent_count: int = 3
    video_download_timeout: int = 5
    video_download_retry_count: int = 3
    video_download_cookie_path: str = ""
    video_download_source: str = SOURCE_YOUTUBE


def get_settings_path() -> Path:
    appdata = os.getenv("APPDATA")
    if os.name == "nt" and appdata:
        return Path(appdata) / APP_NAME / SETTINGS_FILE_NAME

    config_home = os.getenv("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / APP_NAME.lower().replace(" ", "-") / SETTINGS_FILE_NAME

    return Path.home() / ".config" / APP_NAME.lower().replace(" ", "-") / SETTINGS_FILE_NAME


def load_app_settings() -> AppSettings:
    path = get_settings_path()
    if not path.is_file():
        return AppSettings()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return AppSettings()

    if not isinstance(payload, dict):
        return AppSettings()

    align_target = str(payload.get("align_target", ALIGN_TARGET_VIDEO))
    if align_target not in {ALIGN_TARGET_VIDEO, ALIGN_TARGET_AUDIO}:
        align_target = ALIGN_TARGET_VIDEO
    align_encode_mode = str(payload.get("align_encode_mode", ENCODE_MODE_SOFTWARE))
    if align_encode_mode not in {ENCODE_MODE_SOFTWARE, ENCODE_MODE_HARDWARE}:
        align_encode_mode = ENCODE_MODE_SOFTWARE

    return AppSettings(
        output_name_mode=str(payload.get("output_name_mode", OUTPUT_NAME_MODE_FIXED)),
        on_name_template=str(payload.get("on_name_template", DEFAULT_ON_NAME_TEMPLATE)),
        off_name_template=str(payload.get("off_name_template", DEFAULT_OFF_NAME_TEMPLATE)),
        align_video_name_template=str(
            payload.get("align_video_name_template", DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE)
        ),
        align_audio_name_template=str(
            payload.get("align_audio_name_template", DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE)
        ),
        ffmpeg_dir=str(payload.get("ffmpeg_dir", "")),
        align_target=align_target,
        align_encode_mode=align_encode_mode,
        align_force_1080p60=bool(payload.get("align_force_1080p60", False)),
        align_export_use_video_audio=bool(payload.get("align_export_use_video_audio", False)),
        lyrics_source_ids=tuple(
            str(item)
            for item in payload.get("lyrics_source_ids", DEFAULT_LYRICS_PROVIDER_IDS)
            if str(item).strip()
        )
        or DEFAULT_LYRICS_PROVIDER_IDS,
        lyrics_preview_mode=str(payload.get("lyrics_preview_mode", LYRICS_PREVIEW_LINE)),
        lyrics_language=str(payload.get("lyrics_language", LYRICS_LANGUAGE_ORIGINAL)),
        lyrics_strip_intro_lines=bool(payload.get("lyrics_strip_intro_lines", True)),
        video_download_save_dir=str(payload.get("video_download_save_dir", "")),
        video_download_naming_rule=str(payload.get("video_download_naming_rule", NAMING_RULE_TITLE)),
        video_download_custom_template=str(payload.get("video_download_custom_template", "{title}")),
        video_download_merge_video_audio=bool(payload.get("video_download_merge_video_audio", True)),
        video_download_download_thumbnail=bool(payload.get("video_download_download_thumbnail", False)),
        video_download_download_subtitle=bool(payload.get("video_download_download_subtitle", False)),
        video_download_concurrent_count=min(5, max(1, int(payload.get("video_download_concurrent_count", 3) or 3))),
        video_download_timeout=(
            int(payload.get("video_download_timeout", 5) or 5)
            if int(payload.get("video_download_timeout", 5) or 5) in (5, 10, 15)
            else 5
        ),
        video_download_retry_count=min(5, max(1, int(payload.get("video_download_retry_count", 3) or 3))),
        video_download_cookie_path=str(payload.get("video_download_cookie_path", "")),
        video_download_source=str(payload.get("video_download_source", SOURCE_YOUTUBE)),
    )


def save_app_settings(settings: AppSettings) -> Path:
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
