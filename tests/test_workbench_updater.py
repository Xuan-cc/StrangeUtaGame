from __future__ import annotations

import sys

from krok_helper.settings import AppSettings
from krok_helper.updater.settings import UpdaterSettings, ensure_updater_settings
from krok_helper.updater.sources import build_api_urls, build_release_urls, normalize_order
from krok_helper.updater.installer import DEFAULT_APP_EXE_NAME, TMP_DIR_NAME, LaunchPlan
from krok_helper.updater.worker import LatestRelease, ReleaseAsset, current_asset_name, is_newer_version


def test_workbench_updater_uses_workbench_repo_urls() -> None:
    api_urls = build_api_urls(["github"])
    release_urls = build_release_urls(["github"], "v3.0.1", "KaraokeStudio-windows.zip")

    assert api_urls[0][1] == "https://api.github.com/repos/karaoke-studio/karaoke-studio/releases/latest"
    assert release_urls[0][1] == (
        "https://github.com/karaoke-studio/karaoke-studio/"
        "releases/download/v3.0.1/KaraokeStudio-windows.zip"
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
        assets=[ReleaseAsset("KaraokeStudio-windows.zip", 10, "https://example.invalid/app.zip")],
    )

    assert is_newer_version("3.0.1", "3.0.0")
    assert current_asset_name() == "KaraokeStudio-windows.zip"
    assert release.pick_primary_asset("KaraokeStudio-windows.zip") is not None


def test_workbench_updater_launcher_is_workbench_scoped(tmp_path) -> None:
    plan = LaunchPlan(
        app_dir=tmp_path,
        app_exe_name=DEFAULT_APP_EXE_NAME,
        target_version="3.0.1",
        target_tag="v3.0.1",
        asset_name="KaraokeStudio-windows.zip",
        download_urls=[("github", "https://example.invalid/KaraokeStudio-windows.zip")],
        proxy_url="http://127.0.0.1:7890",
    )

    args = plan.command_args(tmp_path / "Updater.exe", current_pid=1234)

    assert TMP_DIR_NAME == "KaraokeStudioUpdater"
    assert DEFAULT_APP_EXE_NAME == "Karaoke Studio.exe"
    assert "--app-exe" in args
    assert args[args.index("--app-exe") + 1] == "Karaoke Studio.exe"
    assert "KaraokeStudio-windows.zip" in args
    assert "StrangeUtaGame" not in " ".join(args)
