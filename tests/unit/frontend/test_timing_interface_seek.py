from __future__ import annotations

from types import SimpleNamespace

from strange_uta_game.frontend.editor.timing_interface import EditorInterface


class _FakeTimingService:
    def __init__(self) -> None:
        self.seeked_ms = None

    def seek(self, ms: int) -> None:
        self.seeked_ms = ms


class _FakePositionWidget:
    def __init__(self) -> None:
        self.position_ms = None

    def set_position(self, ms: int) -> None:
        self.position_ms = ms


class _FakePreview:
    def __init__(self) -> None:
        self.current_time_ms = None
        self.invalidated_lines = []
        self.dependents_invalidated_for = []

    def set_current_time_ms(self, ms: int) -> None:
        self.current_time_ms = ms

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
