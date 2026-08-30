"""全局偏移变更 → 波形时间标签自动刷新（回归）。

Tag 时间戳取自 project 的**带偏移**全局时间戳（collect_all_global_
timestamp_ms_with_chars），偏移变化后必须重新 collect + set_time_tags，
否则标签停在旧位置不跟随（用户实测：改全局偏移后 Tag 不动）。
入口两条：工具栏偏移框（_on_offset_changed）与设置页（_apply_settings_
inner 的 render_offset 块），两条都应以 33ms 防抖调度刷新。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from strange_uta_game.backend.domain import Character, Project, Sentence, Singer
from strange_uta_game.frontend.editor.timing_interface import EditorInterface
from strange_uta_game.frontend.editor.timing.timeline_widget import TimelineWidget


def _make_project() -> Project:
    project = Project()
    singer = Singer(name="default")
    project.add_singer(singer)
    sentence = Sentence(singer_id=singer.id)
    ch = Character(char="愛", check_count=2, singer_id=singer.id)
    ch.add_timestamp(1000)
    ch.add_timestamp(2000)
    sentence.characters.append(ch)
    project.add_sentence(sentence)
    return project


def _make_editor(qapp) -> SimpleNamespace:
    """轻量编辑器命名空间：绑定真实方法 + 真实 TimelineWidget。

    用 SimpleNamespace 而非 __new__ 的半初始化 QWidget——后者的信号
    连接是死连接（C++ 接收者为空），定时器槽不会执行。
    """
    project = _make_project()

    class _Preview:
        _global_offset_ms = 0

        def set_global_offset(self, offset_ms):
            self._global_offset_ms = offset_ms

    editor = SimpleNamespace(
        _project=project,
        _store=SimpleNamespace(
            project=project,
            error_notify=SimpleNamespace(connect=lambda *a: None),
            notify=lambda *_a: None,
        ),
        preview=_Preview(),
        _timetags_dirty_while_hidden=False,
        _get_setting_interface=lambda: None,
        timeline=TimelineWidget(),
    )
    editor._update_time_tags_display = (
        lambda: EditorInterface._update_time_tags_display(editor)
    )
    editor._schedule_time_tags_update = (
        lambda delay_ms=33: EditorInterface._schedule_time_tags_update(editor, delay_ms)
    )
    from PyQt6.QtCore import QTimer

    timer = QTimer()
    timer.setSingleShot(True)
    timer.setInterval(33)
    timer.timeout.connect(editor._update_time_tags_display)
    editor._time_tags_update_timer = timer
    return editor


@pytest.fixture(autouse=True)
def _no_app_settings_disk_write(monkeypatch):
    """_on_offset_changed 无设置接口时兜底 new AppSettings() 并 save()——
    测试必须拦截，禁止写真实 config.json。"""

    class _StubSettings:
        def set(self, *_a, **_k):
            pass

        def save(self):
            pass

    monkeypatch.setattr(
        "strange_uta_game.frontend.settings.app_settings.AppSettings",
        _StubSettings,
    )


def test_offset_change_moves_timeline_tags(qapp, qtbot):
    """工具栏改全局偏移 → 33ms 防抖后 Tag 时间戳整体平移。"""
    editor = _make_editor(qapp)
    EditorInterface._update_time_tags_display(editor)
    display = editor.timeline.waveform_display
    before = [t.ts for t in display._time_tags]
    assert before == [1000, 2000]

    EditorInterface._on_offset_changed(editor, 500)

    qtbot.waitUntil(lambda: [t.ts for t in display._time_tags] == [1500, 2500])
    # 项目模型与预览同步
    ch = editor._project.sentences[0].characters[0]
    assert ch.global_timestamps == [1500, 2500]
    assert editor.preview._global_offset_ms == 500


def test_offset_change_refresh_is_debounced_single_shot(qapp):
    """刷新走防抖定时器：调用返回时未同步刷新（33ms 后一次）。"""
    editor = _make_editor(qapp)
    EditorInterface._update_time_tags_display(editor)
    display = editor.timeline.waveform_display

    EditorInterface._on_offset_changed(editor, -100)

    assert [t.ts for t in display._time_tags] == [1000, 2000]  # 尚未刷新
    assert editor._time_tags_update_timer.isActive()  # 已调度


def test_offset_change_skips_refresh_when_waveform_hidden(qapp, qtbot):
    """波形隐藏时沿用既有的「标脏 + 恢复可见时补刷」语义。"""
    editor = _make_editor(qapp)
    EditorInterface._update_time_tags_display(editor)
    editor.timeline._waveform_visible = False

    EditorInterface._on_offset_changed(editor, 500)

    qtbot.waitUntil(lambda: editor._timetags_dirty_while_hidden is True)
    display = editor.timeline.waveform_display
    assert [t.ts for t in display._time_tags] == [1000, 2000]
