"""编辑器顶部工具栏。

保存/加载音频/撤销/重做/重置打轴等快捷按钮。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit
from qfluentwidgets import FluentIcon as FIF, PushButton


# ──────────────────────────────────────────────
# 工具栏
# ──────────────────────────────────────────────

class EditorToolBar(QFrame):
    """编辑器工具栏 - 保存/加载音频/加载歌词/批量变更/修改字符/插入导唱符/偏移调整"""

    save_clicked = pyqtSignal()
    load_audio_clicked = pyqtSignal()
    load_lyrics_clicked = pyqtSignal()
    bulk_change_clicked = pyqtSignal()
    modify_char_clicked = pyqtSignal()
    insert_guide_clicked = pyqtSignal()
    offset_changed = pyqtSignal(int)  # 偏移量变化（毫秒）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self.btn_save = PushButton("保存项目", self)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setFixedHeight(32)
        self.btn_save.clicked.connect(self.save_clicked.emit)
        layout.addWidget(self.btn_save)

        self.btn_load_audio = PushButton("加载音频", self)
        self.btn_load_audio.setIcon(FIF.MUSIC)
        self.btn_load_audio.setFixedHeight(32)
        self.btn_load_audio.clicked.connect(self.load_audio_clicked.emit)
        layout.addWidget(self.btn_load_audio)

        self.btn_load_lyrics = PushButton("加载歌词", self)
        self.btn_load_lyrics.setIcon(FIF.DOCUMENT)
        self.btn_load_lyrics.setFixedHeight(32)
        self.btn_load_lyrics.clicked.connect(self.load_lyrics_clicked.emit)
        layout.addWidget(self.btn_load_lyrics)

        layout.addSpacing(10)

        self.btn_modify_char = PushButton("修改所选字符", self)
        self.btn_modify_char.setIcon(FIF.EDIT)
        self.btn_modify_char.setFixedHeight(32)
        self.btn_modify_char.clicked.connect(self.modify_char_clicked.emit)
        layout.addWidget(self.btn_modify_char)

        self.btn_insert_guide = PushButton("插入导唱符", self)
        self.btn_insert_guide.setIcon(FIF.ADD)
        self.btn_insert_guide.setFixedHeight(32)
        self.btn_insert_guide.clicked.connect(self.insert_guide_clicked.emit)
        layout.addWidget(self.btn_insert_guide)

        layout.addSpacing(10)

        self.btn_bulk_change = PushButton("批量变更 (Ctrl+H)", self)
        self.btn_bulk_change.setIcon(FIF.EDIT)
        self.btn_bulk_change.setFixedHeight(32)
        self.btn_bulk_change.clicked.connect(self.bulk_change_clicked.emit)
        layout.addWidget(self.btn_bulk_change)

        layout.addSpacing(10)

        # 整体时间戳偏移调整
        lbl_offset = QLabel("Karaoke渲染以及导出偏移:")
        lbl_offset.setStyleSheet("font-size: 11px;")
        layout.addWidget(lbl_offset)
        self.edit_offset = QLineEdit(self)
        self.edit_offset.setText("-100")
        self.edit_offset.setFixedWidth(80)
        self.edit_offset.setFixedHeight(32)
        self.edit_offset.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit_offset.setStyleSheet("font-size: 12px;")
        self.edit_offset.editingFinished.connect(self._on_offset_editing_finished)
        layout.addWidget(self.edit_offset)

        layout.addStretch()

        # 状态标签
        self.lbl_audio = QLabel("未加载音频")
        self.lbl_audio.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(self.lbl_audio)

    def _on_offset_editing_finished(self):
        """偏移输入框编辑完成 — 解析并发射信号"""
        text = self.edit_offset.text().strip()
        try:
            val = int(text)
            val = max(-2000, min(2000, val))
        except ValueError:
            val = 0
        self.edit_offset.setText(str(val))
        self.offset_changed.emit(val)


# ──────────────────────────────────────────────
# 卡拉OK 歌词预览
# ──────────────────────────────────────────────
