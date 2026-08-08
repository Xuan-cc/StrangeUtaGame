"""打轴编辑撤销命令的前端兼容入口。

实际实现已迁入 ``strange_uta_game.backend.application.commands.SentenceSnapshotCommand``，
本模块仅保留 ``_SentenceSnapshotCommand`` 下划线别名以兼容历史 import 路径
（``from ...editor.timing.commands import _SentenceSnapshotCommand``）。
"""

from __future__ import annotations

from typing import Callable

from strange_uta_game.backend.application.commands import (
    Command,
    SentenceSnapshotCommand,
)


class PlaybackRangeCommand(Command):
    """Apply an undoable change to the editor's transient playback range."""

    def __init__(
        self,
        apply_state: Callable[[int | None, int | None], None],
        old_state: tuple[int | None, int | None],
        new_state: tuple[int | None, int | None],
        description: str,
    ) -> None:
        self._apply_state = apply_state
        self._old_state = old_state
        self._new_state = new_state
        self._description = description

    def execute(self) -> None:
        self._apply_state(*self._new_state)

    def undo(self) -> None:
        self._apply_state(*self._old_state)

    @property
    def description(self) -> str:
        return self._description

# 历史下划线命名兼容别名：新代码请直接使用 ``SentenceSnapshotCommand``。
_SentenceSnapshotCommand = SentenceSnapshotCommand

__all__ = [
    "PlaybackRangeCommand",
    "SentenceSnapshotCommand",
    "_SentenceSnapshotCommand",
]
