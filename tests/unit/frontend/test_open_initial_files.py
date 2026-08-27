"""独立运行拖入文件启动（open_initial_files / classify_supported_file）的单元测试。

覆盖三块：
1. classify_supported_file 的扩展名分类（与打轴页拖拽同一套集合）；
2. MainWindow.open_initial_files 的分流：.sug 优先开项目；歌词/音频/视频
   新建项目并加载对应文件；歌词+音频合并进同一新项目；
3. 启动链尾段：拖入启动跳过闪退恢复；缓存清理迁移到链尾并以
   「音频在用（已载/在载/视频提取在途）」为守卫。
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
    assert classify_supported_file("sub.srt") == "lyric"
    assert classify_supported_file("sub.ASS") == "lyric"
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
        ("a.krl", True), ("a.srt", True), ("a.ass", True),
        ("a.mp3", True), ("a.mp4", True),
        ("a.zip", False), ("a.docx", False),
    ]:
        assert loader.can_accept_drop(name) is expected, name
        assert (classify_supported_file(name) is not None) is expected, name


# ── MainWindow.open_initial_files 分流（unbound 调用 + 桩 self） ────────

class _Store:
    def __init__(self):
        self.working_dirs = []
        self.audio_path = None

    def set_working_dir(self, file_path):
        self.working_dirs.append(file_path)


class _FileLoaderStub:
    def __init__(self, ready=True):
        self._timing_service = object() if ready else None
        self._loading_thread = None  # 视频提取线程（None = 不在途）
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
        self._audio_loading = False


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


def test_open_initial_files_srt_and_ass_load_like_other_lyrics(tmp_path, monkeypatch):
    # 自启（拖到程序图标/双击关联文件）对 SRT/ASS 与其他歌词格式同一分流：
    # classify → "lyric" → load_lyrics（新建项目并加载）。
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\ntest\n", encoding="utf-8")
    ass = tmp_path / "sub.ass"
    ass.write_text(
        "[Script Info]\n[Events]\nFormat: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hi\n",
        encoding="utf-8",
    )

    for path in (srt, ass):
        loader = _FileLoaderStub()
        stub = _WindowStub(loader)
        bars = _run_open_initial_files(stub, [str(path)], monkeypatch)

        assert loader.calls == [("load_lyrics", str(path), True)], path
        assert stub._opened_initial_files is True
        assert bars == []


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


# ── 启动链：闪退恢复让位 与 链尾缓存清理守卫 ─────────────────────────────

def _run_update_check_done(stub, monkeypatch):
    """绑定真实 _maybe_clear_audio_cache/_audio_in_use 后执行 _on_update_check_done。"""
    from types import MethodType

    stub.check_crash_recovery = lambda: stub.calls.append(("crash_recovery",))
    stub._schedule_network_dict_auto_update = lambda: None
    stub._clear_all_audio_cache = lambda: stub.calls.append(("clear_cache",))
    stub._maybe_clear_audio_cache = MethodType(
        main_window_mod.MainWindow._maybe_clear_audio_cache, stub
    )
    stub._audio_in_use = MethodType(
        main_window_mod.MainWindow._audio_in_use, stub
    )
    monkeypatch.setattr(
        main_window_mod.QApplication, "activeModalWidget",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        main_window_mod.QTimer, "singleShot",
        staticmethod(lambda ms, fn: None),
    )
    main_window_mod.MainWindow._on_update_check_done(stub)


def test_update_check_done_normal_startup_recovers_then_clears(monkeypatch):
    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    _run_update_check_done(stub, monkeypatch)

    assert stub.calls == [("crash_recovery",), ("clear_cache",)]


def test_update_check_done_skips_recovery_but_clears_for_lyric_only_drag(monkeypatch):
    # 拖入启动跳过闪退恢复；纯歌词拖入会话没有音频，链尾仍清缓存
    # （旧实现按标志一律跳过，此为收窄后的行为）。
    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    stub._opened_initial_files = True
    _run_update_check_done(stub, monkeypatch)

    assert stub.calls == [("clear_cache",)]


def test_update_check_done_skips_clear_when_audio_loaded(monkeypatch):
    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    stub._store.audio_path = r"C:\song.mp3"
    _run_update_check_done(stub, monkeypatch)

    assert stub.calls == [("crash_recovery",)]


def test_update_check_done_skips_clear_when_audio_loading(monkeypatch):
    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    stub.editorInterface._audio_loading = True
    _run_update_check_done(stub, monkeypatch)

    assert stub.calls == [("crash_recovery",)]


def test_update_check_done_skips_clear_when_video_extracting(monkeypatch):
    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    loader._loading_thread = object()
    _run_update_check_done(stub, monkeypatch)

    assert stub.calls == [("crash_recovery",)]


def test_startup_checks_only_starts_update_check():
    # 缓存清理已迁移到启动链末尾，_startup_checks 不再触碰。
    loader = _FileLoaderStub()
    stub = _WindowStub(loader)
    stub._check_for_app_update = lambda: stub.calls.append(("update_check",))

    main_window_mod.MainWindow._startup_checks(stub)

    assert stub.calls == [("update_check",)]
