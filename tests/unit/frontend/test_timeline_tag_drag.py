"""波形时间标签拖拽提交槽 EditorInterface._on_timeline_tags_drag_committed 的单元测试。

用 SimpleNamespace 充当 self（与 test_timing_interface_seek.py 同范式），只校验
分组写入 + push_to_ruby 不变式与副作用调用，不依赖 Qt 控件实例化。
"""
from __future__ import annotations

from types import SimpleNamespace

from strange_uta_game.backend.domain import Character, Project, Ruby, RubyPart, Sentence
from strange_uta_game.frontend.editor.timing_interface import EditorInterface


def _make_editor(project):
    calls = SimpleNamespace(
        tags_display=0, lyric=0, line_info=0, notified=[], synced=[]
    )
    store = SimpleNamespace(notify=lambda ct: calls.notified.append(ct))
    editor = SimpleNamespace(
        _project=project,
        _store=store,
        _update_time_tags_display=lambda: setattr(calls, "tags_display", calls.tags_display + 1),
        refresh_lyric_display=lambda: setattr(calls, "lyric", calls.lyric + 1),
        _update_line_info=lambda: setattr(calls, "line_info", calls.line_info + 1),
        _sync_preview_to_handle=lambda l, c, cp: calls.synced.append((l, c, cp)),
    )
    return editor, calls


def _project_with_ruby_char():
    """单行单字：check_count=2，timestamps=[1000,1500]，ruby=2 段。"""
    project = Project()
    singer = project.get_default_singer()
    s = Sentence.from_text("か", singer.id)
    ch = s.characters[0]
    ch.ruby = Ruby(parts=[RubyPart("か"), RubyPart("あ")])
    ch.check_count = 2
    ch.timestamps = [1000, 1500]
    ch._update_offset_timestamps()
    ch.push_to_ruby()
    project.add_sentence(s)
    return project, s.characters[0]


def test_drag_normal_cp_updates_timestamps_and_ruby():
    project, ch = _project_with_ruby_char()
    editor, calls = _make_editor(project)

    # 拖动第二个 checkpoint (cp=1) +300ms
    EditorInterface._on_timeline_tags_drag_committed(
        editor, [(0, 0, 1, False)], 300
    )

    assert ch.timestamps == [1000, 1800]
    assert ch.global_timestamps == [1000, 1800]
    # ruby 同步：绝对时间戳 + part.offset_ms（相对 timestamps[0]）
    assert ch.ruby.timestamps == [1000, 1800]
    assert ch.ruby.parts[0].offset_ms == 0
    assert ch.ruby.parts[1].offset_ms == 800
    # 副作用四件套 + preview 同步到锚点
    assert calls.tags_display == 1 and calls.lyric == 1 and calls.line_info == 1
    assert calls.notified == ["timetags"]
    assert calls.synced == [(0, 0, 1)]


def test_drag_cp0_rebases_ruby_offsets():
    """拖动 cp0 使 base 改变，兄弟 part.offset_ms 连带重算（相对偏移语义）。"""
    project, ch = _project_with_ruby_char()
    editor, _ = _make_editor(project)

    EditorInterface._on_timeline_tags_drag_committed(
        editor, [(0, 0, 0, False)], -200
    )

    assert ch.timestamps == [800, 1500]
    assert ch.ruby.timestamps == [800, 1500]
    assert ch.ruby.parts[0].offset_ms == 0
    assert ch.ruby.parts[1].offset_ms == 700  # 1500 - 800


def test_multi_select_same_char_rigid_translation():
    """同字符多 cp 一起拖动 = 刚性平移，相对偏移不变。"""
    project, ch = _project_with_ruby_char()
    editor, _ = _make_editor(project)

    EditorInterface._on_timeline_tags_drag_committed(
        editor, [(0, 0, 0, False), (0, 0, 1, False)], 300
    )

    assert ch.timestamps == [1300, 1800]
    assert ch.ruby.parts[0].offset_ms == 0
    assert ch.ruby.parts[1].offset_ms == 500  # 不变


def test_drag_sentence_end_point():
    project = Project()
    singer = project.get_default_singer()
    s = Sentence.from_text("あ", singer.id)
    ch = s.characters[0]
    ch.check_count = 1
    ch.timestamps = [1000]
    ch.is_sentence_end = True
    ch.set_sentence_end_ts(2000)
    project.add_sentence(s)
    editor, calls = _make_editor(project)

    # 句尾呼吸点 cp_idx == check_count == 1, is_end=True
    EditorInterface._on_timeline_tags_drag_committed(
        editor, [(0, 0, 1, True)], 250
    )

    assert ch.sentence_end_ts == 2250
    assert ch.global_sentence_end_ts == 2250
    assert calls.synced == [(0, 0, 1)]


def test_zero_delta_is_noop():
    project, ch = _project_with_ruby_char()
    editor, calls = _make_editor(project)
    EditorInterface._on_timeline_tags_drag_committed(editor, [(0, 0, 1, False)], 0)
    assert ch.timestamps == [1000, 1500]
    assert calls.notified == []


def test_clamp_floor_at_zero():
    project, ch = _project_with_ruby_char()
    editor, _ = _make_editor(project)
    # 大幅左移：第一个 cp 被夹到 0（安全网，逐 cp max(0)）
    EditorInterface._on_timeline_tags_drag_committed(
        editor, [(0, 0, 0, False)], -5000
    )
    assert ch.timestamps[0] == 0
