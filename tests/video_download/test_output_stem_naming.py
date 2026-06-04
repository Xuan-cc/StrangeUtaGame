from __future__ import annotations

import pytest

from krok_helper.video_download.download_task import (
    DownloadOptions,
    NAMING_RULE_CUSTOM,
    NAMING_RULE_TITLE,
    NAMING_RULE_TITLE_UPLOADER,
)
from krok_helper.video_download.ytdlp_service import VideoDownloadError, YtDlpService


def test_naming_rule_title_uses_safe_title_with_resolution_suffix(tmp_path) -> None:
    options = DownloadOptions(save_dir=str(tmp_path), naming_rule=NAMING_RULE_TITLE)

    assert build_stem("Foo", "", "1080p", options) == "Foo [1080p]"


def test_naming_rule_title_uploader_concatenates(tmp_path) -> None:
    options = DownloadOptions(save_dir=str(tmp_path), naming_rule=NAMING_RULE_TITLE_UPLOADER)

    assert build_stem("Foo", "Bar", "720p", options) == "Foo - Bar [720p]"


def test_naming_rule_custom_substitutes_template(tmp_path) -> None:
    options = DownloadOptions(
        save_dir=str(tmp_path),
        naming_rule=NAMING_RULE_CUSTOM,
        custom_template="{title} - {uploader}",
    )

    assert build_stem("Foo", "Bar", "", options) == "Foo - Bar"


def test_custom_template_supports_author_alias(tmp_path) -> None:
    options = DownloadOptions(save_dir=str(tmp_path), naming_rule=NAMING_RULE_CUSTOM, custom_template="{author}")

    assert build_stem("Foo", "Bar", "", options) == "Bar"


def test_custom_template_invalid_raises_video_download_error(tmp_path) -> None:
    options = DownloadOptions(save_dir=str(tmp_path), naming_rule=NAMING_RULE_CUSTOM, custom_template="{unknown_var}")

    with pytest.raises(VideoDownloadError, match="自定义命名模板无效"):
        build_stem("Foo", "Bar", "", options)


def test_empty_template_falls_back_to_title(tmp_path) -> None:
    options = DownloadOptions(save_dir=str(tmp_path), naming_rule=NAMING_RULE_CUSTOM, custom_template="   ")

    assert build_stem("Foo", "Bar", "", options) == "Foo"


def test_resolution_suffix_not_duplicated(tmp_path) -> None:
    options = DownloadOptions(save_dir=str(tmp_path), naming_rule=NAMING_RULE_TITLE)

    assert build_stem("Foo [1080p]", "", "1080p", options) == "Foo [1080p]"


def test_empty_title_falls_back_to_default_chinese(tmp_path) -> None:
    """空 title 走第一层 fallback：使用「未命名视频」作为友好默认。"""
    options = DownloadOptions(save_dir=str(tmp_path), naming_rule=NAMING_RULE_TITLE)

    assert build_stem("", "", "", options) == "未命名视频"


def test_title_with_only_strippable_chars_falls_back_to_video(tmp_path) -> None:
    """title 全是空格 / 点，被 sanitize 后为空，走第二层 fallback：「video」。"""
    options = DownloadOptions(save_dir=str(tmp_path), naming_rule=NAMING_RULE_TITLE)

    assert build_stem("   ", "", "", options) == "video"
    assert build_stem("...", "", "", options) == "video"


def build_stem(title: str, uploader: str, resolution: str, options: DownloadOptions) -> str:
    return YtDlpService()._build_output_stem(
        title=title,
        uploader=uploader,
        resolution=resolution,
        options=options,
    )
