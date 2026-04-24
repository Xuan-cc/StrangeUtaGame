"""打轴编辑撤销命令。

从 timing_interface.py 拆出，仅包含 ``_SentenceSnapshotCommand``。
为了兼容既有 ``from strange_uta_game.frontend.editor.timing_interface import _SentenceSnapshotCommand``
的 late-import 用法，``timing_interface.py`` 会 re-export 本符号。
"""

from __future__ import annotations

from copy import deepcopy
from typing import List

from strange_uta_game.backend.application.commands.base import Command
from strange_uta_game.backend.domain import Project, Sentence


class _SentenceSnapshotCommand(Command):
    """基于句子列表快照的结构编辑撤销命令。"""

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


# ──────────────────────────────────────────────
# 播放控制栏
# ──────────────────────────────────────────────
