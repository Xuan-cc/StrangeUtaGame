from __future__ import annotations

from krok_helper.video_download.ytdlp_service import YtDlpService


def test_normalizes_padding() -> None:
    assert YtDlpService().normalize_version("2024.1.5") == "2024.01.05"


def test_normalizes_full_format_unchanged() -> None:
    assert YtDlpService().normalize_version("2024.10.15") == "2024.10.15"


def test_preserves_suffix() -> None:
    assert YtDlpService().normalize_version("2024.10.15.123") == "2024.10.15.123"


def test_unparseable_returns_stripped_original() -> None:
    assert YtDlpService().normalize_version("  abcde  ") == "abcde"
