from __future__ import annotations

from types import SimpleNamespace

import pytest

from strange_uta_game.frontend.editor.timing_interface import EditorInterface


class _FakeCommandManager:
    def __init__(self, command: object) -> None:
        self._command = command

    def get_last_undone_command(self) -> object:
        return self._command

    def get_last_redone_command(self) -> object:
        return self._command


class _FakeTimingService:
    def __init__(self, command: object) -> None:
        self.command_manager = _FakeCommandManager(command)
        self.position = SimpleNamespace(line_idx=0, char_idx=0)

    def can_undo(self) -> bool:
        return True

    def can_redo(self) -> bool:
        return True

    def undo(self) -> None:
        pass

    def redo(self) -> None:
        pass

    def get_current_position(self):
        return self.position


class _FakePreview:
    def set_focus_position(self, line_idx: int, char_idx: int) -> None:
        self.focus = (line_idx, char_idx)


@pytest.mark.parametrize("handler_name", ["_on_undo", "_on_redo"])
def test_non_structural_history_change_refreshes_preview(handler_name: str) -> None:
    calls: list[str] = []
    timing_service = _FakeTimingService(command=object())
    editor = SimpleNamespace(
        _timing_service=timing_service,
        preview=_FakePreview(),
        refresh_lyric_display=lambda: calls.append("preview"),
        _update_time_tags_display=lambda: calls.append("timeline"),
        _apply_checkpoint_position=lambda position: calls.append("checkpoint"),
        _update_status=lambda: calls.append("status"),
    )
    editor._sync_focus_from_timing_service = lambda: (
        EditorInterface._sync_focus_from_timing_service(editor)
    )

    getattr(EditorInterface, handler_name)(editor)

    assert calls == ["preview", "timeline", "checkpoint", "status"]
    assert editor.preview.focus == (0, 0)
