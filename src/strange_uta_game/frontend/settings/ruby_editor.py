"""全文本编辑界面。

全文本视图编辑歌词注音（ルビ），支持批量操作。
格式: {大冒険|だい,ぼう,けん} — 花括号内为原文|逗号分隔注音。
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

from typing import Optional, List, Tuple, Dict
from difflib import SequenceMatcher

from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Ruby,
)
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    CharType,
    get_char_type,
)
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    split_ruby_for_checkpoints,
)


def _rebuild_characters(
    old_sentence: Sentence,
    new_chars: List[str],
    ruby_map: Dict[int, str],
) -> List[Character]:
    """文本变更后重建 Character 列表，保留匹配字符的时间戳和配置。

    使用 SequenceMatcher 计算旧字符到新字符的映射，
    匹配到的旧字符保留 timestamps/check_count/linked_to_next/singer_id，
    新插入的字符使用默认设置。最后一个字符标记为句尾。
    """
    old_chars_str = [c.char for c in old_sentence.characters]

    if old_chars_str == new_chars:
        # 文本未变，仅更新 ruby
        if ruby_map:
            for i, ch in enumerate(old_sentence.characters):
                if i in ruby_map:
                    ch.set_ruby(Ruby(text=ruby_map[i]))
        # ruby_map 为空时保留现有 ruby 不变
        return old_sentence.characters

    # 构建 old_idx → new_idx 映射
    sm = SequenceMatcher(None, old_chars_str, new_chars)
    new_to_old: Dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                new_to_old[j1 + k] = i1 + k

    characters: List[Character] = []
    for j in range(len(new_chars)):
        is_last = j == len(new_chars) - 1
        old_idx = new_to_old.get(j)

        if old_idx is not None:
            old_ch = old_sentence.characters[old_idx]
            ch = Character(
                char=new_chars[j],
                check_count=old_ch.check_count,
                timestamps=list(old_ch.timestamps),
                sentence_end_ts=old_ch.sentence_end_ts,
                linked_to_next=old_ch.linked_to_next if not is_last else False,
                is_line_end=is_last,
                is_sentence_end=is_last or old_ch.is_sentence_end,
                is_rest=old_ch.is_rest,
                singer_id=old_ch.singer_id,
            )
            # 保留原字符的注音（后续 ruby_map 覆盖优先）
            if old_ch.ruby:
                ch.set_ruby(Ruby(text=old_ch.ruby.text))
        else:
            # 新字符：默认 check_count=1，末尾字符 check_count=2
            ch = Character(
                char=new_chars[j],
                check_count=1,
                is_line_end=is_last,
                is_sentence_end=is_last,
                singer_id=old_sentence.singer_id,
            )

        # 应用 ruby
        if j in ruby_map:
            ch.set_ruby(Ruby(text=ruby_map[j]))

        characters.append(ch)

    return characters


def _apply_ruby_map(sentence: Sentence, ruby_map: Dict[int, str]) -> None:
    """将 ruby_map 应用到句子的字符上。"""
    for ci, ruby_text in ruby_map.items():
        if 0 <= ci < len(sentence.characters):
            sentence.characters[ci].set_ruby(Ruby(text=ruby_text))


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
) -> Tuple[str, List[str], Dict[int, str]]:
    """解析带注音标注的文本行。

    新格式: {大冒険|だい,ぼう,けん} — 花括号内：原文|逗号分隔各字符注音。
    兼容旧格式: 漢字{かんじ} — 花括号标注前面连续汉字块的读音。

    Returns:
        (原文, 字符列表, ruby_map: char_idx → ruby_text)
    """
    raw_chars: List[str] = []
    ruby_map: Dict[int, str] = {}
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

            content = line_text[i + 1 : close]

            if "|" in content:
                # 新格式: {text|r1,r2,...}
                text_part, readings_part = content.split("|", 1)
                readings = readings_part.split(",")

                start_idx = len(raw_chars)
                for ch in text_part:
                    raw_chars.append(ch)

                for j, reading in enumerate(readings):
                    reading = reading.strip()
                    if reading and (start_idx + j) < len(raw_chars):
                        ruby_map[start_idx + j] = reading
            else:
                # 旧格式: 漢字{かんじ}
                ruby_text = content

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
                        overlap = any(
                            ci in ruby_map for ci in range(start_idx, end_idx)
                        )

                        if not overlap:
                            block_len = end_idx - start_idx
                            split_parts = split_ruby_for_checkpoints(
                                ruby_text, block_len
                            )
                            for ci in range(start_idx, end_idx):
                                part_idx = ci - start_idx
                                if (
                                    part_idx < len(split_parts)
                                    and split_parts[part_idx]
                                ):
                                    ruby_map[ci] = split_parts[part_idx]

            i = close + 1
        else:
            raw_chars.append(line_text[i])
            i += 1

    raw_text = "".join(raw_chars)
    return raw_text, raw_chars, ruby_map


class DeleteRubyByTypeDialog(QDialog):
    """按字符类型选择要删除注音的对话框。"""

    _TYPE_LABELS = [
        (CharType.HIRAGANA, "ひらがな（平假名）"),
        (CharType.KATAKANA, "カタカナ（片假名）"),
        (CharType.KANJI, "漢字（汉字）"),
        (CharType.ALPHABET, "アルファベット（英文字母）"),
        (CharType.NUMBER, "数字"),
        (CharType.SYMBOL, "記号（符号）"),
        (CharType.LONG_VOWEL, "長音符号（ー、～等）"),
        (CharType.OTHER, "その他（♪等特殊符号）"),
        (CharType.SPACE, "空格"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("按类型删除注音")
        self.resize(320, 370)
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
        title = QLabel("全文本编辑")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # 说明
        desc = QLabel(
            "全文本编辑：汉字注音用 {假名} 标注，如 赤{あか}い花{はな}\n"
            "支持增删行（换行/排版），切换标签页时自动保存修改"
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

        # 还原
        action_layout = QHBoxLayout()

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

    def is_dirty(self) -> bool:
        """检查文本编辑器内容是否与项目数据不同"""
        if not self._project or not self._project.sentences:
            return False
        return self.text_edit.toPlainText() != self._lines_to_text()

    # ==================== 内部方法 ====================

    def _refresh_display(self):
        """刷新全部显示"""
        has_project = self._project is not None and len(self._project.sentences) > 0

        for btn in (
            self.btn_auto_all,
            self.btn_delete_by_type,
            self.btn_update_cp,
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

        格式: {大冒険|だい,ぼう,けん} — 连续有注音的字符合并为一组，
        花括号内原文|逗号分隔各字符注音。无注音的字符照常输出。
        """
        if not self._project:
            return ""

        result = []
        for sentence in self._project.sentences:
            annotated = ""
            chars = sentence.characters
            i = 0
            while i < len(chars):
                if chars[i].ruby:
                    # 收集连续有 ruby 的字符为一组
                    group_start = i
                    while i < len(chars) and chars[i].ruby:
                        i += 1
                    text_part = "".join(ch.char for ch in chars[group_start:i])
                    readings = ",".join(
                        ch.ruby.text if ch.ruby else "" for ch in chars[group_start:i]
                    )
                    annotated += f"{{{text_part}|{readings}}}"
                else:
                    annotated += chars[i].char
                    i += 1
            result.append(annotated)
        return "\n".join(result)

    def _update_stats(self):
        """更新统计标签"""
        if not self._project:
            self.lbl_stats.setText("共 0 行，0 个注音")
            return

        total = sum(
            sum(1 for c in s.characters if c.ruby) for s in self._project.sentences
        )
        self.lbl_stats.setText(f"共 {len(self._project.sentences)} 行，{total} 个注音")

    def _create_auto_check_service(self):
        """创建带设置的自动检查服务"""
        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        app_settings = AppSettings()
        all_settings = app_settings.get_all()
        auto_check_flags = all_settings.get("auto_check", {})
        user_dict = app_settings.load_dictionary()
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
                content=f"已为 {len(self._project.sentences)} 行自动分析注音并更新节奏点",
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
        for sentence in self._project.sentences:
            for ch in sentence.characters:
                if ch.ruby and get_char_type(ch.char) in selected:
                    ch.set_ruby(None)
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
        """根据当前注音更新节奏点（不重新分析注音）

        先保存文本编辑框内的内容，然后再根据内容和设置更新所有节奏点。
        """
        if not self._project:
            return

        # 先将文本编辑器内容应用回项目数据
        if self.is_dirty():
            self._on_apply_changes()

        try:
            auto_check = self._create_auto_check_service()
            auto_check.update_checkpoints_for_project(self._project)
            if hasattr(self, "_store"):
                self._store.notify("checkpoints")

            InfoBar.success(
                title="更新完成",
                content=f"已根据注音更新 {len(self._project.sentences)} 行的节奏点",
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
        匹配到的旧行保留 timestamps/配置 并做字符级 diff，
        新插入行使用默认设置，删除行被丢弃。
        """
        if not self._project:
            return

        text = self.text_edit.toPlainText()
        new_line_strs = text.split("\n")

        # 解析每行的带注音文本
        parsed_new: List[Tuple[str, List[str], Dict[int, str]]] = []
        parse_errors = []
        for i, ls in enumerate(new_line_strs):
            try:
                raw_text, raw_chars, ruby_map = _parse_annotated_line(ls)
                if not raw_text:
                    raw_text = " "
                    raw_chars = [" "]
                    ruby_map = {}
                parsed_new.append((raw_text, raw_chars, ruby_map))
            except Exception as e:
                parse_errors.append(f"第 {i + 1} 行: {e}")
                parsed_new.append((" ", [" "], {}))

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

        old_sentences = list(self._project.sentences)
        old_texts = [s.text for s in old_sentences]
        new_texts = [p[0] for p in parsed_new]

        # 行级 diff：将旧行映射到新行
        line_sm = SequenceMatcher(None, old_texts, new_texts)

        default_singer = old_sentences[0].singer_id if old_sentences else "default"
        result_sentences: List[Optional[Sentence]] = [None] * len(parsed_new)

        for tag, i1, i2, j1, j2 in line_sm.get_opcodes():
            if tag == "equal":
                # 旧行 i1..i2 完全匹配新行 j1..j2
                for k in range(i2 - i1):
                    old_s = old_sentences[i1 + k]
                    raw_text, raw_chars, ruby_map = parsed_new[j1 + k]
                    old_s.characters = _rebuild_characters(old_s, raw_chars, ruby_map)
                    result_sentences[j1 + k] = old_s

            elif tag == "replace":
                # 尝试 1:1 映射
                old_count = i2 - i1
                new_count = j2 - j1
                matched = min(old_count, new_count)
                for k in range(matched):
                    old_s = old_sentences[i1 + k]
                    raw_text, raw_chars, ruby_map = parsed_new[j1 + k]
                    old_s.characters = _rebuild_characters(old_s, raw_chars, ruby_map)
                    result_sentences[j1 + k] = old_s
                # 多出的新行 → 创建
                for k in range(matched, new_count):
                    raw_text, raw_chars, ruby_map = parsed_new[j1 + k]
                    new_s = Sentence.from_text(raw_text, default_singer)
                    _apply_ruby_map(new_s, ruby_map)
                    result_sentences[j1 + k] = new_s
                # 多出的旧行 → 丢弃

            elif tag == "insert":
                # 新插入行
                for k in range(j2 - j1):
                    raw_text, raw_chars, ruby_map = parsed_new[j1 + k]
                    new_s = Sentence.from_text(raw_text, default_singer)
                    _apply_ruby_map(new_s, ruby_map)
                    result_sentences[j1 + k] = new_s

            # tag == "delete": 旧行被删除，不出现在 result_sentences 中

        # 过滤掉 None（不应该有，但安全处理）
        self._project.sentences = [s for s in result_sentences if s is not None]

        if hasattr(self, "_store"):
            self._store.notify("lyrics")
            self._store.notify("rubies")
        self._update_stats()

        InfoBar.success(
            title="应用成功",
            content=f"已更新 {len(self._project.sentences)} 行",
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
