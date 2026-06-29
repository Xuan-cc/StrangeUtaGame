"""波形时间标签拖拽提交槽 EditorInterface._on_timeline_tags_drag_committed 的单元测试。

用 SimpleNamespace 充当 self（与 test_timing_interface_seek.py 同范式），只校验
分组写入 + push_to_ruby 不变式与副作用调用，不依赖 Qt 控件实例化。
"""
from __future__ import annotations

from types import SimpleNamespace

from strange_uta_game.backend.application.command_manager import CommandManager
from strange_uta_game.backend.domain import Character, Project, Ruby, RubyPart, Sentence
from strange_uta_game.frontend.editor.timing_interface import EditorInterface
from strange_uta_game.frontend.editor.timing.timeline_widget import WaveformDisplay


def _fake_wd():
    """WaveformDisplay 的轻量替身：只持有 set_time_tags / _append_time_tag /
    _visible_slice 所需状态，避免实例化 QWidget（绕开 theme 的 Qt 生命周期）。"""
    f = SimpleNamespace(
        _time_tags=[], _warning_time_tags=[], _handle_index={},
        _selected_handles=set(), _last_tags_input=None,
        _running_max_ts=-1, _seen_char_keys=set(), _is_dragging_tags=False,
        update=lambda: None,
    )
    f._append_time_tag = lambda entry: WaveformDisplay._append_time_tag(f, entry)
    f.set_time_tags = lambda tags: WaveformDisplay.set_time_tags(f, tags)
    f._visible_slice = lambda lst, vs, ve: WaveformDisplay._visible_slice(f, lst, vs, ve)
    return f


def _summary(wd):
    return (
        [(t.ts, t.handle, t.label) for t in wd._time_tags],
        [(t.ts, t.handle, t.label) for t in wd._warning_time_tags],
        set(wd._handle_index),
    )


def _full_rebuild(tags):
    f = _fake_wd()
    f.set_time_tags(tags)
    return f


def test_incremental_append_equals_full_rebuild():
    """顺序打轴（末尾追加单调标签）增量结果必须与全量重建一致。"""
    T0 = [(100, "a", 0, 0, 0, False, "a"), (200, "a", 0, 0, 1, False, "b"),
          (300, "b", 0, 1, 0, False, None)]
    new = (400, "c", 0, 2, 0, False, "c")
    inc = _fake_wd()
    inc.set_time_tags(T0)
    inc.set_time_tags(T0 + [new])          # 走快路径
    assert _summary(inc) == _summary(_full_rebuild(T0 + [new]))


def test_incremental_append_nonmonotonic():
    """末尾追加但 ts 回退（非单调）→ 进警告表，仍与全量一致。"""
    T0 = [(100, "a", 0, 0, 0, False, None), (300, "b", 0, 1, 0, False, None)]
    new = (250, "c", 0, 2, 0, False, None)   # ts < running_max=300
    inc = _fake_wd()
    inc.set_time_tags(T0)
    inc.set_time_tags(T0 + [new])
    assert _summary(inc) == _summary(_full_rebuild(T0 + [new]))
    assert any(t.handle == (0, 2, 0, False) for t in inc._warning_time_tags)


def test_non_append_falls_back_to_full():
    """改动已有时间戳（同长度，非末尾追加）必须回退全量重建并正确。"""
    T0 = [(100, "a", 0, 0, 0, False, "a"), (200, "a", 0, 0, 1, False, "b"),
          (300, "b", 0, 1, 0, False, None)]
    T_edit = [(100, "a", 0, 0, 0, False, "a"), (999, "a", 0, 0, 1, False, "b"),
              (300, "b", 0, 1, 0, False, None)]
    inc = _fake_wd()
    inc.set_time_tags(T0)
    inc.set_time_tags(T_edit)
    assert _summary(inc) == _summary(_full_rebuild(T_edit))


def test_update_time_tags_skipped_when_waveform_hidden():
    """波形隐藏时跳过 collect + set_time_tags，只标脏（省 CPU、降主线程占用）。"""
    collected, set_calls = [], []
    proj = SimpleNamespace(
        collect_all_global_timestamp_ms_with_chars=lambda: (collected.append(1) or []),
        sentences=[],
    )
    timeline = SimpleNamespace(
        is_waveform_visible=lambda: False,
        set_time_tags=lambda t: set_calls.append(t),
    )
    ed = SimpleNamespace(
        _project=proj, timeline=timeline,
        _time_tags_update_timer=SimpleNamespace(isActive=lambda: False, stop=lambda: None),
    )
    EditorInterface._update_time_tags_display(ed)
    assert collected == [] and set_calls == []
    assert ed._timetags_dirty_while_hidden is True


def test_update_time_tags_runs_when_visible():
    collected, set_calls = [], []
    proj = SimpleNamespace(
        collect_all_global_timestamp_ms_with_chars=lambda: (collected.append(1) or []),
        sentences=[],
    )
    timeline = SimpleNamespace(
        is_waveform_visible=lambda: True,
        set_time_tags=lambda t: set_calls.append(t),
    )
    ed = SimpleNamespace(
        _project=proj, timeline=timeline,
        _time_tags_update_timer=SimpleNamespace(isActive=lambda: False, stop=lambda: None),
    )
    EditorInterface._update_time_tags_display(ed)
    assert collected == [1] and len(set_calls) == 1


def test_reshow_waveform_flushes_dirty():
    refreshed = []
    ed = SimpleNamespace(
        timeline=SimpleNamespace(updateGeometry=lambda: None),
        preview=SimpleNamespace(updateGeometry=lambda: None),
        updateGeometry=lambda: None,
        _timetags_dirty_while_hidden=True,
        _update_time_tags_display=lambda: refreshed.append(1),
    )
    EditorInterface._on_waveform_visibility_changed(ed, True)
    assert refreshed == [1] and ed._timetags_dirty_while_hidden is False


def test_visible_slice():
    wd = _fake_wd()
    wd.set_time_tags([(t, "x", 0, i, 0, False, None)
                      for i, t in enumerate([10, 100, 200, 300, 400, 500])])
    assert [t.ts for t in wd._visible_slice(wd._time_tags, 150, 350)] == [200, 300]


def _lbl(a, b, handle):
    # (a, b, tag, color, text)；tag 只需 .handle
    return (a, b, SimpleNamespace(handle=handle), None, "x")


def test_lyrics_change_refreshes_waveform():
    """导入歌词/节奏点/注音变更时，波形 timetag 也必须刷新（此前只刷 preview）。"""
    calls = SimpleNamespace(tags=0, lyric=0, status=0)
    editor = SimpleNamespace(
        timeline=SimpleNamespace(clear_tag_selection=lambda: None),
        refresh_lyric_display=lambda: setattr(calls, "lyric", calls.lyric + 1),
        _update_time_tags_display=lambda: setattr(calls, "tags", calls.tags + 1),
        _update_status=lambda: setattr(calls, "status", calls.status + 1),
    )
    for ct in ("lyrics", "checkpoints", "rubies"):
        EditorInterface._on_data_changed(editor, ct)
    assert calls.tags == 3      # 波形每次都刷新
    assert calls.lyric == 3 and calls.status == 3


def test_label_layout_leftmost_priority():
    """无选中时，重叠标签从左到右贪心，左侧优先。"""
    self = SimpleNamespace(_selected_handles=set())
    labels = [_lbl(0, 30, (0, 0, 0, False)),
              _lbl(20, 50, (0, 1, 0, False)),   # 与 0-30 重叠 → 丢弃
              _lbl(60, 90, (0, 2, 0, False))]
    drawn = [it[2].handle for it in WaveformDisplay._resolve_label_layout(self, labels)]
    assert drawn == [(0, 0, 0, False), (0, 2, 0, False)]


def test_label_layout_selected_priority():
    """选中标签无条件显示，与之重叠的左侧未选中标签反被丢弃。"""
    self = SimpleNamespace(_selected_handles={(0, 1, 0, False)})
    labels = [_lbl(0, 30, (0, 0, 0, False)),
              _lbl(20, 50, (0, 1, 0, False)),   # 选中
              _lbl(60, 90, (0, 2, 0, False))]
    drawn = {it[2].handle for it in WaveformDisplay._resolve_label_layout(self, labels)}
    assert (0, 1, 0, False) in drawn          # 选中必显示
    assert (0, 0, 0, False) not in drawn       # 与选中重叠 → 让位
    assert (0, 2, 0, False) in drawn


def _make_editor(project):
    calls = SimpleNamespace(
        tags_display=0, lyric=0, line_info=0, notified=[], synced=[], undo_registered=[]
    )
    store = SimpleNamespace(notify=lambda ct: calls.notified.append(ct))

    def _register_undo(before, line, char, desc):
        calls.undo_registered.append((line, char, desc, len(before)))

    editor = SimpleNamespace(
        _project=project,
        _store=store,
        tr=lambda s: s,
        _register_timestamp_undo=_register_undo,
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
    # 已注册撤销命令（支持 Ctrl+Z），锚点字符 = handles 首项
    assert calls.undo_registered == [(0, 0, "拖动时间标签", 1)]


def test_zero_delta_registers_no_undo():
    project, ch = _project_with_ruby_char()
    editor, calls = _make_editor(project)
    EditorInterface._on_timeline_tags_drag_committed(editor, [(0, 0, 1, False)], 0)
    assert calls.undo_registered == []


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


def test_drag_then_undo_restores_timestamps():
    """端到端：真实 CommandManager + _register_timestamp_undo，拖拽后 undo 还原。"""
    project, _ = _project_with_ruby_char()
    cm = CommandManager()
    calls = SimpleNamespace(tags_display=0, lyric=0, line_info=0, notified=[], synced=[])
    editor = SimpleNamespace(
        _project=project,
        _store=None,
        tr=lambda s: s,
        _timing_service=SimpleNamespace(command_manager=cm),
        _current_line_idx=0,
        preview=SimpleNamespace(_current_char_idx=0),
        _update_time_tags_display=lambda: None,
        refresh_lyric_display=lambda: None,
        _update_line_info=lambda: None,
        _sync_preview_to_handle=lambda l, c, cp: None,
    )
    # 绑定真实的 _register_timestamp_undo
    editor._register_timestamp_undo = (
        lambda before, l, c, d: EditorInterface._register_timestamp_undo(editor, before, l, c, d)
    )

    EditorInterface._on_timeline_tags_drag_committed(editor, [(0, 0, 1, False)], 300)
    assert editor._project.sentences[0].characters[0].timestamps == [1000, 1800]
    assert cm.can_undo()

    cm.undo()
    # undo 后 sentences 被 deepcopy 替换，需重新从 project 取
    assert editor._project.sentences[0].characters[0].timestamps == [1000, 1500]

    cm.redo()
    assert editor._project.sentences[0].characters[0].timestamps == [1000, 1800]


def test_clamp_floor_at_zero():
    project, ch = _project_with_ruby_char()
    editor, _ = _make_editor(project)
    # 大幅左移：第一个 cp 被夹到 0（安全网，逐 cp max(0)）
    EditorInterface._on_timeline_tags_drag_committed(
        editor, [(0, 0, 0, False)], -5000
    )
    assert ch.timestamps[0] == 0
