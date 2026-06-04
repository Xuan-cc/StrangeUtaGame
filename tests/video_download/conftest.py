from __future__ import annotations

from pathlib import Path

import pytest

from krok_helper.video_download.cookie_manager import CookieManager
from krok_helper.video_download.download_task import (
    DownloadTask,
    FormatOption,
    SOURCE_BILIBILI,
    SOURCE_YOUTUBE,
    VideoInfo,
)


@pytest.fixture
def format_option() -> FormatOption:
    return FormatOption(
        option_id="fmt-1",
        download_format="best",
        format_label="Best",
        resolution="1080p",
        video_codec="h264",
        audio_codec="aac",
    )


@pytest.fixture
def make_format_option():
    def factory(option_id: str, *, is_recommended: bool = False) -> FormatOption:
        return FormatOption(
            option_id=option_id,
            download_format=option_id,
            format_label=option_id,
            resolution="1080p",
            video_codec="h264",
            audio_codec="aac",
            is_recommended=is_recommended,
        )

    return factory


@pytest.fixture
def make_video_info(format_option: FormatOption):
    def factory(
        *,
        url: str = "https://example.com/video",
        webpage_url: str = "",
        source: str = SOURCE_BILIBILI,
        title: str = "Title",
        formats: list[FormatOption] | None = None,
    ) -> VideoInfo:
        return VideoInfo(
            url=url,
            source=source,
            title=title,
            uploader="Uploader",
            duration=1.0,
            webpage_url=webpage_url,
            formats=formats if formats is not None else [format_option],
        )

    return factory


@pytest.fixture
def make_download_task(make_video_info):
    def factory(
        *,
        task_id: str = "task-1",
        url: str = "https://example.com/video",
        webpage_url: str = "",
        status: str | None = None,
    ) -> DownloadTask:
        info = make_video_info(url=webpage_url or url, webpage_url=webpage_url)
        kwargs = {"status": status} if status is not None else {}
        return DownloadTask(
            task_id=task_id,
            url=url,
            title=info.title,
            source=info.source,
            info=info,
            **kwargs,
        )

    return factory


@pytest.fixture
def isolated_cookie_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CookieManager:
    manager = CookieManager()

    def default_cookie_path(platform: str = SOURCE_BILIBILI) -> Path:
        filename = "youtube_cookies.txt" if platform == SOURCE_YOUTUBE else "bilibili_cookies.txt"
        return tmp_path / filename

    monkeypatch.setattr(manager, "default_cookie_path", default_cookie_path)
    return manager


def write_cookie(manager: CookieManager, platform: str, *, domain: str, name: str = "SID") -> Path:
    jar = manager.load_cookie_jar(platform)
    manager.set_cookie(jar, name=name, value="cookie-value", domain=domain)
    return manager.save_cookie_jar(jar, platform)


@pytest.fixture
def cookie_writer():
    return write_cookie
