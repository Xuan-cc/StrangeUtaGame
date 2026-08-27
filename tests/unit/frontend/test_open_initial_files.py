"""独立运行拖入文件启动（open_initial_files / classify_supported_file）的单元测试。

覆盖三块：
1. classify_supported_file 的扩展名分类（与打轴页拖拽同一套集合）；
2. MainWindow.open_initial_files 的分流：.sug 优先开项目；歌词/音频/视频
   新建项目并加载对应文件；歌词+音频合并进同一新项目；
3. 启动即拖入文件时跳过闪退恢复（_on_update_check_done 的守卫）。
"""

from __future__ import annotations

from strange_uta_game.frontend.editor.timing import file_loader as file_loader_mod
from strange_uta_game.frontend.editor.timing.file_loader import (
    FileLoader,
    classify_supported_file,
)
from strange_uta_game.frontend import main_window as main_window_mod


# ── classify_supported_file ──────────────────────────────────────────────

def test_classify_supported_file_covers_all_drop_kinds(tmp_path):
    assert classify_supported_file("song.sug") == "project"
    assert classify_supported_file("lyrics.LRC") == "lyric"
    assert classify_supported_file("歌詞.krl") == "lyric"
    assert classify_supported_file("song.mp3") == "audio"
    assert classify_supported_file("song.DSF") == "audio"
    assert classify_supported_file("video.mp4") == "video"
    assert classify_supported_file("archive.zip") is None
    assert classify_supported_file("noext") is None


def test_classify_supported_file_matches_can_accept_drop(tmp_path):
    # can_accept_drop（窗口拖拽）与 classify（启动文件分流）必须同一套判定。
    editor = _make_editor()
    loader = FileLoader(editor)
    for name, expected in [
        ("a.sug", True), ("a.lrc", True), ("a.txt", True), ("a.kra", True),
        ("a.krl", True), ("a.mp3", True), ("a.mp4", True),
        ("a.zip", False), ("a.docx", False),
    ]:
        assert loader.can_accept_drop(name) is expected, name
        assert (classify_supported_file(name) is not None) is expected, name


# ── MainWindow.open_initial_files 分流（unbound 调用 + 桩 self） ────────

class _Store:
    def __init__(self):
        self.working_dirs = []

    def set_working_dir(self, file_path):
        self.working_dirs.append(file_path)


class _FileLoaderStub:
    def __init__(self, ready=True):
        self._timing_service = object() if ready else None
        self.calls = []

    def load_lyrics(self, path, check_unsaved=True):
        self.calls.append(("load_lyrics", path, check_unsaved))

    def load_media(self, path):
        self.calls.append(("load_media", path))

    def create_fresh_project(self):
        self.calls.append(("create_fresh_project",))

    def check_unsaved_changes(self):
        self.calls.append(("check_unsaved_changes",))
        return True


class _EditorStub:
    def __init__(self, file_loader):
        self._file_loader = file_loader


class _WindowStub:
    """仅提供 open_initial_files 触达的 MainWindow 属性/方法。"""

    def __init__(self, file_loader):
        self._store = _Store()
        self.editorInterface = _EditorStub(file_loader)
        self._opened_initial_files = False
        self.calls = []

    def tr(self, text):
        return text

    def switchTo(self, interface):
        self.calls.append(("switchTo", interface))

    def _refresh_frameless(self):
        self.calls.append(("_refresh_frameless",))

    def open_initial_project(self, file_path, check_unsaved=True):
        self.calls.append(("open_initial_project", file_path, check_unsaved))


def _make_editor():
    return type("E", (), {})()


def _run_open_initial_files(stub, paths, monkeypatch):
    from types import MethodType

    stub._require_ready_file_loader = MethodType(
        main_window_mod.MainWindow._require_ready_file_loader, stub
    )
    bars = []
    monkeypatch.setattr(
        main_window_mod, "InfoBar",
        type("InfoBarStub", (), {
            "error": staticmethod(lambda **kw: bars.append(("error", kw))),
            "warning": staticmethod(lambda **kw: bars.append(("warning", kw))),
            "success": staticmethod(lambda **kw: bars.append(("success", kw))),
        }),
    )
    main_window_mod.MainWindow.open_initial_files(stub, paths)
    return bars


def test_open_initial_files_project_wins_over_other_kinds(tmp_path, monkeypatch):
    sug = tmp_path / "song.sug"
    sug.write_text("{}", encoding="utf-8")
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    bars = _run_open_initial_files(stub, [str(mp3), str(sug)], monkeypatch)

    assert stub.calls == [("open_initial_project", str(sug), True)]
    assert loader.calls == []
    assert stub._opened_initial_files is True
    assert bars == []


def test_open_initial_files_lyric_uses_load_lyrics(tmp_path, monkeypatch):
    lrc = tmp_path / "lyrics.lrc"
    lrc.write_text("[00:01.00]测试", encoding="utf-8")

    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    bars = _run_open_initial_files(stub, [str(lrc)], monkeypatch)

    # load_lyrics 自带「新建项目 + 装入歌词」语义，含未保存检测
    assert loader.calls == [("load_lyrics", str(lrc), True)]
    assert ("open_initial_project", str(lrc), True) not in stub.calls
    assert stub._store.working_dirs == [str(lrc)]
    assert stub._opened_initial_files is True


def test_open_initial_files_audio_creates_fresh_project(tmp_path, monkeypatch):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    bars = _run_open_initial_files(stub, [str(mp3)], monkeypatch)

    assert loader.calls == [
        ("check_unsaved_changes",),
        ("create_fresh_project",),
        ("load_media", str(mp3)),
    ]
    assert stub._store.working_dirs == [str(mp3)]


def test_open_initial_files_lyric_plus_audio_share_one_project(tmp_path, monkeypatch):
    lrc = tmp_path / "lyrics.lrc"
    lrc.write_text("[00:01.00]测试", encoding="utf-8")
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    bars = _run_open_initial_files(stub, [str(lrc), str(mp3)], monkeypatch)

    # 歌词在场时项目由 load_lyrics 创建，不再单独 create_fresh_project
    assert loader.calls == [
        ("load_lyrics", str(lrc), True),
        ("load_media", str(mp3)),
    ]
    assert stub._store.working_dirs == [str(lrc), str(mp3)]


def test_open_initial_files_unsupported_shows_warning(tmp_path, monkeypatch):
    doc = tmp_path / "doc.docx"
    doc.write_bytes(b"")

    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    bars = _run_open_initial_files(stub, [str(doc)], monkeypatch)

    assert [kind for kind, _ in bars] == ["warning"]
    assert loader.calls == []
    assert stub._opened_initial_files is False


def test_open_initial_files_missing_file_shows_error(tmp_path, monkeypatch):
    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    bars = _run_open_initial_files(
        stub, [str(tmp_path / "missing.mp3")], monkeypatch
    )

    assert [kind for kind, _ in bars] == ["error"]
    assert loader.calls == []


def test_open_initial_files_drops_request_when_loader_not_ready(tmp_path, monkeypatch):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")

    loader = _FileLoaderStub(ready=False)
    stub = _WindowStub(loader)
    bars = _run_open_initial_files(stub, [str(mp3)], monkeypatch)

    # 未就绪属契约违背：记日志后直接丢弃，不重试、不加载
    assert loader.calls == []
    assert stub.calls == []


# ── 启动即拖入文件时跳过闪退恢复 ─────────────────────────────────────────

def test_update_check_done_skips_crash_recovery_after_initial_files(
    qtbot, monkeypatch,
):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    stub.check_crash_recovery = lambda: stub.calls.append(("crash_recovery",))
    stub._schedule_network_dict_auto_update = lambda: None
    timers = []
    monkeypatch.setattr(
        main_window_mod.QApplication, "activeModalWidget",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        main_window_mod.QTimer, "singleShot",
        staticmethod(lambda ms, fn: timers.append((ms, fn))),
    )

    stub._opened_initial_files = True
    main_window_mod.MainWindow._on_update_check_done(stub)
    assert stub.calls == []  # 跳过恢复
    assert len(timers) == 1  # 仅词典更新调度

    stub._opened_initial_files = False
    main_window_mod.MainWindow._on_update_check_done(stub)
    assert stub.calls == [("crash_recovery",)]


def test_startup_checks_skips_cache_clear_after_initial_files():
    # 清缓存以「尚未加载任何音频」为前提；拖入文件启动时初始媒体可能在途
    # （视频提取/引擎缓存写入），必须跳过，顺延到下次正常启动。
    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    stub._clear_all_audio_cache = lambda: stub.calls.append(("clear_cache",))
    stub._check_for_app_update = lambda: stub.calls.append(("update_check",))

    stub._opened_initial_files = True
    main_window_mod.MainWindow._startup_checks(stub)
    assert stub.calls == [("update_check",)]

    stub._opened_initial_files = False
    main_window_mod.MainWindow._startup_checks(stub)
    assert stub.calls == [
        ("update_check",),
        ("clear_cache",),
        ("update_check",),
    ]
