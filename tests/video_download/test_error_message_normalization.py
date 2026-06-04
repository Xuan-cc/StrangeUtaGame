from __future__ import annotations

from krok_helper.video_download.ytdlp_service import YtDlpService


def normalize(message: str) -> str:
    return YtDlpService()._normalize_error_message(RuntimeError(message))


def test_ffmpeg_not_found_maps_to_friendly() -> None:
    assert "未找到 ffmpeg" in normalize("ffmpeg not found")


def test_requested_format_not_available_maps_to_friendly() -> None:
    assert "当前清晰度不可用" in normalize("requested format is not available")


def test_downloaded_file_empty_maps_to_youtube_hint() -> None:
    result = normalize("downloaded file is empty")

    assert "YouTube" in result
    assert "空文件" in result


def test_not_a_bot_maps_to_youtube_hint() -> None:
    assert "机器人校验" in normalize("Sign in to confirm you are not a bot")


def test_login_required_maps_to_bilibili_hint() -> None:
    assert "Bilibili 登录状态" in normalize("login required")


def test_http_403_maps_to_friendly() -> None:
    assert "访问被拒绝" in normalize("HTTP Error 403: Forbidden")


def test_timed_out_maps_to_friendly() -> None:
    assert "网络超时" in normalize("The read operation timed out")


def test_unknown_error_returned_verbatim() -> None:
    assert normalize("some unknown failure") == "some unknown failure"
