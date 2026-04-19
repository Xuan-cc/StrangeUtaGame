"""导出界面。

提供多格式导出功能，支持 LRC/KRA/TXT/ASS/Nicokara。
Nicokara 格式支持演唱者过滤和演唱者标签插入。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QCheckBox,
    QGroupBox,
    QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    LineEdit,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    SimpleCardWidget,
    CheckBox,
)

from typing import Optional, Set, Dict, cast
from pathlib import Path

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.application.export_service import ExportService
from strange_uta_game.frontend.settings.settings_interface import (
    AppSettings,
    NicokaraTagsDialog,
)


class ExportInterface(QWidget):
    """导出界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._export_service = ExportService()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # 标题
        title = QLabel("导出")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("将项目导出为多种歌词格式")
        desc.setStyleSheet("font-size: 13px; color: gray;")
        layout.addWidget(desc)

        # 格式选择
        content = QHBoxLayout()
        content.setSpacing(20)

        # 左侧：格式列表
        left_card = SimpleCardWidget()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(20, 20, 20, 20)
        left_layout.setSpacing(10)

        left_label = QLabel("选择导出格式")
        left_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        left_layout.addWidget(left_label)

        self.format_list = QListWidget()
        self.format_list.setMinimumHeight(200)
        self._populate_formats()
        left_layout.addWidget(self.format_list)

        content.addWidget(left_card, 1)

        # 右侧：导出配置
        right_card = SimpleCardWidget()
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(15)

        right_label = QLabel("导出设置")
        right_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        right_layout.addWidget(right_label)

        # 输出路径
        path_label = QLabel("输出路径")
        path_label.setStyleSheet("font-size: 13px; color: #666;")
        right_layout.addWidget(path_label)

        path_row = QHBoxLayout()
        self.line_output = LineEdit()
        self.line_output.setPlaceholderText("选择导出目录...")
        self.line_output.setReadOnly(True)
        path_row.addWidget(self.line_output)

        btn_browse = PushButton("浏览...", self)
        btn_browse.setIcon(FIF.FOLDER)
        btn_browse.clicked.connect(self._on_browse)
        path_row.addWidget(btn_browse)
        right_layout.addLayout(path_row)

        # 文件名
        fname_label = QLabel("文件名（不含扩展名）")
        fname_label.setStyleSheet("font-size: 13px; color: #666;")
        right_layout.addWidget(fname_label)

        self.line_filename = LineEdit()
        self.line_filename.setPlaceholderText("untitled")
        right_layout.addWidget(self.line_filename)

        # Nicokara 标签设置按钮（仅 Nicokara 格式显示）
        self.btn_tags = PushButton("Nicokara 标签设置...", self)
        self.btn_tags.setIcon(FIF.TAG)
        self.btn_tags.clicked.connect(self._on_nicokara_tags)
        self.btn_tags.hide()
        right_layout.addWidget(self.btn_tags)

        # 演唱者选择区域（仅 Nicokara 格式显示）
        self._singer_group = QGroupBox("演唱者过滤")
        singer_group_layout = QVBoxLayout(self._singer_group)
        singer_group_layout.setSpacing(6)

        singer_hint = QLabel("勾选要导出的演唱者（不勾选则导出全部）")
        singer_hint.setStyleSheet("font-size: 12px; color: #888;")
        singer_group_layout.addWidget(singer_hint)

        self._singer_checkboxes: list[CheckBox] = []
        self._singer_checkbox_widget = QWidget()
        self._singer_checkbox_container = QVBoxLayout(self._singer_checkbox_widget)
        self._singer_checkbox_container.setContentsMargins(0, 0, 0, 0)
        self._singer_checkbox_container.setSpacing(6)

        self._singer_scroll_area = QScrollArea()
        self._singer_scroll_area.setWidgetResizable(True)
        self._singer_scroll_area.setMaximumHeight(120)
        self._singer_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._singer_scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._singer_scroll_area.setWidget(self._singer_checkbox_widget)
        singer_group_layout.addWidget(self._singer_scroll_area)

        self._chk_insert_singer_tags = CheckBox("在演唱者切换处插入【演唱者名】标签")
        self._chk_insert_singer_tags.setToolTip(
            "导出时，当演唱者发生变化，在字符前自动插入演唱者名称标签"
        )
        self._chk_insert_singer_tags.hide()

        self._singer_group.hide()
        right_layout.addWidget(self._singer_group)
        right_layout.addWidget(self._chk_insert_singer_tags)

        right_layout.addStretch()

        # 导出按钮
        self.btn_export = PrimaryPushButton("导出", self)
        self.btn_export.setIcon(FIF.SHARE)
        self.btn_export.setMinimumHeight(45)
        self.btn_export.clicked.connect(self._on_export)
        right_layout.addWidget(self.btn_export)

        # 批量导出
        self.btn_batch = PushButton("批量导出（全部格式）", self)
        self.btn_batch.setIcon(FIF.FOLDER)
        self.btn_batch.clicked.connect(self._on_batch_export)
        right_layout.addWidget(self.btn_batch)

        content.addWidget(right_card, 1)

        layout.addLayout(content, 1)

    def _populate_formats(self):
        """填充格式列表"""
        formats = self._export_service.get_available_formats()
        for fmt in formats:
            item = QListWidgetItem(f"{fmt['name']} ({fmt['extension']})")
            item.setData(Qt.ItemDataRole.UserRole, fmt["name"])
            self.format_list.addItem(item)
        if self.format_list.count() > 0:
            self.format_list.setCurrentRow(0)
        self.format_list.currentItemChanged.connect(self._on_format_selected)

    def _on_format_selected(self, current, _previous):
        """根据所选格式显示/隐藏 Nicokara 专用控件"""
        if current:
            name = current.data(Qt.ItemDataRole.UserRole)
            is_nicokara = "nicokara" in name.lower()
            self.btn_tags.setVisible(is_nicokara)
            self._singer_group.setVisible(is_nicokara)
            self._chk_insert_singer_tags.setVisible(is_nicokara)
            if is_nicokara:
                self._refresh_singer_checkboxes()

    def set_project(self, project: Project):
        self._project = project

    def _get_export_offset(self) -> int:
        """从设置中获取导出时间偏移（毫秒）。"""
        settings = AppSettings()
        return settings.get("export.offset_ms", 0)

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self._project = self._store.project
            # 用音频文件名（无扩展名）作为默认导出文件名
            audio_path = getattr(self._store, "audio_path", None)
            if audio_path:
                default_name = Path(audio_path).stem
            elif self._project and self._project.metadata.title:
                default_name = self._project.metadata.title
            else:
                default_name = ""
            self.line_filename.setText(default_name)
            self._refresh_singer_checkboxes()
        elif change_type == "singers":
            self._refresh_singer_checkboxes()

    def _refresh_singer_checkboxes(self):
        """刷新演唱者 checkbox 列表"""
        # 清除现有 checkbox
        while self._singer_checkbox_container.count():
            item = self._singer_checkbox_container.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    cast(QWidget, widget).deleteLater()
        self._singer_checkboxes.clear()

        if not self._project:
            return

        used_singer_ids = set()
        for sentence in getattr(self._project, "sentences", []) or []:
            # 行级别演唱者
            sentence_singer = getattr(sentence, "singer_id", None)
            if sentence_singer:
                used_singer_ids.add(sentence_singer)
            # per-char 级别演唱者
            for character in getattr(sentence, "characters", []) or []:
                singer_id = getattr(character, "singer_id", None)
                if singer_id:
                    used_singer_ids.add(singer_id)

        for singer in self._project.singers:
            if singer.id not in used_singer_ids:
                continue
            chk = CheckBox(f"{singer.name}")
            chk.setProperty("singer_id", singer.id)
            chk.setStyleSheet(
                f"QCheckBox {{ color: {singer.color}; font-weight: bold; }}"
            )
            self._singer_checkbox_container.addWidget(chk)
            self._singer_checkboxes.append(chk)

        self._singer_checkbox_container.addStretch(1)

    def _get_selected_singer_ids(self) -> Optional[Set[str]]:
        """获取勾选的演唱者 ID 集合，如果没有勾选任何则返回 None（表示全部）"""
        selected = set()
        for chk in self._singer_checkboxes:
            if chk.isChecked():
                selected.add(chk.property("singer_id"))
        return selected if selected else None

    def _get_singer_map(self) -> Dict[str, str]:
        """获取 singer_id → 显示名 的映射"""
        if not self._project:
            return {}
        return {s.id: s.name for s in self._project.singers}

    def _on_browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择导出目录", "")
        if path:
            self.line_output.setText(path)

    def _on_nicokara_tags(self):
        """打开 Nicokara 标签设置对话框"""
        settings = AppSettings()
        tag_data = settings.get("nicokara_tags") or {}
        dialog = NicokaraTagsDialog(tag_data, self)
        if dialog.exec() == NicokaraTagsDialog.DialogCode.Accepted:
            new_tags = dialog.get_tag_data()
            settings.set("nicokara_tags", new_tags)
            settings.save()

    def _on_export(self):
        if not self._project:
            InfoBar.warning(
                title="无项目",
                content="请先创建或打开项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        selected = self.format_list.currentItem()
        if not selected:
            InfoBar.warning(
                title="未选择格式",
                content="请选择导出格式",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        output_dir = self.line_output.text()
        if not output_dir:
            # 弹出文件选择
            output_dir = QFileDialog.getExistingDirectory(self, "选择导出目录", "")
            if not output_dir:
                return
            self.line_output.setText(output_dir)

        # 导出前验证
        warnings = self._export_service.validate_before_export(self._project)
        if warnings:
            InfoBar.warning(
                title="导出提醒",
                content="\n".join(warnings[:3]),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

        name = selected.data(Qt.ItemDataRole.UserRole)
        # 获取扩展名
        formats = self._export_service.get_available_formats()
        ext = ""
        for fmt in formats:
            if fmt["name"] == name:
                ext = fmt["extension"]
                break

        base_name = (
            self.line_filename.text().strip()
            or self._project.metadata.title
            or "untitled"
        )
        filename = base_name + ext
        filepath = str(Path(output_dir) / filename)

        result = self._export_service.export(
            self._project,
            name,
            filepath,
            offset_ms=self._get_export_offset(),
            singer_ids=self._get_selected_singer_ids(),
            insert_singer_tags=self._chk_insert_singer_tags.isChecked(),
            singer_map=self._get_singer_map(),
        )
        if result.success:
            InfoBar.success(
                title="导出成功",
                content=result.file_path,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
        else:
            InfoBar.error(
                title="导出失败",
                content=result.error_message or "未知错误",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    def _on_batch_export(self):
        if not self._project:
            InfoBar.warning(
                title="无项目",
                content="请先创建或打开项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        output_dir = self.line_output.text()
        if not output_dir:
            output_dir = QFileDialog.getExistingDirectory(self, "选择导出目录", "")
            if not output_dir:
                return
            self.line_output.setText(output_dir)

        formats = self._export_service.get_available_formats()
        format_names = [fmt["name"] for fmt in formats]
        base_name = (
            self.line_filename.text().strip()
            or self._project.metadata.title
            or "untitled"
        )

        results = self._export_service.batch_export(
            self._project,
            format_names,
            output_dir,
            base_name,
            offset_ms=self._get_export_offset(),
        )
        success_count = sum(1 for r in results if r.success)

        InfoBar.success(
            title="批量导出完成",
            content=f"成功导出 {success_count}/{len(format_names)} 种格式",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self,
        )
