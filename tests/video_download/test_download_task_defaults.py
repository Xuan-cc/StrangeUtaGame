from __future__ import annotations

import json

from krok_helper.settings import AppSettings, load_app_settings
from krok_helper.video_download.download_task import (
    DownloadOptions,
    DownloadTask,
    SOURCE_BILIBILI,
    TASK_STATUS_WAITING,
)


def test_download_task_thumbnail_default_false() -> None:
    task = DownloadTask(task_id="1", url="url", title="title", source=SOURCE_BILIBILI)

    assert task.download_thumbnail is False


def test_download_task_merge_default_true() -> None:
    task = DownloadTask(task_id="1", url="url", title="title", source=SOURCE_BILIBILI)

    assert task.merge_video_audio is True


def test_download_task_status_default_waiting() -> None:
    task = DownloadTask(task_id="1", url="url", title="title", source=SOURCE_BILIBILI)

    assert task.status == TASK_STATUS_WAITING


def test_download_options_thumbnail_default_false(tmp_path) -> None:
    options = DownloadOptions(save_dir=str(tmp_path))

    assert options.download_thumbnail is False


def test_download_options_merge_default_true(tmp_path) -> None:
    options = DownloadOptions(save_dir=str(tmp_path))

    assert options.merge_video_audio is True


def test_settings_dataclass_thumbnail_default_false() -> None:
    assert AppSettings().video_download_download_thumbnail is False


def test_settings_load_missing_thumbnail_defaults_false(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"video_download_save_dir": "D:/Downloads"}), encoding="utf-8")
    monkeypatch.setattr("krok_helper.settings.get_settings_path", lambda: settings_path)

    assert load_app_settings().video_download_download_thumbnail is False


def test_settings_load_preserves_user_explicit_true(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"video_download_download_thumbnail": True}), encoding="utf-8")
    monkeypatch.setattr("krok_helper.settings.get_settings_path", lambda: settings_path)

    assert load_app_settings().video_download_download_thumbnail is True
