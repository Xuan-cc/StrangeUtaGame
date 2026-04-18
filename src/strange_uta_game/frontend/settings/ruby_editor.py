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
    QDialog,
    QCheckBox,
    QMessageBox,
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
from difflib import SequenceMatcher

from strange_uta_game.backend.domain import (
    Project,
    LyricLine,
    Ruby,
    TimeTag,
    CheckpointConfig,
)
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    CharType,
    get_char_type,
)


def _rebuild_timetags_and_checkpoints(
    line: LyricLine, old_chars: List[str], new_chars: List[str]
) -> None:
    """文本变更后重建 timetag 和 checkpoint 索引。

    使用 SequenceMatcher 计算旧字符列表到新字符列表的映射，
    将原有 timetag 的 char_idx 平移到新位置。新插入的字符无 timetag，
    删除的字符的 timetag 被丢弃。Checkpoint 也做相应重建。
    """
    if old_chars == new_chars:
        return

    # 构建 old_idx → new_idx 映射
    sm = SequenceMatcher(None, old_chars, new_chars)
    old_to_new: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                old_to_new[i1 + k] = j1 + k
        # 'replace' / 'delete' / 'insert' 不映射（被删除/替换的字符无法保留 timetag）

    # 重建 timetags：仅保留有映射的，更新 char_idx
    new_timetags: List[TimeTag] = []
    for tag in line.timetags:
        new_idx = old_to_new.get(tag.char_idx)
        if new_idx is not None:
            new_timetags.append(
                TimeTag(
                    timestamp_ms=tag.timestamp_ms,
                    singer_id=tag.singer_id,
                    char_idx=new_idx,
                    checkpoint_idx=tag.checkpoint_idx,
                    tag_type=tag.tag_type,
                )
            )
    line.timetags = sorted(new_timetags, key=lambda t: t.timestamp_ms)

    # 重建 checkpoints：保留映射到的旧配置，新字符用默认值
    old_cp_map: dict[int, CheckpointConfig] = {
        cp.char_idx: cp for cp in line.checkpoints
    }
    new_checkpoints: List[CheckpointConfig] = []
    for j in range(len(new_chars)):
        # 查找是否有旧字符映射到这个新位置
        mapped_old_idx = None
        for old_idx, new_mapped in old_to_new.items():
            if new_mapped == j:
                mapped_old_idx = old_idx
                break

        if mapped_old_idx is not None and mapped_old_idx in old_cp_map:
            old_cp = old_cp_map[mapped_old_idx]
            new_checkpoints.append(
                CheckpointConfig(
                    char_idx=j,
                    check_count=old_cp.check_count,
                    is_line_end=(j == len(new_chars) - 1),
                    is_rest=old_cp.is_rest,
                    linked_to_next=old_cp.linked_to_next,
                    singer_id=old_cp.singer_id,
                )
            )
        else:
            # 新字符：默认 check_count=1，末尾字符 check_count=2
            is_last = j == len(new_chars) - 1
            new_checkpoints.append(
                CheckpointConfig(
                    char_idx=j,
                    check_count=2 if is_last else 1,
                    is_line_end=is_last,
                )
            )
    line.checkpoints = new_checkpoints


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


class DeleteRubyByTypeDialog(QDialog):
    """按字符类型选择要删除注音的对话框。"""

    _TYPE_LABELS = [
        (CharType.HIRAGANA, "ひらがな（平假名）"),
        (CharType.KATAKANA, "カタカナ（片假名）"),
        (CharType.KANJI, "漢字（汉字）"),
        (CharType.ALPHABET, "アルファベット（英文字母）"),
        (CharType.NUMBER, "数字"),
        (CharType.SYMBOL, "記号（符号）"),
        (CharType.SPACE, "空格"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("按类型删除注音")
        self.resize(320, 300)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        lbl = QLabel("选择要删除注音的字符类型：")
        lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl)

        self._checkboxes: list[tuple[CharType, QCheckBox]] = []
        for char_type, label in self._TYPE_LABELS:
            cb = QCheckBox(label, self)
            if char_type in (CharType.HIRAGANA, CharType.KATAKANA):
                cb.setChecked(True)
            layout.addWidget(cb)
            self._checkboxes.append((char_type, cb))

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_ok = PrimaryPushButton("删除选中类型", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def selected_types(self) -> list[CharType]:
        """返回用户选中的字符类型列表。"""
        return [ct for ct, cb in self._checkboxes if cb.isChecked()]


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
            "支持增删行（换行/排版），编辑后点击「应用修改」保存"
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

        self.btn_delete_by_type = PushButton("按类型删除注音", self)
        self.btn_delete_by_type.setIcon(FIF.DELETE)
        self.btn_delete_by_type.clicked.connect(self._on_delete_rubies_by_type)
        self.btn_delete_by_type.setEnabled(False)
        batch_layout.addWidget(self.btn_delete_by_type)

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
            self.btn_delete_by_type,
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

    def _on_delete_rubies_by_type(self):
        """打开对话框，按字符类型删除注音。"""
        if not self._project:
            return

        dlg = DeleteRubyByTypeDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_types()
        if not selected:
            return

        removed = 0
        for line in self._project.lines:
            to_remove = []
            for ruby in line.rubies:
                chars_in_range = line.chars[
                    ruby.start_idx : min(ruby.end_idx, len(line.chars))
                ]
                if chars_in_range and all(
                    get_char_type(c) in selected for c in chars_in_range
                ):
                    to_remove.append(ruby)

            for ruby in to_remove:
                line.rubies.remove(ruby)
                removed += 1

        self._refresh_display()
        if hasattr(self, "_store"):
            self._store.notify("rubies")

        InfoBar.success(
            title="删除完成",
            content=f"已删除 {removed} 个注音（类型: {', '.join(label for ct, label in DeleteRubyByTypeDialog._TYPE_LABELS if ct in selected)}）",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
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
        """将文本编辑器内容应用回项目（支持增删行，保留打轴数据）。

        使用行级 SequenceMatcher 将旧行映射到新行，
        匹配到的旧行保留 timetag/checkpoint 并做字符级 diff，
        新插入行使用默认设置，删除行被丢弃。
        """
        if not self._project:
            return

        text = self.text_edit.toPlainText()
        new_line_strs = text.split("\n")

        # 解析每行的带注音文本
        parsed_new: List[Tuple[str, List[str], List[Ruby]]] = []
        parse_errors = []
        for i, ls in enumerate(new_line_strs):
            try:
                raw_text, raw_chars, rubies = _parse_annotated_line(ls)
                if not raw_text:
                    raw_text = " "
                    raw_chars = [" "]
                    rubies = []
                parsed_new.append((raw_text, raw_chars, rubies))
            except Exception as e:
                parse_errors.append(f"第 {i + 1} 行: {e}")
                parsed_new.append((" ", [" "], []))

        if parse_errors:
            InfoBar.warning(
                title="部分行解析失败",
                content="\n".join(parse_errors[:3]),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

        old_lines = list(self._project.lines)
        old_texts = [line.text for line in old_lines]
        new_texts = [p[0] for p in parsed_new]

        # 行级 diff：将旧行映射到新行
        line_sm = SequenceMatcher(None, old_texts, new_texts)

        default_singer = old_lines[0].singer_id if old_lines else "default"
        result_lines: List[LyricLine] = [None] * len(parsed_new)  # type: ignore

        for tag, i1, i2, j1, j2 in line_sm.get_opcodes():
            if tag == "equal":
                # 旧行 i1..i2 完全匹配新行 j1..j2
                for k in range(i2 - i1):
                    old_line = old_lines[i1 + k]
                    raw_text, raw_chars, rubies = parsed_new[j1 + k]
                    old_chars = list(old_line.chars)
                    old_line.text = raw_text
                    old_line.chars = raw_chars
                    old_line.rubies.clear()
                    for r in rubies:
                        old_line.add_ruby(r)
                    _rebuild_timetags_and_checkpoints(old_line, old_chars, raw_chars)
                    result_lines[j1 + k] = old_line

            elif tag == "replace":
                # 尝试 1:1 映射 — 如果旧段和新段长度相同，逐行做字符级 diff
                old_count = i2 - i1
                new_count = j2 - j1
                matched = min(old_count, new_count)
                for k in range(matched):
                    old_line = old_lines[i1 + k]
                    raw_text, raw_chars, rubies = parsed_new[j1 + k]
                    old_chars = list(old_line.chars)
                    old_line.text = raw_text
                    old_line.chars = raw_chars
                    old_line.rubies.clear()
                    for r in rubies:
                        old_line.add_ruby(r)
                    _rebuild_timetags_and_checkpoints(old_line, old_chars, raw_chars)
                    result_lines[j1 + k] = old_line
                # 多出的新行 → 创建
                for k in range(matched, new_count):
                    raw_text, raw_chars, rubies = parsed_new[j1 + k]
                    nl = LyricLine(
                        singer_id=default_singer,
                        text=raw_text,
                        chars=raw_chars,
                    )
                    for r in rubies:
                        nl.add_ruby(r)
                    result_lines[j1 + k] = nl
                # 多出的旧行 → 丢弃（tag == replace 中 old_count > new_count 部分）

            elif tag == "insert":
                # 新插入行
                for k in range(j2 - j1):
                    raw_text, raw_chars, rubies = parsed_new[j1 + k]
                    nl = LyricLine(
                        singer_id=default_singer,
                        text=raw_text,
                        chars=raw_chars,
                    )
                    for r in rubies:
                        nl.add_ruby(r)
                    result_lines[j1 + k] = nl

            # tag == "delete": 旧行被删除，不出现在 result_lines 中

        self._project.lines = result_lines

        if hasattr(self, "_store"):
            self._store.notify("lyrics")
            self._store.notify("rubies")
        self._update_stats()

        InfoBar.success(
            title="应用成功",
            content=f"已更新 {len(result_lines)} 行",
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
