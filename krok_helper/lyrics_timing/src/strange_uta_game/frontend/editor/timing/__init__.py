"""打轴编辑子包 — timing_interface.py 的拆分产物。

为了保持向后兼容，``editor/timing_interface.py`` 会从本包 re-export 全部公开符号。
"""

from __future__ import annotations

from .bulk_change_dialog import BulkChangeDialog
from .commands import SentenceSnapshotCommand, _SentenceSnapshotCommand
from .dialogs import (
    CharEditDialog,
    CompleteTimestampDialog,
    InsertGuideSymbolDialog,
    ModifyCharacterDialog,
)
from .file_loader import FileLoader
from .karaoke_preview import KaraokePreview
from .singer_manager_window import MiniSingerManager
from .timeline_widget import TimelineWidget
from .toolbar import EditorToolBar
from .transport_bar import TransportBar

__all__ = [
    "SentenceSnapshotCommand",
    "_SentenceSnapshotCommand",
    "TransportBar",
    "EditorToolBar",
    "FileLoader",
    "KaraokePreview",
    "MiniSingerManager",
    "TimelineWidget",
    "ModifyCharacterDialog",
    "InsertGuideSymbolDialog",
    "CharEditDialog",
    "BulkChangeDialog",
    "CompleteTimestampDialog",
]
