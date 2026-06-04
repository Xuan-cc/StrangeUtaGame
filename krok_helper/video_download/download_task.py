from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


SOURCE_YOUTUBE = "YouTube"
SOURCE_BILIBILI = "Bilibili"
SOURCE_UNKNOWN = "未知"

TASK_STATUS_WAITING = "等待中"
TASK_STATUS_DOWNLOADING = "下载中"
TASK_STATUS_COMPLETED = "已完成"
TASK_STATUS_FAILED = "失败"
TASK_STATUS_CANCELLED = "已取消"

NAMING_RULE_TITLE = "使用标题"
NAMING_RULE_TITLE_UPLOADER = "标题 + 作者"
NAMING_RULE_CUSTOM = "自定义模板"


@dataclass(slots=True)
class FormatOption:
    option_id: str
    download_format: str
    format_label: str
    resolution: str
    video_codec: str
    audio_codec: str
    filesize: int | None = None
    ext: str = ""
    note: str = ""
    height: int = 0
    width: int = 0
    is_recommended: bool = False
    requires_merge: bool = False


@dataclass(slots=True)
class VideoInfo:
    url: str
    source: str
    title: str
    uploader: str
    duration: float | None
    thumbnail_url: str = ""
    thumbnail_bytes: bytes = b""
    webpage_url: str = ""
    width: int = 0
    height: int = 0
    filesize: int | None = None
    formats: list[FormatOption] = field(default_factory=list)
    recommended_option_id: str = ""
    subtitles_available: bool = False
    extractor_args_hint: str = ""


@dataclass(slots=True)
class DownloadTask:
    task_id: str
    url: str
    title: str
    source: str
    selected_format: FormatOption | None = None
    output_path: Path | None = None
    status: str = TASK_STATUS_WAITING
    progress: float = 0.0
    speed_text: str = ""
    filesize: int | None = None
    downloaded_bytes: int = 0
    error_message: str = ""
    local_file: Path | None = None
    cancel_requested: bool = False
    info: VideoInfo | None = None
    available_formats: list[FormatOption] = field(default_factory=list)
    progress_total_phases: int = 1
    progress_phase_index: int = 0
    progress_phase_bytes: int = 0
    progress_phase_name: str = ""
    progress_phase_source_name: str = ""
    progress_phase_totals: list[int] = field(default_factory=list)
    progress_phase_downloaded: list[int] = field(default_factory=list)
    progress_merge_active: bool = False
    speed_samples: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=120))
    naming_rule: str = NAMING_RULE_TITLE
    custom_template: str = "{title}"
    merge_video_audio: bool = True
    download_thumbnail: bool = False
    settings_confirmed: bool = False


@dataclass(slots=True)
class DownloadOptions:
    save_dir: str
    naming_rule: str = NAMING_RULE_TITLE
    custom_template: str = "{title}"
    merge_video_audio: bool = True
    download_thumbnail: bool = False
    download_subtitle: bool = False
    concurrent_count: int = 3
    timeout: int = 30
    retry_count: int = 3
    cookie_file: str = ""
