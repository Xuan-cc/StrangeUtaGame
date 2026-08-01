"""ProjectStore.export_dir 优先级测试。

导出专用目录与 working_dir 的关键差异：用户显式设置的
export.default_export_dir 优先于已保存项目目录和音频/歌词目录。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from strange_uta_game.frontend.project_store import ProjectStore


def _app():
    return QCoreApplication.instance() or QCoreApplication([])


def _patch_settings(monkeypatch, *, default_export_dir="", last_export_dir=""):
    """把 AppSettings 替换成只回放给定 export.* 值的假实现。"""

    class _FakeSettings:
        def get(self, key, default=None):
            if key == "export.default_export_dir":
                return default_export_dir
            if key == "export.last_export_dir":
                return last_export_dir
            return default

    monkeypatch.setattr(
        "strange_uta_game.frontend.settings.app_settings.AppSettings",
        _FakeSettings,
    )


def test_default_export_dir_wins_over_saved_project_dir(monkeypatch, tmp_path):
    _ = _app()
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    default_dir = tmp_path / "default_export"
    default_dir.mkdir()
    _patch_settings(monkeypatch, default_export_dir=str(default_dir))

    store = ProjectStore()
    store._project = object()
    store._save_path = str(proj_dir / "song.sug")

    # 显式指定的导出目录优先级最高
    assert store.export_dir == str(default_dir)


def test_default_export_dir_wins_over_audio_dir(monkeypatch, tmp_path):
    _ = _app()
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    default_dir = tmp_path / "default_export"
    default_dir.mkdir()
    _patch_settings(monkeypatch, default_export_dir=str(default_dir))

    store = ProjectStore()
    store._project = object()
    # 未保存（无 save_path），仅加载了音频
    store._audio_path = str(audio_dir / "track.wav")

    # 默认导出目录应压过音频目录
    assert store.export_dir == str(default_dir)
    # 而 working_dir（保存语义）仍回退到音频目录，不受默认导出目录影响
    assert store.working_dir == str(audio_dir)


def test_falls_back_to_working_dir_when_default_unset(monkeypatch, tmp_path):
    _ = _app()
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    _patch_settings(monkeypatch, default_export_dir="")

    store = ProjectStore()
    store._project = object()
    store._audio_path = str(audio_dir / "track.wav")

    # 未设默认导出目录 → 回退到 working_dir（音频目录）
    assert store.export_dir == str(audio_dir)


def test_nonexistent_default_export_dir_is_ignored(monkeypatch, tmp_path):
    _ = _app()
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    _patch_settings(
        monkeypatch, default_export_dir=str(tmp_path / "does_not_exist")
    )

    store = ProjectStore()
    store._project = object()
    store._audio_path = str(audio_dir / "track.wav")

    # 配置的目录不存在 → 跳过，回退到 working_dir
    assert store.export_dir == str(audio_dir)
