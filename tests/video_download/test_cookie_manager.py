from __future__ import annotations

import sys
import types

import pytest

from krok_helper.video_download.download_task import SOURCE_BILIBILI, SOURCE_YOUTUBE


def test_cookies_stored_per_platform(isolated_cookie_manager, cookie_writer) -> None:
    cookie_writer(isolated_cookie_manager, SOURCE_BILIBILI, domain=".bilibili.com", name="SESSDATA")

    assert isolated_cookie_manager.has_cookie(SOURCE_BILIBILI) is True
    assert isolated_cookie_manager.has_cookie(SOURCE_YOUTUBE) is False


def test_clear_one_platform_doesnt_affect_other(isolated_cookie_manager, cookie_writer) -> None:
    cookie_writer(isolated_cookie_manager, SOURCE_BILIBILI, domain=".bilibili.com", name="SESSDATA")
    cookie_writer(isolated_cookie_manager, SOURCE_YOUTUBE, domain=".youtube.com")

    isolated_cookie_manager.clear(SOURCE_BILIBILI)

    assert isolated_cookie_manager.has_cookie(SOURCE_BILIBILI) is False
    assert isolated_cookie_manager.has_cookie(SOURCE_YOUTUBE) is True


def test_resolved_cookie_path_distinct_per_platform(isolated_cookie_manager) -> None:
    assert isolated_cookie_manager.resolved_cookie_path(SOURCE_BILIBILI) != isolated_cookie_manager.resolved_cookie_path(
        SOURCE_YOUTUBE
    )


@pytest.mark.parametrize("browser", ["Chrome", "Edge"])
def test_import_from_browser_rejects_non_firefox(isolated_cookie_manager, browser: str) -> None:
    with pytest.raises(ValueError, match="仅支持.*Firefox"):
        isolated_cookie_manager.import_from_browser(SOURCE_YOUTUBE, browser)


def test_import_from_browser_accepts_firefox_with_mock(isolated_cookie_manager, monkeypatch) -> None:
    install_fake_ytdlp(monkeypatch, b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tvalue\n")

    isolated_cookie_manager.import_from_browser(SOURCE_YOUTUBE, "Firefox")

    assert isolated_cookie_manager.has_cookie(SOURCE_YOUTUBE) is True


def test_get_profile_youtube_returns_logged_in_label(isolated_cookie_manager, cookie_writer) -> None:
    cookie_writer(isolated_cookie_manager, SOURCE_YOUTUBE, domain=".youtube.com")

    profile = isolated_cookie_manager.get_profile(SOURCE_YOUTUBE)

    assert profile is not None
    assert profile.nickname == "已登录"


def test_get_profile_returns_none_when_no_cookie(isolated_cookie_manager) -> None:
    assert isolated_cookie_manager.get_profile(SOURCE_YOUTUBE) is None


def test_import_from_browser_raises_when_resulting_cookie_empty(isolated_cookie_manager, monkeypatch) -> None:
    install_fake_ytdlp(monkeypatch, b"")

    with pytest.raises(RuntimeError, match="请确认浏览器中已登录 YouTube"):
        isolated_cookie_manager.import_from_browser(SOURCE_YOUTUBE, "Firefox")


def install_fake_ytdlp(monkeypatch, cookie_bytes: bytes) -> None:
    module = types.ModuleType("yt_dlp")

    class FakeCookieJar:
        def save(self, filename: str, ignore_discard: bool, ignore_expires: bool) -> None:
            del ignore_discard, ignore_expires
            with open(filename, "wb") as file:
                file.write(cookie_bytes)

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            self.options = options
            self.cookiejar = FakeCookieJar()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    module.YoutubeDL = FakeYoutubeDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
