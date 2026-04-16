"""演唱者管理界面。

管理演唱者的添加、删除、重命名、颜色设置等。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QColorDialog,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    LineEdit,
    ListWidget,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    RoundMenu,
    Action,
)

from typing import Optional, List

from strange_uta_game.backend.domain import Project, Singer
from strange_uta_game.backend.application import SingerService


class SingerEditDialog(QDialog):
    """演唱者编辑对话框"""

    def __init__(self, singer: Singer = None, parent=None):
        super().__init__(parent)

        self._singer = singer
        self._color = singer.color if singer else "#FF6B6B"

        if singer:
            self.setWindowTitle("编辑演唱者")
        else:
            self.setWindowTitle("添加演唱者")

        self.resize(300, 200)

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QFormLayout(self)

        # 名称输入
        self.line_name = LineEdit()
        if self._singer:
            self.line_name.setText(self._singer.name)
        else:
            self.line_name.setPlaceholderText("输入演唱者名称...")
        layout.addRow("名称:", self.line_name)

        # 颜色选择
        color_layout = QHBoxLayout()

        self.lbl_color_preview = QLabel("    ")
        self.lbl_color_preview.setStyleSheet(
            f"background-color: {self._color}; border: 1px solid gray;"
        )
        self.lbl_color_preview.setFixedSize(40, 30)
        color_layout.addWidget(self.lbl_color_preview)

        btn_color = PushButton("选择颜色...")
        btn_color.clicked.connect(self._on_select_color)
        color_layout.addWidget(btn_color)

        color_layout.addStretch()

        layout.addRow("颜色:", color_layout)

        # 默认演唱者
        self.chk_default = QPushButton("设为默认演唱者")
        self.chk_default.setCheckable(True)
        if self._singer and self._singer.is_default:
            self.chk_default.setChecked(True)
        layout.addRow("", self.chk_default)

        # 按钮
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addRow("", button_box)

    def _on_select_color(self):
        """选择颜色"""
        color = QColorDialog.getColor(QColor(self._color), self, "选择演唱者颜色")
        if color.isValid():
            self._color = color.name()
            self.lbl_color_preview.setStyleSheet(
                f"background-color: {self._color}; border: 1px solid gray;"
            )

    def _on_accept(self):
        """确认"""
        name = self.line_name.text().strip()
        if not name:
            QMessageBox.warning(self, "警告", "请输入演唱者名称")
            return

        self.accept()

    def get_data(self) -> dict:
        """获取编辑后的数据"""
        return {
            "name": self.line_name.text().strip(),
            "color": self._color,
            "is_default": self.chk_default.isChecked(),
        }


class SingerManagerInterface(QWidget):
    """演唱者管理界面"""

    singers_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None
        self._singer_service: Optional[SingerService] = None

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 标题
        title = QLabel("演唱者管理")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # 说明
        desc = QLabel("管理歌词中的演唱者，每个演唱者可以有自己的颜色标识")
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        layout.addSpacing(10)

        # 演唱者列表
        self.list_singers = ListWidget()
        self.list_singers.setMinimumHeight(200)
        self.list_singers.itemClicked.connect(self._on_singer_selected)
        self.list_singers.itemDoubleClicked.connect(self._on_edit_singer)
        layout.addWidget(self.list_singers)

        # 按钮区域
        button_layout = QHBoxLayout()

        self.btn_add = PrimaryPushButton("添加", self)
        self.btn_add.setIcon(FIF.ADD)
        self.btn_add.clicked.connect(self._on_add_singer)
        button_layout.addWidget(self.btn_add)

        self.btn_edit = PushButton("编辑", self)
        self.btn_edit.setIcon(FIF.EDIT)
        self.btn_edit.clicked.connect(self._on_edit_singer)
        self.btn_edit.setEnabled(False)
        button_layout.addWidget(self.btn_edit)

        self.btn_delete = PushButton("删除", self)
        self.btn_delete.setIcon(FIF.DELETE)
        self.btn_delete.clicked.connect(self._on_delete_singer)
        self.btn_delete.setEnabled(False)
        button_layout.addWidget(self.btn_delete)

        button_layout.addStretch()

        layout.addLayout(button_layout)

        # 统计信息
        self.lbl_stats = QLabel("共 0 位演唱者")
        self.lbl_stats.setStyleSheet("color: gray;")
        layout.addWidget(self.lbl_stats)

        layout.addStretch()

    def set_project(self, project: Project):
        """设置项目"""
        self._project = project
        self._singer_service = SingerService(project)
        self._refresh_list()

    def _refresh_list(self):
        """刷新演唱者列表"""
        self.list_singers.clear()

        if not self._project:
            self.lbl_stats.setText("未加载项目")
            return

        for singer in self._project.singers:
            item = QListWidgetItem()

            # 显示格式: 名称 [默认] - 颜色方块
            display_text = singer.name
            if singer.is_default:
                display_text += " [默认]"
            if not singer.enabled:
                display_text += " (已禁用)"

            item.setText(display_text)

            # 设置背景色为演唱者颜色
            color = QColor(singer.color)
            item.setBackground(color)

            # 根据背景色亮度设置文字颜色
            luminance = (
                0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
            ) / 255
            if luminance > 0.5:
                item.setForeground(QColor("black"))
            else:
                item.setForeground(QColor("white"))

            # 存储演唱者 ID
            item.setData(Qt.ItemDataRole.UserRole, singer.id)

            self.list_singers.addItem(item)

        # 更新统计
        total = len(self._project.singers)
        enabled = sum(1 for s in self._project.singers if s.enabled)
        self.lbl_stats.setText(f"共 {total} 位演唱者（{enabled} 位启用）")

        # 重置按钮状态
        self.btn_edit.setEnabled(False)
        self.btn_delete.setEnabled(False)

    def _on_singer_selected(self):
        """选择演唱者"""
        selected = self.list_singers.currentItem()
        if selected:
            self.btn_edit.setEnabled(True)
            self.btn_delete.setEnabled(True)

            # 不能删除最后一个演唱者
            singer_id = selected.data(Qt.ItemDataRole.UserRole)
            singer = self._project.get_singer(singer_id)
            if singer and len(self._project.singers) <= 1:
                self.btn_delete.setEnabled(False)

    def _on_add_singer(self):
        """添加演唱者"""
        if not self._project:
            InfoBar.warning(
                title="未加载项目",
                content="请先打开或创建一个项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        dialog = SingerEditDialog(parent=self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()

            try:
                singer = self._singer_service.add_singer(
                    name=data["name"],
                    color=data["color"],
                )

                # 如果设为默认
                if data["is_default"]:
                    # 设置为默认
                    for s in self._project.singers:
                        if s.id == singer.id:
                            s.is_default = True
                        else:
                            s.is_default = False

                self._refresh_list()
                self.singers_changed.emit()

                InfoBar.success(
                    title="添加成功",
                    content=f"已添加演唱者: {data['name']}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=2000,
                    parent=self,
                )

            except Exception as e:
                InfoBar.error(
                    title="添加失败",
                    content=str(e),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

    def _on_edit_singer(self):
        """编辑演唱者"""
        selected = self.list_singers.currentItem()
        if not selected:
            return

        singer_id = selected.data(Qt.ItemDataRole.UserRole)
        singer = self._project.get_singer(singer_id)

        if not singer:
            return

        dialog = SingerEditDialog(singer, self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()

            try:
                # 重命名
                if data["name"] != singer.name:
                    self._singer_service.rename_singer(singer.id, data["name"])

                # 改颜色
                if data["color"] != singer.color:
                    self._singer_service.change_singer_color(singer.id, data["color"])

                # 设为默认
                if data["is_default"] and not singer.is_default:
                    for s in self._project.singers:
                        if s.id == singer.id:
                            s.is_default = True
                        else:
                            s.is_default = False

                self._refresh_list()
                self.singers_changed.emit()

                InfoBar.success(
                    title="修改成功",
                    content=f"已更新演唱者: {data['name']}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=2000,
                    parent=self,
                )

            except Exception as e:
                InfoBar.error(
                    title="修改失败",
                    content=str(e),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

    def _on_delete_singer(self):
        """删除演唱者"""
        selected = self.list_singers.currentItem()
        if not selected:
            return

        singer_id = selected.data(Qt.ItemDataRole.UserRole)
        singer = self._project.get_singer(singer_id)

        if not singer:
            return

        # 检查是否是最后一个演唱者
        if len(self._project.singers) <= 1:
            InfoBar.warning(
                title="无法删除",
                content="必须至少保留一个演唱者",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        # 确认删除
        reply = QMessageBox.question(
            self,
            "确认删除",
            f'确定要删除演唱者 "{singer.name}" 吗？\n\n该演唱者的歌词将被转移给默认演唱者。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                # 获取默认演唱者
                default_singer = self._project.get_default_singer()
                transfer_to = (
                    default_singer.id if default_singer.id != singer_id else None
                )

                # 删除
                self._singer_service.remove_singer(singer_id, transfer_to)

                self._refresh_list()
                self.singers_changed.emit()

                InfoBar.success(
                    title="删除成功",
                    content=f"已删除演唱者: {singer.name}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=2000,
                    parent=self,
                )

            except Exception as e:
                InfoBar.error(
                    title="删除失败",
                    content=str(e),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )
