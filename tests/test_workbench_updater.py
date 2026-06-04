from __future__ import annotations

import sys

from krok_helper.settings import AppSettings
from krok_helper.updater.settings import UpdaterSettings, ensure_updater_settings
from krok_helper.updater.sources import build_api_urls, build_release_urls, normalize_order
from krok_helper.updater.worker import LatestRelease, ReleaseAsset, current_asset_name, is_newer_version


def test_workbench_updater_uses_workbench_repo_urls() -> None:
    api_urls = build_api_urls(["github"])
    release_urls = build_release_urls(["github"], "v3.0.1", "KaraokeHelper-windows.zip")

    assert api_urls[0][1] == "https://api.github.com/repos/karaoke-studio/karaoke-studio/releases/latest"
    assert release_urls[0][1] == (
        "https://github.com/karaoke-studio/karaoke-studio/"
        "releases/download/v3.0.1/KaraokeHelper-windows.zip"
    )


def test_workbench_updater_settings_roundtrip_defaults() -> None:
    settings = AppSettings()

    updater = ensure_updater_settings(settings)

    assert updater.enabled is True
    assert updater.check_on_startup is True
    assert settings.updater["source_order"] == ["github", "ghproxy", "gh-proxy", "ghproxy-net"]
    assert UpdaterSettings.load(settings).min_check_interval_hours == 8


def test_workbench_updater_normalizes_source_order() -> None:
    assert normalize_order(["ghproxy", "bogus", "github", "ghproxy"]) == [
        "ghproxy",
        "github",
        "gh-proxy",
        "ghproxy-net",
    ]


def test_workbench_updater_version_and_asset_selection(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    release = LatestRelease(
        tag="v3.0.1",
        version="3.0.1",
        name="v3.0.1",
        body="",
        html_url="",
        prerelease=False,
        published_at="",
        assets=[ReleaseAsset("KaraokeHelper-windows.zip", 10, "https://example.invalid/app.zip")],
    )

    assert is_newer_version("3.0.1", "3.0.0")
    assert current_asset_name() == "KaraokeHelper-windows.zip"
    assert release.pick_primary_asset("KaraokeHelper-windows.zip") is not None
