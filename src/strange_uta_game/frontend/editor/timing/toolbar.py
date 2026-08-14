"""编辑器顶部工具栏。

保存/加载音频/撤销/重做/重置打轴等快捷按钮。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
from qfluentwidgets import (
    Action,
    CaptionLabel,
    DropDownPushButton,
    FluentIcon as FIF,
    LineEdit,
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
    recent_project_clicked = pyqtSignal(str)
    clear_recent_projects_clicked = pyqtSignal()
    load_audio_clicked = pyqtSignal()
    load_lyrics_clicked = pyqtSignal()
    bulk_change_clicked = pyqtSignal()
    modify_line_clicked = pyqtSignal()
    analyze_rubies_clicked = pyqtSignal()
    analyze_rubies_by_line_clicked = pyqtSignal()
    analyze_rubies_selected_clicked = pyqtSignal()
    analyze_rubies_no_cp_clicked = pyqtSignal()           # 注音分析（不更新节奏点）
    analyze_rubies_by_line_no_cp_clicked = pyqtSignal()   # 按行注音分析（不更新节奏点）
    analyze_rubies_selected_no_cp_clicked = pyqtSignal()  # 注音分析所选字符（不更新节奏点）
    romanize_all_clicked = pyqtSignal()                   # 全部转为罗马字注音（不更新节奏点/不删除注音）
    open_fulltext_clicked = pyqtSignal()
    modify_char_clicked = pyqtSignal()
    insert_guide_clicked = pyqtSignal()
    delete_rubies_by_type_clicked = pyqtSignal()
    set_singer_by_line_clicked = pyqtSignal()
    apply_singer_clicked = pyqtSignal()
    singer_manager_clicked = pyqtSignal()
    complete_timestamp_clicked = pyqtSignal()          # 补全时间戳
    separate_symbol_timestamp_clicked = pyqtSignal()   # 分离符号时间戳
    adjust_raw_timestamp_clicked = pyqtSignal()          # 整体调整原始时间戳
    adjust_raw_timestamp_line_clicked = pyqtSignal()     # 按行调整原始时间戳
    adjust_raw_timestamp_selected_clicked = pyqtSignal() # 调整所选字符原始时间戳
    delete_all_timestamps_clicked = pyqtSignal()              # 删除所有时间戳
    delete_all_timestamps_keep_head_clicked = pyqtSignal()    # 删除所有时间戳（保留行首）
    auto_generate_interlude_guide_clicked = pyqtSignal()      # 自动生成间奏指引
    auto_insert_guide_clicked = pyqtSignal()                  # 根据时间戳自动插入导唱符

    delete_timestamps_selected_clicked = pyqtSignal()      # 删除所选范围时间戳
    analyze_pinyin_clicked = pyqtSignal()                   # 中文拼音注音
    concat_sug_clicked = pyqtSignal()                       # 拼接多个SUG
    ai_timing_clicked = pyqtSignal()                        # AI 打轴（一级入口）
    offset_changed = pyqtSignal(int)  # 偏移量变化（毫秒）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recent_project_paths: list[str] = []
        self.setFixedHeight(40)
        self._init_ui()

    def _init_ui(self):
        tr = self.tr
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        # 文件管理下拉菜单
        self.btn_load = DropDownPushButton(tr("文件管理"), self)
        self.btn_load.setIcon(FIF.FOLDER)
        self.btn_load.setFixedHeight(32)
        self.btn_load.setMinimumWidth(110)
        self.btn_load.setMenu(self._create_load_menu())
        layout.addWidget(self.btn_load)

        layout.addSpacing(10)

        # 编辑管理下拉菜单
        self.btn_edit = DropDownPushButton(tr("编辑管理"), self)
        self.btn_edit.setIcon(FIF.EDIT)
        self.btn_edit.setFixedHeight(32)
        self.btn_edit.setMinimumWidth(110)
        edit_menu = RoundMenu(parent=self.btn_edit)
        edit_menu.addAction(Action(FIF.EDIT, tr("修改所选字符"), self, triggered=self.modify_char_clicked.emit))
        edit_menu.addAction(Action(FIF.EDIT, tr("批量变更"), self, triggered=self.bulk_change_clicked.emit))
        edit_menu.addAction(Action(FIF.EDIT, tr("修改选中行"), self, triggered=self.modify_line_clicked.emit))
        self.btn_edit.setMenu(edit_menu)
        layout.addWidget(self.btn_edit)

        self.btn_insert_guide = DropDownPushButton(tr("插入导唱符"), self)
        self.btn_insert_guide.setIcon(FIF.ADD)
        self.btn_insert_guide.setFixedHeight(32)
        self.btn_insert_guide.setMinimumWidth(110)
        guide_menu = RoundMenu(parent=self.btn_insert_guide)
        guide_menu.addAction(Action(
            FIF.ADD,
            tr("插入导唱符"),
            self,
            triggered=self.insert_guide_clicked.emit,
        ))
        guide_menu.addAction(Action(
            FIF.SYNC,
            tr("自动插入导唱符"),
            self,
            triggered=self.auto_insert_guide_clicked.emit,
        ))
        self.btn_insert_guide.setMenu(guide_menu)
        layout.addWidget(self.btn_insert_guide)

        layout.addSpacing(10)

        # 自动注音管理下拉菜单
        self.btn_ruby = DropDownPushButton(tr("自动注音管理"), self)
        self.btn_ruby.setIcon(FIF.SYNC)
        self.btn_ruby.setFixedHeight(32)
        self.btn_ruby.setMinimumWidth(110)
        ruby_menu = RoundMenu(parent=self.btn_ruby)
        # 第一组：注音分析并自动更新节奏点
        ruby_menu.addAction(Action(FIF.SYNC, tr("全部 · 含节奏点"), self, triggered=self.analyze_rubies_clicked.emit))
        ruby_menu.addAction(Action(FIF.SYNC, tr("按行 · 含节奏点"), self, triggered=self.analyze_rubies_by_line_clicked.emit))
        ruby_menu.addAction(Action(FIF.SYNC, tr("所选 · 含节奏点"), self, triggered=self.analyze_rubies_selected_clicked.emit))
        ruby_menu.addSeparator()
        # 第二组：仅注音分析，保留现有节奏点不动
        ruby_menu.addAction(Action(FIF.SYNC, tr("全部 · 仅注音"), self, triggered=self.analyze_rubies_no_cp_clicked.emit))
        ruby_menu.addAction(Action(FIF.SYNC, tr("按行 · 仅注音"), self, triggered=self.analyze_rubies_by_line_no_cp_clicked.emit))
        ruby_menu.addAction(Action(FIF.SYNC, tr("所选 · 仅注音"), self, triggered=self.analyze_rubies_selected_no_cp_clicked.emit))
        ruby_menu.addSeparator()
        # 第三组：把现有注音/单假名整体转为罗马字（独立操作，不分析/不更新节奏点/不删除注音）
        ruby_menu.addAction(Action(FIF.FONT, tr("全部转为罗马字"), self, triggered=self.romanize_all_clicked.emit))
        ruby_menu.addSeparator()
        ruby_menu.addAction(Action(FIF.DELETE, tr("按类型删除注音"), self, triggered=self.delete_rubies_by_type_clicked.emit))
        ruby_menu.addSeparator()
        ruby_menu.addAction(Action(FIF.FONT, tr("中文拼音注音"), self, triggered=self.analyze_pinyin_clicked.emit))
        self.btn_ruby.setMenu(ruby_menu)
        layout.addWidget(self.btn_ruby)

        # 演唱者相关下拉菜单
        self.btn_singer = DropDownPushButton(tr("演唱者相关"), self)
        self.btn_singer.setIcon(FIF.PEOPLE)
        self.btn_singer.setFixedHeight(32)
        self.btn_singer.setMinimumWidth(110)
        singer_menu = RoundMenu(parent=self.btn_singer)
        singer_menu.addAction(Action(FIF.PEOPLE, tr("演唱者管理"), self, triggered=self.singer_manager_clicked.emit))
        singer_menu.addAction(Action(FIF.PEOPLE, tr("应用演唱者"), self, triggered=self.apply_singer_clicked.emit))
        singer_menu.addAction(Action(FIF.PEOPLE, tr("按行设置演唱者"), self, triggered=self.set_singer_by_line_clicked.emit))
        self.btn_singer.setMenu(singer_menu)
        layout.addWidget(self.btn_singer)

        # 全文本编辑（独立按钮，位于演唱者相关与补全时间戳之间）
        self.btn_fulltext = PushButton(tr("全文本编辑"), self)
        self.btn_fulltext.setIcon(FIF.EDIT)
        self.btn_fulltext.setFixedHeight(32)
        self.btn_fulltext.setMinimumWidth(110)
        self.btn_fulltext.clicked.connect(self.open_fulltext_clicked.emit)
        layout.addWidget(self.btn_fulltext)

        # 时间戳工具下拉菜单
        self.btn_timestamp = DropDownPushButton(tr("时间戳工具"), self)
        self.btn_timestamp.setIcon(FIF.DATE_TIME)
        self.btn_timestamp.setFixedHeight(32)
        self.btn_timestamp.setMinimumWidth(120)
        ts_menu = RoundMenu(parent=self.btn_timestamp)
        ts_menu.addAction(Action(FIF.DATE_TIME, tr("补全时间戳"), self, triggered=self.complete_timestamp_clicked.emit))
        ts_menu.addAction(Action(FIF.DATE_TIME, tr("分离符号时间戳"), self, triggered=self.separate_symbol_timestamp_clicked.emit))
        ts_menu.addSeparator()
        ts_menu.addAction(Action(FIF.DATE_TIME, tr("调整原始时间戳"), self, triggered=self.adjust_raw_timestamp_clicked.emit))
        ts_menu.addAction(Action(FIF.DATE_TIME, tr("按行调整原始时间戳"), self, triggered=self.adjust_raw_timestamp_line_clicked.emit))
        ts_menu.addAction(Action(FIF.DATE_TIME, tr("调整所选字符原始时间戳"), self, triggered=self.adjust_raw_timestamp_selected_clicked.emit))
        ts_menu.addSeparator()
        ts_menu.addAction(Action(FIF.DELETE, tr("删除所有时间戳"), self, triggered=self.delete_all_timestamps_clicked.emit))
        ts_menu.addAction(Action(FIF.DELETE, tr("删除所有时间戳（保留行首）"), self, triggered=self.delete_all_timestamps_keep_head_clicked.emit))

        ts_menu.addAction(Action(FIF.DELETE, tr("删除所选范围时间戳"), self, triggered=self.delete_timestamps_selected_clicked.emit))
        ts_menu.addSeparator()
        ts_menu.addAction(Action(FIF.MUSIC, tr("自动生成间奏指引"), self, triggered=self.auto_generate_interlude_guide_clicked.emit))
        self.btn_timestamp.setMenu(ts_menu)
        layout.addWidget(self.btn_timestamp)

        # AI 打轴：一级入口按钮（standalone / embedded 共用同一弹窗）
        self.btn_ai_timing = PushButton(tr("AI 打轴"), self)
        self.btn_ai_timing.setIcon(FIF.ROBOT)
        self.btn_ai_timing.setFixedHeight(32)
        self.btn_ai_timing.setMinimumWidth(100)
        self.btn_ai_timing.clicked.connect(self.ai_timing_clicked.emit)
        layout.addWidget(self.btn_ai_timing)

        layout.addSpacing(10)

        # 整体时间戳偏移调整
        lbl_offset = CaptionLabel(tr("全局偏移:"))
        layout.addWidget(lbl_offset)
        self.edit_offset = LineEdit(self)
        self.edit_offset.setText("-100")
        self.edit_offset.setMinimumWidth(80)
        self.edit_offset.setMaximumWidth(140)  # 限制上限以防输入框喧宾夺主
        self.edit_offset.setFixedHeight(32)
        self.edit_offset.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit_offset.editingFinished.connect(self._on_offset_editing_finished)
        layout.addWidget(self.edit_offset)

        layout.addStretch()

    def _create_load_menu(self) -> RoundMenu:
        """按当前最近项目列表创建文件菜单。"""
        tr = self.tr
        load_menu = RoundMenu(parent=self.btn_load)
        load_menu.addAction(Action(FIF.ADD, tr("新建项目"), self, triggered=self.new_project_clicked.emit))
        load_menu.addAction(Action(FIF.FOLDER, tr("加载项目"), self, triggered=self.load_project_clicked.emit))
        self._recent_menu = RoundMenu(tr("最近打开的文件"), load_menu)
        self._recent_menu.setIcon(FIF.HISTORY)
        self._rebuild_recent_menu()
        load_menu.addMenu(self._recent_menu)
        load_menu.addAction(Action(FIF.SAVE, tr("保存项目"), self, triggered=self.save_clicked.emit))
        load_menu.addAction(Action(FIF.SAVE_AS, tr("项目另存为"), self, triggered=self.save_as_clicked.emit))
        load_menu.addSeparator()
        load_menu.addAction(Action(FIF.MUSIC, tr("加载音频"), self, triggered=self.load_audio_clicked.emit))
        load_menu.addAction(Action(FIF.DOCUMENT, tr("加载歌词"), self, triggered=self.load_lyrics_clicked.emit))
        load_menu.addSeparator()
        load_menu.addAction(Action(FIF.LINK, tr("多项目拼接"), self, triggered=self.concat_sug_clicked.emit))
        return load_menu

    def _rebuild_recent_menu(self) -> None:
        """原地刷新最近项目子菜单，不替换文件菜单或工具栏。"""
        recent_menu = self._recent_menu
        old_actions = list(recent_menu.actions())
        recent_menu.clear()
        for action in old_actions:
            action.deleteLater()

        if self._recent_project_paths:
            for file_path in self._recent_project_paths:
                path = Path(file_path)
                action = Action(
                    FIF.DOCUMENT,
                    f"{path.name}  —  {path.parent}",
                    recent_menu,
                )
                action.setToolTip(file_path)
                action.triggered.connect(
                    lambda checked=False, p=file_path: self.recent_project_clicked.emit(p)
                )
                recent_menu.addAction(action)
            recent_menu.addSeparator()
            recent_menu.addAction(Action(
                FIF.DELETE,
                self.tr("清除最近打开记录"),
                recent_menu,
                triggered=self.clear_recent_projects_clicked.emit,
            ))
        else:
            empty_action = Action(self.tr("暂无最近打开的文件"), recent_menu)
            empty_action.setEnabled(False)
            recent_menu.addAction(empty_action)

    def set_recent_projects(self, paths: list[str]) -> None:
        """更新最近项目列表，仅原地刷新最近项目子菜单。"""
        normalized = [str(path) for path in paths]
        if normalized == self._recent_project_paths:
            return
        self._recent_project_paths = normalized
        if hasattr(self, "_recent_menu"):
            self._rebuild_recent_menu()

    def changeEvent(self, event):
        """切语言时整条工具栏拆掉重建。"""
        if event.type() == QEvent.Type.LanguageChange:
            from strange_uta_game.frontend.localization import detach_layout_for_rebuild
            saved_offset = self.edit_offset.text() if hasattr(self, "edit_offset") else ""
            detach_layout_for_rebuild(self)
            self._init_ui()
            if saved_offset and hasattr(self, "edit_offset"):
                self.edit_offset.setText(saved_offset)
        super().changeEvent(event)

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
