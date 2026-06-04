"""编辑器顶部工具栏。

保存/加载音频/撤销/重做/重置打轴等快捷按钮。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit
from qfluentwidgets import (
    Action,
    CaptionLabel,
    DropDownPushButton,
    FluentIcon as FIF,
    PushButton,
    RoundMenu,
)


# ──────────────────────────────────────────────
# 工具栏
# ──────────────────────────────────────────────

class EditorToolBar(QFrame):
    """编辑器工具栏 - 保存/加载/批量变更/修改字符/插入导唱符/偏移调整"""

    save_clicked = pyqtSignal()
    save_as_clicked = pyqtSignal()
    new_project_clicked = pyqtSignal()
    load_project_clicked = pyqtSignal()
    load_audio_clicked = pyqtSignal()
    load_lyrics_clicked = pyqtSignal()
    bulk_change_clicked = pyqtSignal()
    modify_line_clicked = pyqtSignal()
    analyze_rubies_clicked = pyqtSignal()
    analyze_rubies_by_line_clicked = pyqtSignal()
    analyze_rubies_selected_clicked = pyqtSignal()
    open_fulltext_clicked = pyqtSignal()
    modify_char_clicked = pyqtSignal()
    insert_guide_clicked = pyqtSignal()
    delete_rubies_by_type_clicked = pyqtSignal()
    set_singer_by_line_clicked = pyqtSignal()
    apply_singer_clicked = pyqtSignal()
    singer_manager_clicked = pyqtSignal()
    complete_timestamp_clicked = pyqtSignal()          # 补全时间戳
    separate_symbol_timestamp_clicked = pyqtSignal()   # 分离符号时间戳
    adjust_raw_timestamp_clicked = pyqtSignal()        # 调整原始时间戳
    offset_changed = pyqtSignal(int)  # 偏移量变化（毫秒）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        # 文件管理下拉菜单
        self.btn_load = DropDownPushButton("文件管理", self)
        self.btn_load.setIcon(FIF.FOLDER)
        self.btn_load.setFixedHeight(32)
        load_menu = RoundMenu(parent=self.btn_load)
        load_menu.addAction(Action(FIF.ADD, "新建项目", self, triggered=self.new_project_clicked.emit))
        load_menu.addAction(Action(FIF.FOLDER, "加载项目", self, triggered=self.load_project_clicked.emit))
        load_menu.addAction(Action(FIF.SAVE, "保存项目", self, triggered=self.save_clicked.emit))
        load_menu.addAction(Action(FIF.SAVE_AS, "项目另存为", self, triggered=self.save_as_clicked.emit))
        load_menu.addSeparator()
        load_menu.addAction(Action(FIF.MUSIC, "加载音频", self, triggered=self.load_audio_clicked.emit))
        load_menu.addAction(Action(FIF.DOCUMENT, "加载歌词", self, triggered=self.load_lyrics_clicked.emit))
        self.btn_load.setMenu(load_menu)
        layout.addWidget(self.btn_load)

        layout.addSpacing(10)

        # 编辑管理下拉菜单
        self.btn_edit = DropDownPushButton("编辑管理", self)
        self.btn_edit.setIcon(FIF.EDIT)
        self.btn_edit.setFixedHeight(32)
        edit_menu = RoundMenu(parent=self.btn_edit)
        edit_menu.addAction(Action(FIF.EDIT, "修改所选字符", self, triggered=self.modify_char_clicked.emit))
        edit_menu.addAction(Action(FIF.EDIT, "批量变更", self, triggered=self.bulk_change_clicked.emit))
        edit_menu.addAction(Action(FIF.EDIT, "修改选中行", self, triggered=self.modify_line_clicked.emit))
        self.btn_edit.setMenu(edit_menu)
        layout.addWidget(self.btn_edit)

        self.btn_insert_guide = PushButton("插入导唱符", self)
        self.btn_insert_guide.setIcon(FIF.ADD)
        self.btn_insert_guide.setFixedHeight(32)
        self.btn_insert_guide.clicked.connect(self.insert_guide_clicked.emit)
        layout.addWidget(self.btn_insert_guide)

        layout.addSpacing(10)

        # 自动注音管理下拉菜单
        self.btn_ruby = DropDownPushButton("自动注音管理", self)
        self.btn_ruby.setIcon(FIF.SYNC)
        self.btn_ruby.setFixedHeight(32)
        ruby_menu = RoundMenu(parent=self.btn_ruby)
        ruby_menu.addAction(Action(FIF.SYNC, "注音分析", self, triggered=self.analyze_rubies_clicked.emit))
        ruby_menu.addAction(Action(FIF.SYNC, "按行注音分析", self, triggered=self.analyze_rubies_by_line_clicked.emit))
        ruby_menu.addAction(Action(FIF.SYNC, "注音分析所选字符", self, triggered=self.analyze_rubies_selected_clicked.emit))
        ruby_menu.addAction(Action(FIF.DELETE, "按类型删除注音", self, triggered=self.delete_rubies_by_type_clicked.emit))
        self.btn_ruby.setMenu(ruby_menu)
        layout.addWidget(self.btn_ruby)

        # 演唱者相关下拉菜单
        self.btn_singer = DropDownPushButton("演唱者相关", self)
        self.btn_singer.setIcon(FIF.PEOPLE)
        self.btn_singer.setFixedHeight(32)
        singer_menu = RoundMenu(parent=self.btn_singer)
        singer_menu.addAction(Action(FIF.PEOPLE, "演唱者管理", self, triggered=self.singer_manager_clicked.emit))
        singer_menu.addAction(Action(FIF.PEOPLE, "应用演唱者", self, triggered=self.apply_singer_clicked.emit))
        singer_menu.addAction(Action(FIF.PEOPLE, "按行设置演唱者", self, triggered=self.set_singer_by_line_clicked.emit))
        self.btn_singer.setMenu(singer_menu)
        layout.addWidget(self.btn_singer)

        # 全文本编辑（独立按钮，位于演唱者相关与补全时间戳之间）
        self.btn_fulltext = PushButton("全文本编辑", self)
        self.btn_fulltext.setIcon(FIF.EDIT)
        self.btn_fulltext.setFixedHeight(32)
        self.btn_fulltext.clicked.connect(self.open_fulltext_clicked.emit)
        layout.addWidget(self.btn_fulltext)

        # 时间戳工具下拉菜单
        self.btn_timestamp = DropDownPushButton("时间戳工具", self)
        self.btn_timestamp.setIcon(FIF.DATE_TIME)
        self.btn_timestamp.setFixedHeight(32)
        ts_menu = RoundMenu(parent=self.btn_timestamp)
        ts_menu.addAction(Action(FIF.DATE_TIME, "补全时间戳", self, triggered=self.complete_timestamp_clicked.emit))
        ts_menu.addAction(Action(FIF.DATE_TIME, "分离符号时间戳", self, triggered=self.separate_symbol_timestamp_clicked.emit))
        ts_menu.addAction(Action(FIF.DATE_TIME, "调整原始时间戳", self, triggered=self.adjust_raw_timestamp_clicked.emit))
        self.btn_timestamp.setMenu(ts_menu)
        layout.addWidget(self.btn_timestamp)

        layout.addSpacing(10)

        # 整体时间戳偏移调整
        lbl_offset = CaptionLabel("全局偏移:")
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

    def _on_offset_editing_finished(self):
        """偏移输入框编辑完成 — 解析并发射信号"""
        text = self.edit_offset.text().strip()
        try:
            val = int(text)
            val = max(-5000, min(5000, val))
        except ValueError:
            val = 0
        self.edit_offset.setText(str(val))
        self.offset_changed.emit(val)


# ──────────────────────────────────────────────
# 卡拉OK 歌词预览
# ──────────────────────────────────────────────
