from __future__ import annotations

from krok_helper.video_download.download_task import SOURCE_BILIBILI, SOURCE_UNKNOWN, SOURCE_YOUTUBE
from krok_helper.video_download.ytdlp_service import YtDlpService


def test_detects_bilibili_via_url() -> None:
    assert YtDlpService().detect_source("https://www.bilibili.com/video/BV1abc") == SOURCE_BILIBILI


def test_detects_youtube_via_url() -> None:
    assert YtDlpService().detect_source("https://www.youtube.com/watch?v=abc") == SOURCE_YOUTUBE


def test_detects_youtube_short_url() -> None:
    assert YtDlpService().detect_source("https://youtu.be/abc") == SOURCE_YOUTUBE


def test_detects_via_extractor_key() -> None:
    assert YtDlpService().detect_source("https://example.com/watch/abc", "BiliBili") == SOURCE_BILIBILI


def test_returns_unknown_for_unrelated_url() -> None:
    assert YtDlpService().detect_source("https://example.com/foo") == SOURCE_UNKNOWN
