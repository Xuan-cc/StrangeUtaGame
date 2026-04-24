"""打轴编辑子包 — timing_interface.py 的拆分产物。

为了保持向后兼容，``editor/timing_interface.py`` 会从本包 re-export 全部公开符号。
"""

from __future__ import annotations

from .commands import _SentenceSnapshotCommand
from .dialogs import (
    CharEditDialog,
    InsertGuideSymbolDialog,
    ModifyCharacterDialog,
)
from .karaoke_preview import KaraokePreview
from .timeline_widget import TimelineWidget
from .toolbar import EditorToolBar
from .transport_bar import TransportBar

__all__ = [
    "_SentenceSnapshotCommand",
    "TransportBar",
    "EditorToolBar",
    "KaraokePreview",
    "TimelineWidget",
    "ModifyCharacterDialog",
    "InsertGuideSymbolDialog",
    "CharEditDialog",
]
