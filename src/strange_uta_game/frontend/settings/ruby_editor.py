"""注音编辑界面。

全文本视图编辑歌词注音（ルビ），支持批量操作。
格式: 漢字{かんじ} — 花括号内为注音。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
)

from typing import Optional, List, Tuple

from strange_uta_game.backend.domain import Project, LyricLine, Ruby
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    get_char_type,
    CharType,
)


def _is_kanji_char(char: str) -> bool:
    """判断是否为汉字"""
    if len(char) != 1:
        return False
    code = ord(char)
    return (
        (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or (0xF900 <= code <= 0xFAFF)
    )


def _is_kana_char(char: str) -> bool:
    """判断是否为假名（平假名/片假名）"""
    if len(char) != 1:
        return False
    code = ord(char)
    return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)


def _is_symbol_char(char: str) -> bool:
    """判断是否为符号/标点"""
    if len(char) != 1:
        return False
    try:
        ct = get_char_type(char)
        return ct in (CharType.SYMBOL, CharType.SPACE, CharType.OTHER)
    except ValueError:
        return False


def _parse_annotated_line(
    line_text: str,
) -> Tuple[str, List[str], List[Ruby]]:
    """解析带注音标注的文本行。

    格式: 漢字{かんじ} — 花括号标注前面连续汉字块的读音。

    Returns:
        (原文, 字符列表, 注音列表)
    """
    raw_chars: List[str] = []
    rubies: List[Ruby] = []
    i = 0
    n = len(line_text)

    while i < n:
        if line_text[i] == "{":
            close = line_text.find("}", i)
            if close == -1:
                # 无配对右括号，当普通字符处理
                raw_chars.append(line_text[i])
                i += 1
                continue

            ruby_text = line_text[i + 1 : close]

            if ruby_text and raw_chars:
                # 向前查找连续汉字块
                end_idx = len(raw_chars)
                start_idx = end_idx
                while start_idx > 0 and _is_kanji_char(raw_chars[start_idx - 1]):
                    start_idx -= 1

                # 若前面没有汉字，退一格（允许给任意字符标注）
                if start_idx == end_idx and end_idx > 0:
                    start_idx = end_idx - 1

                if start_idx < end_idx:
                    # 检查是否与已有注音重叠
                    overlap = False
                    for existing in rubies:
                        if (
                            start_idx < existing.end_idx
                            and end_idx > existing.start_idx
                        ):
                            overlap = True
                            break

                    if not overlap:
                        rubies.append(
                            Ruby(
                                text=ruby_text,
                                start_idx=start_idx,
                                end_idx=end_idx,
                            )
                        )

            i = close + 1
        else:
            raw_chars.append(line_text[i])
            i += 1

    raw_text = "".join(raw_chars)
    return raw_text, raw_chars, rubies


class RubyInterface(QWidget):
    """注音编辑界面

    全文本视图 + 批量操作。
    """

    rubies_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None

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
        desc = QLabel(
            "全文本编辑：汉字注音用 {假名} 标注，如 赤{あか}い花{はな}\n"
            "编辑后点击「应用修改」保存，或点击「更新节奏点」根据注音重算节奏点数量"
        )
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        layout.addSpacing(5)

        # 批量操作按钮
        batch_layout = QHBoxLayout()

        self.btn_auto_all = PushButton("自动分析全部注音", self)
        self.btn_auto_all.setIcon(FIF.SYNC)
        self.btn_auto_all.clicked.connect(self._on_auto_analyze_all)
        self.btn_auto_all.setEnabled(False)
        batch_layout.addWidget(self.btn_auto_all)

        self.btn_delete_kana = PushButton("删除假名注音", self)
        self.btn_delete_kana.setIcon(FIF.DELETE)
        self.btn_delete_kana.clicked.connect(self._on_delete_kana_rubies)
        self.btn_delete_kana.setEnabled(False)
        batch_layout.addWidget(self.btn_delete_kana)

        self.btn_delete_symbol = PushButton("删除符号注音", self)
        self.btn_delete_symbol.setIcon(FIF.DELETE)
        self.btn_delete_symbol.clicked.connect(self._on_delete_symbol_rubies)
        self.btn_delete_symbol.setEnabled(False)
        batch_layout.addWidget(self.btn_delete_symbol)

        self.btn_update_cp = PushButton("更新节奏点", self)
        self.btn_update_cp.setIcon(FIF.UPDATE)
        self.btn_update_cp.clicked.connect(self._on_update_checkpoints)
        self.btn_update_cp.setEnabled(False)
        batch_layout.addWidget(self.btn_update_cp)

        batch_layout.addStretch()

        layout.addLayout(batch_layout)

        # 全文本编辑器
        self.text_edit = QPlainTextEdit()
        self.text_edit.setFont(QFont("Microsoft YaHei", 12))
        self.text_edit.setPlaceholderText(
            "加载项目后，歌词将以注音标注格式显示在此处...\n"
            "示例: 赤{あか}い花{はな}が咲{さ}いた"
        )
        self.text_edit.setMinimumHeight(300)
        layout.addWidget(self.text_edit, stretch=1)

        # 应用/还原
        action_layout = QHBoxLayout()

        self.btn_apply = PrimaryPushButton("应用修改", self)
        self.btn_apply.setIcon(FIF.ACCEPT)
        self.btn_apply.clicked.connect(self._on_apply_changes)
        self.btn_apply.setEnabled(False)
        action_layout.addWidget(self.btn_apply)

        self.btn_revert = PushButton("还原", self)
        self.btn_revert.setIcon(FIF.CANCEL)
        self.btn_revert.clicked.connect(self._on_revert)
        self.btn_revert.setEnabled(False)
        action_layout.addWidget(self.btn_revert)

        action_layout.addStretch()

        self.lbl_stats = QLabel("共 0 行，0 个注音")
        self.lbl_stats.setStyleSheet("color: gray;")
        action_layout.addWidget(self.lbl_stats)

        layout.addLayout(action_layout)

    # ==================== 公共接口 ====================

    def set_project(self, project: Project, line_idx: int = 0):
        """设置项目"""
        self._project = project
        self._refresh_display()

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self._project = self._store.project
            self._refresh_display()
        elif change_type in ("rubies", "lyrics"):
            self._refresh_display()

    # ==================== 内部方法 ====================

    def _refresh_display(self):
        """刷新全部显示"""
        has_project = self._project is not None and len(self._project.lines) > 0

        for btn in (
            self.btn_auto_all,
            self.btn_delete_kana,
            self.btn_delete_symbol,
            self.btn_update_cp,
            self.btn_apply,
            self.btn_revert,
        ):
            btn.setEnabled(has_project)

        if has_project:
            self.text_edit.setPlainText(self._lines_to_text())
            self._update_stats()
        else:
            self.text_edit.setPlainText("")
            self.lbl_stats.setText("共 0 行，0 个注音")

    def _lines_to_text(self) -> str:
        """将项目歌词转为带注音标注的文本。

        格式: 漢字{かんじ}
        """
        if not self._project:
            return ""

        result = []
        for line in self._project.lines:
            annotated = ""
            i = 0
            while i < len(line.chars):
                ruby = line.get_ruby_for_char(i)
                if ruby and ruby.start_idx == i:
                    target = "".join(line.chars[ruby.start_idx : ruby.end_idx])
                    annotated += f"{target}{{{ruby.text}}}"
                    i = ruby.end_idx
                else:
                    annotated += line.chars[i]
                    i += 1
            result.append(annotated)
        return "\n".join(result)

    def _update_stats(self):
        """更新统计标签"""
        if not self._project:
            self.lbl_stats.setText("共 0 行，0 个注音")
            return

        total = sum(len(line.rubies) for line in self._project.lines)
        self.lbl_stats.setText(f"共 {len(self._project.lines)} 行，{total} 个注音")

    def _create_auto_check_service(self):
        """创建带设置的自动检查服务"""
        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        app_settings = AppSettings()
        all_settings = app_settings.get_all()
        auto_check_flags = all_settings.get("auto_check", {})
        user_dict = all_settings.get("ruby_dictionary", {}).get("entries", [])
        return AutoCheckService(
            auto_check_flags=auto_check_flags, user_dictionary=user_dict
        )

    # ==================== 批量操作 ====================

    def _on_auto_analyze_all(self):
        """自动分析全部注音（同时更新节奏点）"""
        if not self._project:
            return

        try:
            auto_check = self._create_auto_check_service()
            auto_check.apply_to_project(self._project)
            self._refresh_display()
            if hasattr(self, "_store"):
                self._store.notify("rubies")

            InfoBar.success(
                title="分析完成",
                content=f"已为 {len(self._project.lines)} 行自动分析注音并更新节奏点",
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

    def _on_delete_kana_rubies(self):
        """删除所有假名上的注音（假名本身就是读音，不需要标注）"""
        if not self._project:
            return

        removed = 0
        for line in self._project.lines:
            to_remove = []
            for ruby in line.rubies:
                chars_in_range = line.chars[
                    ruby.start_idx : min(ruby.end_idx, len(line.chars))
                ]
                if chars_in_range and all(_is_kana_char(c) for c in chars_in_range):
                    to_remove.append(ruby)

            for ruby in to_remove:
                line.rubies.remove(ruby)
                removed += 1

        self._refresh_display()
        if hasattr(self, "_store"):
            self._store.notify("rubies")

        InfoBar.success(
            title="删除完成",
            content=f"已删除 {removed} 个假名注音",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_delete_symbol_rubies(self):
        """删除所有符号上的注音"""
        if not self._project:
            return

        removed = 0
        for line in self._project.lines:
            to_remove = []
            for ruby in line.rubies:
                chars_in_range = line.chars[
                    ruby.start_idx : min(ruby.end_idx, len(line.chars))
                ]
                if chars_in_range and all(_is_symbol_char(c) for c in chars_in_range):
                    to_remove.append(ruby)

            for ruby in to_remove:
                line.rubies.remove(ruby)
                removed += 1

        self._refresh_display()
        if hasattr(self, "_store"):
            self._store.notify("rubies")

        InfoBar.success(
            title="删除完成",
            content=f"已删除 {removed} 个符号注音",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_update_checkpoints(self):
        """根据当前注音更新节奏点（不重新分析注音）"""
        if not self._project:
            return

        try:
            auto_check = self._create_auto_check_service()
            auto_check.update_checkpoints_for_project(self._project)
            if hasattr(self, "_store"):
                self._store.notify("checkpoints")

            InfoBar.success(
                title="更新完成",
                content=f"已根据注音更新 {len(self._project.lines)} 行的节奏点",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )
        except Exception as e:
            InfoBar.warning(
                title="更新失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    # ==================== 应用/还原 ====================

    def _on_apply_changes(self):
        """将文本编辑器内容应用回项目"""
        if not self._project:
            return

        text = self.text_edit.toPlainText()
        lines = text.split("\n")

        # 过滤空行
        lines = [l for l in lines if l.strip()]

        if len(lines) != len(self._project.lines):
            InfoBar.warning(
                title="行数不匹配",
                content=(
                    f"编辑器中有 {len(lines)} 行，"
                    f"项目中有 {len(self._project.lines)} 行。\n"
                    "请勿增删行，仅修改注音内容。"
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return

        errors = []
        for i, (line_text, proj_line) in enumerate(zip(lines, self._project.lines)):
            try:
                raw_text, raw_chars, rubies = _parse_annotated_line(line_text)

                if not raw_text.strip():
                    continue

                # 更新行数据
                proj_line.text = raw_text
                proj_line.chars = raw_chars
                proj_line.rubies.clear()
                for ruby in rubies:
                    proj_line.add_ruby(ruby)

            except Exception as e:
                errors.append(f"第 {i + 1} 行: {e}")

        if errors:
            InfoBar.warning(
                title="部分行应用失败",
                content="\n".join(errors[:3]),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
        else:
            if hasattr(self, "_store"):
                self._store.notify("rubies")
            self._update_stats()

            InfoBar.success(
                title="应用成功",
                content=f"已更新 {len(lines)} 行的注音",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )

    def _on_revert(self):
        """还原编辑器内容为项目当前状态"""
        self._refresh_display()

        InfoBar.info(
            title="已还原",
            content="编辑器内容已还原为项目当前状态",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )
