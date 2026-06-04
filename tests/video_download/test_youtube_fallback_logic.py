from __future__ import annotations

from krok_helper.video_download.ytdlp_service import YOUTUBE_FALLBACK_EXTRACTOR_ARGS, YtDlpService


YOUTUBE_URL = "https://www.youtube.com/watch?v=abc"
BILIBILI_URL = "https://www.bilibili.com/video/BV1abc"


def test_returns_true_for_youtube_bot_error() -> None:
    assert YtDlpService()._should_retry_youtube_with_fallback(YOUTUBE_URL, "not a bot") is True


def test_returns_true_for_empty_file_error() -> None:
    assert YtDlpService()._should_retry_youtube_with_fallback(YOUTUBE_URL, "downloaded file is empty") is True


def test_returns_false_when_already_using_fallback_args() -> None:
    assert (
        YtDlpService()._should_retry_youtube_with_fallback(
            YOUTUBE_URL,
            "not a bot",
            extractor_args_hint=YOUTUBE_FALLBACK_EXTRACTOR_ARGS,
        )
        is False
    )


def test_returns_false_for_bilibili_url() -> None:
    assert YtDlpService()._should_retry_youtube_with_fallback(BILIBILI_URL, "not a bot") is False


def test_returns_false_for_unrelated_error() -> None:
    assert YtDlpService()._should_retry_youtube_with_fallback(YOUTUBE_URL, "unrelated failure") is False
