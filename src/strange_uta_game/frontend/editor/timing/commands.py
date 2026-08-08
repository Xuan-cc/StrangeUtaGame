"""打轴编辑撤销命令的前端兼容入口。

实际实现已迁入 ``strange_uta_game.backend.application.commands.SentenceSnapshotCommand``，
本模块仅保留 ``_SentenceSnapshotCommand`` 下划线别名以兼容历史 import 路径
（``from ...editor.timing.commands import _SentenceSnapshotCommand``）。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Callable

from strange_uta_game.backend.application.commands import (
    Command,
    SentenceSnapshotCommand,
)
from strange_uta_game.backend.domain import Character, Project


class CharacterSnapshotCommand(Command):
    """Undo one-character edits without copying the entire lyric project."""

    def __init__(
        self,
        project: Project,
        line_idx: int,
        char_idx: int,
        before: Character,
        after: Character,
        description: str,
    ) -> None:
        self._project = project
        self._line_idx = line_idx
        self._char_idx = char_idx
        self._before = deepcopy(before)
        self._after = deepcopy(after)
        self._description = description
        self._initial_execute = True

    def _apply(self, state: Character) -> None:
        if not 0 <= self._line_idx < len(self._project.sentences):
            return
        sentence = self._project.sentences[self._line_idx]
        if not 0 <= self._char_idx < len(sentence.characters):
            return
        sentence.characters[self._char_idx] = deepcopy(state)
        self._project._update_timestamp()

    def execute(self) -> None:
        # Callers mutate the live character before registering the command.
        # Preserve its identity on initial registration; redo applies a copy.
        if self._initial_execute:
            self._initial_execute = False
            self._project._update_timestamp()
            return
        self._apply(self._after)

    def undo(self) -> None:
        self._apply(self._before)

    @property
    def description(self) -> str:
        return self._description


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
    "CharacterSnapshotCommand",
    "PlaybackRangeCommand",
    "SentenceSnapshotCommand",
    "_SentenceSnapshotCommand",
]
