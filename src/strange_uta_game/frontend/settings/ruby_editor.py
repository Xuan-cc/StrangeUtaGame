"""注音编辑界面。

手动编辑歌词注音（ルビ）。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QSpinBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    LineEdit,
    SpinBox,
    TableWidget,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    ComboBox,
)

from typing import Optional, List

from strange_uta_game.backend.domain import Project, LyricLine, Ruby
from strange_uta_game.backend.application import AutoCheckService


class RubyEditDialog(QDialog):
    """注音编辑对话框"""

    def __init__(
        self,
        text: str = "",
        ruby_text: str = "",
        start_idx: int = 0,
        end_idx: int = 0,
        parent=None,
    ):
        super().__init__(parent)

        self.setWindowTitle("编辑注音")
        self.resize(400, 250)

        self._init_ui(text, ruby_text, start_idx, end_idx)

    def _init_ui(self, text: str, ruby_text: str, start_idx: int, end_idx: int):
        """初始化界面"""
        layout = QFormLayout(self)

        # 原文显示
        self.lbl_original = QLabel(text)
        self.lbl_original.setStyleSheet(
            "font-size: 16px; padding: 5px; background: #f0f0f0;"
        )
        layout.addRow("原文:", self.lbl_original)

        # 注音输入
        self.line_ruby = LineEdit()
        self.line_ruby.setText(ruby_text)
        self.line_ruby.setPlaceholderText("输入注音（如：ふりがな）...")
        layout.addRow("注音:", self.line_ruby)

        # 位置范围
        pos_layout = QHBoxLayout()

        self.spin_start = SpinBox()
        self.spin_start.setRange(0, 999)
        self.spin_start.setValue(start_idx)
        self.spin_start.valueChanged.connect(self._on_start_changed)
        pos_layout.addWidget(QLabel("起始:"))
        pos_layout.addWidget(self.spin_start)

        self.spin_end = SpinBox()
        self.spin_end.setRange(0, 999)
        self.spin_end.setValue(end_idx)
        pos_layout.addWidget(QLabel("结束:"))
        pos_layout.addWidget(self.spin_end)

        pos_layout.addStretch()

        layout.addRow("字符范围:", pos_layout)

        # 预览
        self.lbl_preview = QLabel("")
        self.lbl_preview.setStyleSheet(
            "font-size: 14px; padding: 5px; background: #f9f9f9; color: #666;"
        )
        layout.addRow("预览:", self.lbl_preview)

        self._update_preview()

        # 按钮
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addRow("", button_box)

    def _on_start_changed(self):
        """起始位置改变时，确保结束位置不小于起始位置"""
        if self.spin_end.value() < self.spin_start.value():
            self.spin_end.setValue(self.spin_start.value() + 1)
        self._update_preview()

    def _update_preview(self):
        """更新预览"""
        ruby = self.line_ruby.text().strip()
        if ruby:
            original = self.lbl_original.text()
            start = self.spin_start.value()
            end = min(self.spin_end.value(), len(original))

            if start < len(original):
                target_text = original[start:end]
                self.lbl_preview.setText(f"{target_text} → {ruby}")
            else:
                self.lbl_preview.setText("位置超出范围")
        else:
            self.lbl_preview.setText("请输入注音")

    def _on_accept(self):
        """确认"""
        ruby = self.line_ruby.text().strip()
        if not ruby:
            QMessageBox.warning(self, "警告", "请输入注音")
            return

        if self.spin_start.value() >= self.spin_end.value():
            QMessageBox.warning(self, "警告", "结束位置必须大于起始位置")
            return

        self.accept()

    def get_data(self) -> dict:
        """获取编辑后的数据"""
        return {
            "ruby_text": self.line_ruby.text().strip(),
            "start_idx": self.spin_start.value(),
            "end_idx": self.spin_end.value(),
        }


class RubyInterface(QWidget):
    """注音编辑界面"""

    rubies_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None
        self._current_line_idx: int = 0
        self._auto_check = AutoCheckService()

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 标题
        title = QLabel("注音编辑")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # 说明
        desc = QLabel("为歌词中的汉字添加假名注音（ルビ）")
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        layout.addSpacing(10)

        # 行选择
        line_layout = QHBoxLayout()
        line_layout.addWidget(QLabel("当前行:"))

        self.lbl_line_text = QLabel("未选择")
        self.lbl_line_text.setStyleSheet(
            "font-size: 16px; padding: 5px; background: #f0f0f0;"
        )
        self.lbl_line_text.setWordWrap(True)
        line_layout.addWidget(self.lbl_line_text, stretch=1)

        self.btn_prev = PushButton("← 上一行")
        self.btn_prev.clicked.connect(self._on_prev_line)
        self.btn_prev.setEnabled(False)
        line_layout.addWidget(self.btn_prev)

        self.btn_next = PushButton("下一行 →")
        self.btn_next.clicked.connect(self._on_next_line)
        self.btn_next.setEnabled(False)
        line_layout.addWidget(self.btn_next)

        layout.addLayout(line_layout)

        # 注音表格
        self.table = TableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["原文", "注音", "范围", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(2, 80)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(3, 100)
        self.table.setMinimumHeight(200)
        layout.addWidget(self.table)

        # 按钮区域
        button_layout = QHBoxLayout()

        self.btn_auto = PushButton("自动分析注音", self)
        self.btn_auto.setIcon(FIF.SYNC)
        self.btn_auto.clicked.connect(self._on_auto_analyze)
        self.btn_auto.setEnabled(False)
        button_layout.addWidget(self.btn_auto)

        self.btn_add = PrimaryPushButton("添加注音", self)
        self.btn_add.setIcon(FIF.ADD)
        self.btn_add.clicked.connect(self._on_add_ruby)
        self.btn_add.setEnabled(False)
        button_layout.addWidget(self.btn_add)

        button_layout.addStretch()

        layout.addLayout(button_layout)

        # 统计信息
        self.lbl_stats = QLabel("共 0 个注音")
        self.lbl_stats.setStyleSheet("color: gray;")
        layout.addWidget(self.lbl_stats)

        layout.addStretch()

    def set_project(self, project: Project, line_idx: int = 0):
        """设置项目和当前行"""
        self._project = project
        self._current_line_idx = line_idx
        self._refresh_display()

    def _refresh_display(self):
        """刷新显示"""
        if not self._project or not self._project.lines:
            self.lbl_line_text.setText("未加载项目或没有歌词")
            self.table.setRowCount(0)
            self.lbl_stats.setText("共 0 个注音")
            self.btn_auto.setEnabled(False)
            self.btn_add.setEnabled(False)
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return

        # 更新行文本显示
        if 0 <= self._current_line_idx < len(self._project.lines):
            line = self._project.lines[self._current_line_idx]
            self.lbl_line_text.setText(f"[{self._current_line_idx + 1}] {line.text}")

            # 刷新表格
            self._refresh_table(line)

            # 更新按钮状态
            self.btn_auto.setEnabled(True)
            self.btn_add.setEnabled(True)
            self.btn_prev.setEnabled(self._current_line_idx > 0)
            self.btn_next.setEnabled(
                self._current_line_idx < len(self._project.lines) - 1
            )
        else:
            self.lbl_line_text.setText("无效的行索引")
            self.table.setRowCount(0)

    def _refresh_table(self, line: LyricLine):
        """刷新注音表格"""
        self.table.setRowCount(len(line.rubies))

        for i, ruby in enumerate(line.rubies):
            # 原文
            original_text = ""
            if line.chars and ruby.start_idx < len(line.chars):
                end = min(ruby.end_idx, len(line.chars))
                original_text = "".join(line.chars[ruby.start_idx : end])

            item_original = QTableWidgetItem(original_text)
            self.table.setItem(i, 0, item_original)

            # 注音
            item_ruby = QTableWidgetItem(ruby.text)
            self.table.setItem(i, 1, item_ruby)

            # 范围
            item_range = QTableWidgetItem(f"{ruby.start_idx}-{ruby.end_idx}")
            item_range.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 2, item_range)

            # 操作按钮容器
            btn_widget = QWidget()
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(5, 0, 5, 0)
            btn_layout.setSpacing(5)

            btn_edit = PushButton("编辑")
            btn_edit.clicked.connect(lambda checked, idx=i: self._on_edit_ruby(idx))
            btn_layout.addWidget(btn_edit)

            btn_delete = PushButton("删除")
            btn_delete.clicked.connect(lambda checked, idx=i: self._on_delete_ruby(idx))
            btn_layout.addWidget(btn_delete)

            self.table.setCellWidget(i, 3, btn_widget)

        # 更新统计
        self.lbl_stats.setText(f"共 {len(line.rubies)} 个注音")

    def _on_prev_line(self):
        """上一行"""
        if self._current_line_idx > 0:
            self._current_line_idx -= 1
            self._refresh_display()

    def _on_next_line(self):
        """下一行"""
        if self._project and self._current_line_idx < len(self._project.lines) - 1:
            self._current_line_idx += 1
            self._refresh_display()

    def _on_auto_analyze(self):
        """自动分析注音"""
        if not self._project or not (
            0 <= self._current_line_idx < len(self._project.lines)
        ):
            return

        line = self._project.lines[self._current_line_idx]

        try:
            # 应用自动分析
            self._auto_check.apply_to_line(line)

            self._refresh_table(line)
            self.rubies_changed.emit()

            InfoBar.success(
                title="分析完成",
                content=f"已为第 {self._current_line_idx + 1} 行自动分析注音",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )

        except Exception as e:
            InfoBar.warning(
                title="分析失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _on_add_ruby(self):
        """添加注音"""
        if not self._project or not (
            0 <= self._current_line_idx < len(self._project.lines)
        ):
            return

        line = self._project.lines[self._current_line_idx]

        dialog = RubyEditDialog(
            text=line.text,
            ruby_text="",
            start_idx=0,
            end_idx=min(1, len(line.chars)) if line.chars else 1,
            parent=self,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()

            try:
                ruby = Ruby(
                    text=data["ruby_text"],
                    start_idx=data["start_idx"],
                    end_idx=data["end_idx"],
                )
                line.add_ruby(ruby)

                self._refresh_table(line)
                self.rubies_changed.emit()

                InfoBar.success(
                    title="添加成功",
                    content=f"已添加注音: {data['ruby_text']}",
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

    def _on_edit_ruby(self, ruby_idx: int):
        """编辑注音"""
        if not self._project or not (
            0 <= self._current_line_idx < len(self._project.lines)
        ):
            return

        line = self._project.lines[self._current_line_idx]

        if not (0 <= ruby_idx < len(line.rubies)):
            return

        ruby = line.rubies[ruby_idx]

        dialog = RubyEditDialog(
            text=line.text,
            ruby_text=ruby.text,
            start_idx=ruby.start_idx,
            end_idx=ruby.end_idx,
            parent=self,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()

            try:
                # 更新注音
                ruby.text = data["ruby_text"]
                ruby.start_idx = data["start_idx"]
                ruby.end_idx = data["end_idx"]

                self._refresh_table(line)
                self.rubies_changed.emit()

                InfoBar.success(
                    title="修改成功",
                    content=f"已更新注音: {data['ruby_text']}",
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

    def _on_delete_ruby(self, ruby_idx: int):
        """删除注音"""
        if not self._project or not (
            0 <= self._current_line_idx < len(self._project.lines)
        ):
            return

        line = self._project.lines[self._current_line_idx]

        if not (0 <= ruby_idx < len(line.rubies)):
            return

        ruby = line.rubies[ruby_idx]

        reply = QMessageBox.question(
            self,
            "确认删除",
            f'确定要删除注音 "{ruby.text}" 吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                line.rubies.pop(ruby_idx)

                self._refresh_table(line)
                self.rubies_changed.emit()

                InfoBar.success(
                    title="删除成功",
                    content=f"已删除注音: {ruby.text}",
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
