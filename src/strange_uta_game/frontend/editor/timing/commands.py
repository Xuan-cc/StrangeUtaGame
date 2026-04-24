"""打轴编辑撤销命令的前端兼容入口。

实际实现已迁入 ``strange_uta_game.backend.application.commands.SentenceSnapshotCommand``，
本模块仅保留 ``_SentenceSnapshotCommand`` 下划线别名以兼容历史 import 路径
（``from ...editor.timing.commands import _SentenceSnapshotCommand``）。
"""

from __future__ import annotations

from strange_uta_game.backend.application.commands import SentenceSnapshotCommand

# 历史下划线命名兼容别名：新代码请直接使用 ``SentenceSnapshotCommand``。
_SentenceSnapshotCommand = SentenceSnapshotCommand

__all__ = ["SentenceSnapshotCommand", "_SentenceSnapshotCommand"]
