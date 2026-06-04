from __future__ import annotations

import os

import pytest

from krok_helper.video_download.download_task import SOURCE_BILIBILI, SOURCE_YOUTUBE
from krok_helper.video_download.ytdlp_service import VideoDownloadError, YtDlpService


pytestmark = [
    pytest.mark.skipif(
        os.environ.get("KROK_INTEGRATION") != "1",
        reason="integration tests opt-in via KROK_INTEGRATION=1",
    ),
    pytest.mark.timeout(60),
]


def test_extract_infos_youtube_returns_non_empty(youtube_public_url: str) -> None:
    infos = extract_or_skip(youtube_public_url)

    assert len(infos) >= 1
    assert infos[0].source == SOURCE_YOUTUBE
    assert infos[0].title != ""
    assert len(infos[0].formats) > 0


def test_extract_infos_bilibili_returns_non_empty(bilibili_public_url: str) -> None:
    infos = extract_or_skip(bilibili_public_url)

    assert len(infos) >= 1
    assert infos[0].source == SOURCE_BILIBILI
    assert infos[0].title != ""
    assert len(infos[0].formats) > 0


def test_extract_infos_bilibili_multipart_returns_multiple_parts(bilibili_multipart_url: str) -> None:
    infos = extract_or_skip(bilibili_multipart_url)

    assert len(infos) >= 2
    for index, info in enumerate(infos, start=1):
        assert info.source == SOURCE_BILIBILI
        assert f"P{index}" in info.title


def test_detect_source_against_real_urls(
    youtube_public_url: str,
    bilibili_public_url: str,
    bilibili_multipart_url: str,
) -> None:
    service = YtDlpService()

    assert service.detect_source(youtube_public_url) == SOURCE_YOUTUBE
    assert service.detect_source(bilibili_public_url) == SOURCE_BILIBILI
    assert service.detect_source(bilibili_multipart_url) == SOURCE_BILIBILI


def extract_or_skip(url: str):
    try:
        return YtDlpService().extract_infos(url)
    except VideoDownloadError as exc:
        pytest.skip(f"integration dependency or network unavailable: {exc}")
