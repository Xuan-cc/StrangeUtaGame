"""句子列表快照撤销命令。

在结构化编辑（批量替换、切片替换、整段重建等）中，先拍下 ``project.sentences``
的深拷贝快照，再让业务代码自由修改，最后把 before/after 两份快照交给本命令，
由 CommandManager 统一纳入 undo/redo 栈。

曾位于 ``frontend/editor/timing/commands.py`` 下，抽入后端通用命令层，
使批量替换、整段重建等非 UI 入口也能复用同一语义。
"""

from __future__ import annotations

from copy import deepcopy
from typing import List

from strange_uta_game.backend.application.commands.base import Command
from strange_uta_game.backend.domain import Project, Sentence


class SentenceSnapshotCommand(Command):
    """基于 ``project.sentences`` 前后快照的结构化编辑撤销命令。"""

    def __init__(
        self,
        project: Project,
        before_sentences: List[Sentence],
        after_sentences: List[Sentence],
        description: str,
    ):
        self._project = project
        self._before_sentences = before_sentences
        self._after_sentences = after_sentences
        self._description = description

    def execute(self) -> None:
        self._project.sentences = deepcopy(self._after_sentences)
        self._project._update_timestamp()

    def undo(self) -> None:
        self._project.sentences = deepcopy(self._before_sentences)
        self._project._update_timestamp()

    @property
    def description(self) -> str:
        return self._description


__all__ = ["SentenceSnapshotCommand"]
