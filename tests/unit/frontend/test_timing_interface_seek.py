from __future__ import annotations

import time
from types import SimpleNamespace

from strange_uta_game.frontend.editor.timing_interface import EditorInterface


class _FakeTimingService:
    def __init__(self) -> None:
        self.seeked_ms = None

    def seek(self, ms: int) -> None:
        self.seeked_ms = ms


class _FakeRangeTimingService(_FakeTimingService):
    def __init__(self, position_ms: int) -> None:
        super().__init__()
        self.position_ms = position_ms
        self.played = False

    def is_playing(self) -> bool:
        return False

    def get_duration_ms(self) -> int:
        return 10_000

    def get_position_ms(self) -> int:
        return self.position_ms

    def play(self) -> None:
        self.played = True


class _FakePositionWidget:
    def __init__(self) -> None:
        self.position_ms = None
        self.duration_ms = None
        self.playing = None

    def set_position(self, ms: int) -> None:
        self.position_ms = ms

    def set_duration(self, ms: int) -> None:
        self.duration_ms = ms

    def set_playing(self, playing: bool) -> None:
        self.playing = playing


class _FakePreview:
    def __init__(self) -> None:
        self.current_time_ms = None
        self.invalidated_lines = []
        self.dependents_invalidated_for = []
        self.duration_ms = None
        self.playing = None

    def set_current_time_ms(self, ms: int) -> None:
        self.current_time_ms = ms

    def set_duration(self, ms: int) -> None:
        self.duration_ms = ms

    def set_playing(self, playing: bool) -> None:
        self.playing = playing

    def _invalidate_line(self, line_idx: int) -> None:
        self.invalidated_lines.append(line_idx)

    def _invalidate_line_and_dependents(self, line_idx: int) -> None:
        self.dependents_invalidated_for.append(line_idx)


def test_seek_immediately_updates_preview_time():
    target_ms = 1234
    timing_service = _FakeTimingService()
    transport = _FakePositionWidget()
    timeline = _FakePositionWidget()
    preview = _FakePreview()
    editor = SimpleNamespace(
        _timing_service=timing_service,
        transport=transport,
        timeline=timeline,
        preview=preview,
        auto_scroll_suspended=False,
    )

    def suspend_auto_scroll() -> None:
        editor.auto_scroll_suspended = True

    editor._suspend_auto_scroll = suspend_auto_scroll

    EditorInterface._on_seek(editor, target_ms)

    assert editor.auto_scroll_suspended is True
    assert timing_service.seeked_ms == target_ms
    assert transport.position_ms == target_ms
    assert timeline.position_ms == target_ms
    assert preview.current_time_ms == target_ms


def test_timetag_added_delegates_dependent_invalidation_to_preview():
    """editor 仅负责把 changed line 转给 preview；闭包语义由 preview 自行计算。"""
    preview = _FakePreview()
    editor = SimpleNamespace(
        _project=SimpleNamespace(sentences=[object(), object(), object(), object()]),
        preview=preview,
        time_tags_scheduled=False,
        status_updated=False,
    )

    def schedule_time_tags_update() -> None:
        editor.time_tags_scheduled = True

    def update_status() -> None:
        editor.status_updated = True

    editor._schedule_time_tags_update = schedule_time_tags_update
    editor._update_status = update_status
    # 无 char/cp 信息（默认 -1）→ 增量追加返回 False → 回退全量调度
    editor._try_incremental_append = lambda *a: False

    EditorInterface._handle_timetag_added(editor, 2)

    assert preview.dependents_invalidated_for == [2]
    assert preview.invalidated_lines == []  # editor 不再直接逐行 invalidate
    assert editor.time_tags_scheduled is True
    assert editor.status_updated is True


def test_play_starts_from_locked_start_when_position_is_outside_range():
    timing_service = _FakeRangeTimingService(position_ms=8_000)
    position_widgets = [_FakePositionWidget(), _FakePositionWidget()]
    preview = _FakePreview()
    preview.set_playing = lambda playing: None
    preview._last_auto_scroll_line_idx = -1
    preview._auto_scroll_suspended = False
    transport = position_widgets[0]
    timeline = position_widgets[1]
    transport.set_playing = lambda playing: None
    timeline.set_playing = lambda playing: None
    editor = SimpleNamespace(
        _timing_service=timing_service,
        _playback_range_start_ms=2_000,
        _playback_range_end_ms=6_000,
        transport=transport,
        timeline=timeline,
        preview=preview,
        _position_poll_timer=SimpleNamespace(start=lambda: None),
        _auto_scroll_cooldown_timer=SimpleNamespace(stop=lambda: None),
        _auto_scroll_suspended=False,
        _auto_scroll_new_line_reached=False,
        _status_state="paused",
        lbl_status=SimpleNamespace(setText=lambda text: None),
        tr=lambda text: text,
        _update_mode_indicator=lambda playing=None: None,
        _show_runtime_error=lambda text: None,
    )

    EditorInterface._on_play(editor)

    assert timing_service.seeked_ms == 2_000
    assert timing_service.played is True
    assert transport.position_ms == 2_000
    assert timeline.position_ms == 2_000
    assert preview.current_time_ms == 2_000


def test_poll_pauses_exactly_at_locked_end_at_1_5x_speed():
    class Engine:
        playing = True

        def is_playing(self) -> bool:
            return self.playing

    engine = Engine()

    class TimingService:
        _audio_engine = engine
        seeked_ms = None
        paused = False

        def get_position_ms(self) -> int:
            return 6_250

        def get_duration_ms(self) -> int:
            return 10_000

        def get_speed(self) -> float:
            return 1.5

        def seek(self, ms: int) -> None:
            self.seeked_ms = ms

        def pause(self) -> None:
            self.paused = True
            engine.playing = False

    service = TimingService()
    transport = _FakePositionWidget()
    timeline = _FakePositionWidget()
    preview = _FakePreview()
    editor = SimpleNamespace(
        _timing_service=service,
        _playback_range_end_ms=6_000,
        _last_polled_duration_ms=None,
        transport=transport,
        timeline=timeline,
        preview=preview,
        y=lambda: 0,
        _position_poll_timer=SimpleNamespace(stop=lambda: None),
        _auto_scroll_cooldown_timer=SimpleNamespace(stop=lambda: None),
        _auto_scroll_suspended=False,
        _auto_scroll_new_line_reached=False,
        _status_state="playing",
        lbl_status=SimpleNamespace(setText=lambda text: None),
        tr=lambda text: text,
        _update_mode_indicator=lambda playing=None: None,
        _validate_all_timestamps=lambda: None,
    )
    editor._on_pause = lambda: EditorInterface._on_pause(editor)

    EditorInterface._poll_audio_position(editor)

    assert service.seeked_ms == 6_000
    assert service.get_speed() == 1.5
    assert service.paused is True
    assert transport.position_ms == 6_000
    assert timeline.position_ms == 6_000
    assert preview.current_time_ms == 6_000
    assert editor._status_state == "range_finished"


def test_page_animation_resume_polls_once_when_audio_finished_during_pause():
    calls = []
    editor = SimpleNamespace(
        _timing_service=SimpleNamespace(is_playing=lambda: False),
        _position_poll_timer=SimpleNamespace(start=lambda: calls.append("start")),
        _status_state="playing",
        _poll_audio_position=lambda: calls.append("poll"),
        _update_mode_indicator=lambda: calls.append("mode"),
    )

    EditorInterface._resume_poll_after_page_animation(editor)

    assert calls == ["poll"]


def test_page_animation_resume_does_not_mark_explicit_pause_as_finished():
    calls = []
    editor = SimpleNamespace(
        _timing_service=SimpleNamespace(is_playing=lambda: False),
        _position_poll_timer=SimpleNamespace(start=lambda: calls.append("start")),
        _status_state="paused",
        _poll_audio_position=lambda: calls.append("poll"),
        _update_mode_indicator=lambda: calls.append("mode"),
    )

    EditorInterface._resume_poll_after_page_animation(editor)

    assert calls == ["mode"]


def test_position_callback_syncs_mode_before_frame_throttle():
    calls = []
    editor = SimpleNamespace(
        _timing_service=SimpleNamespace(is_playing=lambda: False),
        _shortcut_mode_playing=True,
        _last_position_update_time=time.monotonic(),
        _update_mode_indicator=lambda: calls.append("mode"),
    )

    EditorInterface._handle_position_changed(editor, 10_000, 10_000, {})

    assert calls == ["mode"]


def test_manual_pause_selects_edit_mode_without_requerying_engine():
    class StaleTimingService:
        def pause(self) -> None:
            pass

        def is_playing(self) -> bool:
            return True

    selected_modes = []
    editor = SimpleNamespace(
        _timing_service=StaleTimingService(),
        transport=SimpleNamespace(set_playing=lambda playing: None),
        preview=SimpleNamespace(set_playing=lambda playing: None),
        timeline=SimpleNamespace(set_playing=lambda playing: None),
        _status_state="playing",
        lbl_status=SimpleNamespace(setText=lambda text: None),
        tr=lambda text: text,
        _update_mode_indicator=lambda playing=None: selected_modes.append(playing),
        _auto_scroll_suspended=True,
        _auto_scroll_new_line_reached=True,
        _auto_scroll_cooldown_timer=SimpleNamespace(stop=lambda: None),
        _position_poll_timer=SimpleNamespace(stop=lambda: None),
        _validate_all_timestamps=lambda: None,
    )

    EditorInterface._on_pause(editor)

    assert selected_modes == [False]
