from __future__ import annotations

import json
import subprocess

from krok_helper.network import proxy_cli_args_for_app_settings, subprocess_env_for_app_settings
from krok_helper.settings import AppSettings
from krok_helper.video_download.ytdlp_service import YtDlpService


def _settings_with_manual_proxy() -> AppSettings:
    return AppSettings(
        updater={
            "proxy": {
                "mode": "manual",
                "manual_url": "127.0.0.1:7890",
            }
        }
    )


def test_global_proxy_builds_cli_args_and_subprocess_env() -> None:
    settings = _settings_with_manual_proxy()

    assert proxy_cli_args_for_app_settings(settings) == ["--proxy", "http://127.0.0.1:7890"]
    env = subprocess_env_for_app_settings(settings)
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"


def test_ytdlp_cli_extract_uses_global_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = YtDlpService(app_settings=_settings_with_manual_proxy())
    monkeypatch.setattr(service, "_find_ytdlp_cli", lambda: "yt-dlp")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"title": "ok", "duration": 1, "formats": []}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    service._extract_info_with_cli("https://www.youtube.com/watch?v=abc")

    command = captured["command"]
    assert isinstance(command, list)
    assert command[1:3] == ["--proxy", "http://127.0.0.1:7890"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"

