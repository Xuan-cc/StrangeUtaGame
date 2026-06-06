from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
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
LEGACY_APP_NAMES = ("Karaoke Helper",)

# 工作台界面主题：跟随系统 / 强制浅色 / 强制深色。
# 与 SUG ``frontend/theme.py::ThemeMode`` 对应。新值必须同步两边。
UI_THEME_AUTO = "auto"
UI_THEME_LIGHT = "light"
UI_THEME_DARK = "dark"
UI_THEMES = {UI_THEME_AUTO, UI_THEME_LIGHT, UI_THEME_DARK}

# StrangeUtaGame 一次性迁移使用的 marker。
# True 表示已经检测过老路径并完成（或确认无须）导入，不再重复。
LYRICS_TIMING_MIGRATED_KEY = "lyrics_timing_migrated_v1"


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
    updater: dict = field(default_factory=dict)

    # ── 工作台界面主题 ──
    # ``auto`` 跟随 OS；``light`` / ``dark`` 强制。SUG embedded 嵌入区与所有工作台
    # 页面共享同一份主题状态（由 ``theme_workbench`` 单例驱动）。非法值在
    # ``load_app_settings`` 中 fallback 到 ``auto`` 并 warn。
    ui_theme: str = UI_THEME_AUTO

    # ── 歌词打轴模块（StrangeUtaGame）的设置 namespace ──
    # 由 frontend/settings/app_settings.py 中 AppSettings 的 dotted-key 树
    # 序列化/反序列化得来，宿主直接以 dict 形式持久化；StrangeUtaGame
    # 内部继续按它自己的 ``get("audio.default_volume")`` 风格读写。
    # 大字典/列表型字段单独建独立 namespace，避免 lyrics_timing 主 dict 过大。
    lyrics_timing: dict = field(default_factory=dict)
    lyrics_timing_dictionary: list = field(default_factory=list)
    lyrics_timing_singers: list = field(default_factory=list)
    lyrics_timing_network_dictionary: dict = field(default_factory=dict)
    # 一次性迁移 marker（见 :func:`migrate_strange_uta_game_settings`）
    lyrics_timing_migrated_v1: bool = False


def _settings_path_for_app_name(app_name: str) -> Path:
    appdata = os.getenv("APPDATA")
    if os.name == "nt" and appdata:
        return Path(appdata) / app_name / SETTINGS_FILE_NAME

    config_home = os.getenv("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / app_name.lower().replace(" ", "-") / SETTINGS_FILE_NAME

    return Path.home() / ".config" / app_name.lower().replace(" ", "-") / SETTINGS_FILE_NAME


def get_settings_path() -> Path:
    return _settings_path_for_app_name(APP_NAME)


def get_legacy_settings_paths() -> list[Path]:
    return [_settings_path_for_app_name(name) for name in LEGACY_APP_NAMES]


def load_app_settings() -> AppSettings:
    path = get_settings_path()
    if not path.is_file():
        path = next((legacy for legacy in get_legacy_settings_paths() if legacy.is_file()), path)
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
    ui_theme_raw = str(payload.get("ui_theme", UI_THEME_AUTO))
    if ui_theme_raw not in UI_THEMES:
        logging.getLogger(__name__).warning(
            "settings.json ui_theme=%r 非法，回落到 %s", ui_theme_raw, UI_THEME_AUTO
        )
        ui_theme_raw = UI_THEME_AUTO

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
        updater=_safe_dict(payload.get("updater")),
        ui_theme=ui_theme_raw,
        lyrics_timing=_safe_dict(payload.get("lyrics_timing")),
        lyrics_timing_dictionary=_safe_list(payload.get("lyrics_timing_dictionary")),
        lyrics_timing_singers=_safe_list(payload.get("lyrics_timing_singers")),
        lyrics_timing_network_dictionary=_safe_dict(payload.get("lyrics_timing_network_dictionary")),
        lyrics_timing_migrated_v1=bool(payload.get(LYRICS_TIMING_MIGRATED_KEY, False)),
    )


def save_app_settings(settings: AppSettings) -> Path:
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _safe_dict(value: object) -> dict:
    """Return ``value`` if it's a dict, otherwise an empty dict.

    Used by :func:`load_app_settings` when loading namespace fields that
    must always end up as dicts even if the on-disk JSON contained a
    non-dict value due to corruption or version skew.
    """
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list:
    """Return ``value`` if it's a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


# ════════════════════════════════════════════════════════════════════
# StrangeUtaGame 一次性配置迁移
# ════════════════════════════════════════════════════════════════════

# StrangeUtaGame 在 standalone 模式下回退到这个目录写 config 当程序目录
# 不可写时。见 ``frontend/settings/app_settings.py:get_config_dir``。
_LEGACY_STRANGE_UTA_GAME_DIR = Path.home() / ".strange_uta_game"
_LEGACY_FILES = {
    "lyrics_timing": "config.json",
    "lyrics_timing_dictionary": "dictionary.json",
    "lyrics_timing_singers": "singers.json",
    "lyrics_timing_network_dictionary": "network_dictionary.json",
}
_LEGACY_LIST_FIELDS = {"lyrics_timing_dictionary", "lyrics_timing_singers"}


def migrate_strange_uta_game_settings(
    settings: AppSettings,
    legacy_dir: Path | None = None,
) -> bool:
    """One-shot import of legacy StrangeUtaGame JSON files into ``settings``.

    Scans ``legacy_dir`` (defaults to ``~/.strange_uta_game``) for the four
    standalone JSON config files and merges their contents into the
    matching namespace fields on the in-memory ``AppSettings``. If the
    marker ``lyrics_timing_migrated_v1`` is already True, returns False
    immediately so re-runs are no-ops.

    The caller is responsible for persisting the result via
    :func:`save_app_settings` — this function mutates ``settings`` in
    place but does not touch disk on the krok-helper side. Reads from the
    legacy files are best-effort: corrupt/missing files are skipped
    silently.

    Returns:
        True if at least one legacy file was imported AND the marker
        flipped to True. False if the marker was already set, the legacy
        dir doesn't exist, or no files were importable.
    """
    if settings.lyrics_timing_migrated_v1:
        return False

    src = legacy_dir if legacy_dir is not None else _LEGACY_STRANGE_UTA_GAME_DIR
    if not src.is_dir():
        # 没有老安装可迁移 —— marker 直接置位避免每次启动都扫盘。
        settings.lyrics_timing_migrated_v1 = True
        return False

    log = logging.getLogger(__name__)
    imported_any = False
    for namespace_field, filename in _LEGACY_FILES.items():
        legacy_path = src / filename
        if not legacy_path.is_file():
            continue
        try:
            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("legacy StrangeUtaGame file unreadable: %s", legacy_path, exc_info=True)
            continue
        if namespace_field in _LEGACY_LIST_FIELDS:
            if not isinstance(payload, list):
                continue
        elif not isinstance(payload, dict):
            continue
        # 直接覆盖整个 namespace；老用户首次启动我们以"老配置为准"。
        setattr(settings, namespace_field, payload)
        imported_any = True

    settings.lyrics_timing_migrated_v1 = True
    return imported_any
