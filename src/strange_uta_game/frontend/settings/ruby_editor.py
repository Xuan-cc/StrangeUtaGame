"""全文本编辑界面。

全文本视图编辑歌词注音（ルビ），支持批量操作。
格式: {大冒険||だ|い,ぼ|う,け|ん} — `||` 分开汉字块与 ruby；
`,` 分开不同字的读音；`|` 分开同一字的多个 RubyPart（mora）。
示例 {大冒険||だ|い,ぼ|う,け|ん} = 大(だ・い) 冒(ぼ・う) 険(け・ん)。
注意：切换动作不触发自动注音 / 自动节奏点，只做字符 diff 增删。
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
    RubyPart,
)
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    CharType,
    get_char_type,
)


def _rebuild_characters(
    old_sentence: Sentence,
    new_chars: List[str],
    ruby_map: Dict[int, List[str]],
) -> List[Character]:
    """文本变更后重建 Character 列表，保留匹配字符的时间戳和配置。

    使用 SequenceMatcher 计算旧字符到新字符的映射，
    匹配到的旧字符保留 timestamps/check_count/linked_to_next/singer_id，
    新插入的字符使用默认设置。最后一个字符标记为句尾。

    ruby_map[j] 为 RubyPart.text 列表（来自新格式解析，可能为多段 mora）。
    - 匹配到的旧字符：优先保留 old_ch.ruby（含多段 RubyPart 切分），
      仅当用户在文本框里**显式改动**了该字符的 ruby 文本（ruby_map[j]
      拼接后与 old_ch.ruby.text 不同）时，才用 ruby_map[j] 覆盖。
    - 新插入字符：仅当 ruby_map[j] 明确给出时应用，否则保持空 ruby
      （切换动作不自动注音，需用户主动触发）。
    """
    old_chars_str = [c.char for c in old_sentence.characters]

    if old_chars_str == new_chars:
        # 文本未变，仅在用户显式改动 ruby 时更新
        for i, ch in enumerate(old_sentence.characters):
            if i not in ruby_map:
                continue
            new_parts = ruby_map[i]
            new_text = "".join(new_parts)
            old_text = ch.ruby.text if ch.ruby else ""
            if new_text == old_text:
                # ruby 文本一致，保留 old_ch.ruby 的完整 RubyPart 切分
                continue
            # 用户改了注音：用新 parts 覆盖，check_count 同步为 parts 长度
            ch.set_ruby(Ruby(parts=[RubyPart(text=t) for t in new_parts]))
            ch.set_check_count(len(new_parts), force=True)
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
            # 默认保留原字符的完整 ruby（含多段 RubyPart）
            if old_ch.ruby:
                ch.set_ruby(
                    Ruby(parts=[RubyPart(text=p.text) for p in old_ch.ruby.parts])
                )
            # 仅当用户显式改动 ruby 文本时才覆盖
            if j in ruby_map:
                new_parts = ruby_map[j]
                new_text = "".join(new_parts)
                old_text = old_ch.ruby.text if old_ch.ruby else ""
                if new_text != old_text:
                    ch.set_ruby(Ruby(parts=[RubyPart(text=t) for t in new_parts]))
                    ch.set_check_count(len(new_parts), force=True)
        else:
            # 新插入字符：默认 check_count=1，空 ruby
            ch = Character(
                char=new_chars[j],
                check_count=1,
                is_line_end=is_last,
                is_sentence_end=is_last,
                singer_id=old_sentence.singer_id,
            )
            # 仅当用户在文本框里显式给新字符加了 ruby 才应用
            if j in ruby_map:
                new_parts = ruby_map[j]
                ch.set_ruby(Ruby(parts=[RubyPart(text=t) for t in new_parts]))
                ch.set_check_count(len(new_parts), force=True)

        characters.append(ch)

    return characters


def _apply_ruby_map(sentence: Sentence, ruby_map: Dict[int, List[str]]) -> None:
    """将 ruby_map 应用到句子的字符上（用于新插入行）。

    仅当 ruby_map[ci] 明确给出时才应用（新行默认保持空 ruby，
    不触发自动注音）。check_count 同步为 parts 长度。
    """
    for ci, parts in ruby_map.items():
        if 0 <= ci < len(sentence.characters) and parts:
            sentence.characters[ci].set_ruby(
                Ruby(parts=[RubyPart(text=t) for t in parts])
            )
            sentence.characters[ci].set_check_count(len(parts), force=True)


def _is_kanji_char(char: str) -> bool:
    """判断是否为汉字（公用辅助）。"""
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
) -> Tuple[str, List[str], Dict[int, List[str]]]:
    """解析带注音标注的文本行（新格式）。

    新格式: ``{大冒険||だ|い,ぼ|う,け|ん}``
      - ``||`` 分开汉字块与 ruby 部分
      - ``,`` 分开不同字的 ruby
      - ``|`` 分开同一字的多个 RubyPart（mora）

    示例：
      - ``{大冒険||だ|い,ぼ|う,け|ん}`` → 大(だ・い) 冒(ぼ・う) 険(け・ん)
      - ``{漢|か|ん|じ}`` → 漢(か・ん・じ)（单字多段 mora）
      - ``{赤|あか}`` → 赤(あか)（单字单段，向后兼容）

    不再支持旧的 ``漢字{かんじ}`` 后置格式。

    Returns:
        (原文, 字符列表, ruby_map: char_idx → List[RubyPart.text])
    """
    raw_chars: List[str] = []
    ruby_map: Dict[int, List[str]] = {}
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

            if "||" in content:
                # 新主格式：text||mora|mora,mora|mora
                text_part, readings_part = content.split("||", 1)
                per_char_readings = readings_part.split(",")
                start_idx = len(raw_chars)
                for ch in text_part:
                    raw_chars.append(ch)

                for j, reading_group in enumerate(per_char_readings):
                    # reading_group 内部用 "|" 分 mora；空串代表无 ruby
                    parts = [p for p in reading_group.split("|") if p != ""]
                    if parts and (start_idx + j) < len(raw_chars):
                        ruby_map[start_idx + j] = parts
            elif "|" in content:
                # 兼容简短格式：{text|mora|mora|mora}（单字多段 mora）
                # 或 {text|reading}（单字单段）
                text_part, _, readings_part = content.partition("|")
                parts = [p for p in readings_part.split("|") if p != ""]

                start_idx = len(raw_chars)
                for ch in text_part:
                    raw_chars.append(ch)

                if len(text_part) == 1 and parts:
                    ruby_map[start_idx] = parts
                elif len(text_part) > 1 and parts:
                    # 歧义：多字只给一个 reading，兜底当作首字全吃
                    ruby_map[start_idx] = parts
            else:
                # {text} 无 ruby → 纯文本
                for ch in content:
                    raw_chars.append(ch)

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
            "全文本编辑：格式 {原文||读音1,读音2,...}，`||` 分开原文与读音，\n"
            "`,` 分开不同字，`|` 分开同一字的多 mora。例：{大冒険||だ|い,ぼ|う,け|ん}\n"
            "切换标签页时只做增删字符/行，不会重新自动注音，请主动点击「自动分析」"
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
            "示例: {大冒険||だ|い,ぼ|う,け|ん}"
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

    def scroll_to_line(self, line_idx: int, char_idx: int = 0):
        """#1：从打轴界面切换至全文本编辑时，将 QPlainTextEdit 输入光标
        跳转到 (line_idx, char_idx) 对应位置。

        文本由 _lines_to_text() 生成：每条 Sentence 占一行，连词组渲染为
        `{原文|读音,...}`。本方法用同样的遍历逻辑把"字符索引"映射到
        行内的列号（列号计入花括号/竖线/逗号等语法字符的长度），尽量把
        光标停在 char_idx 字符的起始位置。
        """
        if not self._project or not self._project.sentences:
            return
        if not (0 <= line_idx < len(self._project.sentences)):
            return
        sentence = self._project.sentences[line_idx]
        chars = sentence.characters
        if char_idx < 0:
            char_idx = 0
        if char_idx > len(chars):
            char_idx = len(chars)

        # 复刻 _lines_to_text 对单行的生成，同时累计到目标 char_idx 为止的列数
        column = 0
        i = 0
        while i < len(chars) and i < char_idx:
            if chars[i].ruby:
                group_start = i
                while i < len(chars) - 1 and chars[i].linked_to_next:
                    i += 1
                i += 1
                # 整组包含目标字符：跳到组的起始 `{` 之后的原文部分
                if group_start <= char_idx < i:
                    # 起始 `{`
                    column += 1
                    # 目标字符在原文段中的偏移（按字符计）
                    column += char_idx - group_start
                    char_idx = -1  # 提前结束
                    break
                # 整组在目标之前：累加整组生成文本长度
                text_part = "".join(ch.char for ch in chars[group_start:i])
                readings = ",".join(
                    ch.ruby.text if ch.ruby else "" for ch in chars[group_start:i]
                )
                column += len(f"{{{text_part}|{readings}}}")
            else:
                column += len(chars[i].char)
                i += 1

        # 定位 QTextCursor
        doc = self.text_edit.document()
        if doc is None:
            return
        block = doc.findBlockByNumber(line_idx)
        if not block.isValid():
            return
        cursor = self.text_edit.textCursor()
        cursor.setPosition(block.position() + min(column, block.length() - 1))
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()
        self.text_edit.setFocus()

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
        """将项目歌词转为带注音标注的文本（新格式，保留 RubyPart 切分）。

        格式: ``{大冒険||だ|い,ぼ|う,け|ん}`` — ``||`` 分开汉字块与 ruby，
        ``,`` 分开不同字，``|`` 分开同一字的多个 RubyPart（mora）。

        连词组通过 linked_to_next 链合并为同一 ``{...||...}`` 块；
        非连词且有 ruby 的字符单独输出为 ``{字||mora|mora|...}``；
        无注音的字符照常输出。
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
                    # 收集连词组（linked_to_next 链）
                    group_start = i
                    while i < len(chars) - 1 and chars[i].linked_to_next:
                        i += 1
                    i += 1  # 包含链中最后一个字符
                    text_part = "".join(ch.char for ch in chars[group_start:i])
                    # 每字的 RubyPart 列表用 `|` 拼接；不同字之间用 `,`
                    readings = ",".join(
                        "|".join(p.text for p in ch.ruby.parts) if ch.ruby else ""
                        for ch in chars[group_start:i]
                    )
                    annotated += f"{{{text_part}||{readings}}}"
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
        """自动分析全部注音：拆成注音与节奏点两步；节奏点失败不影响注音更新。

        弹三选项对话框：
        - 全部重新分析（覆盖已有注音）
        - 仅分析未注音字符（保留已有注音）
        - 取消
        """
        if not self._project:
            return

        # 三选项对话框
        msg = QMessageBox(self)
        msg.setWindowTitle("自动分析全部注音")
        msg.setText("请选择分析范围：")
        msg.setInformativeText(
            "「全部重新分析」会覆盖现有注音。\n"
            "「仅分析未注音字符」会保留已有的人工/字典注音。"
        )
        btn_all = msg.addButton("全部重新分析", QMessageBox.ButtonRole.DestructiveRole)
        btn_only_noruby = msg.addButton(
            "仅分析未注音字符", QMessageBox.ButtonRole.AcceptRole
        )
        btn_cancel = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_only_noruby)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return
        only_noruby = clicked is btn_only_noruby

        auto_check = self._create_auto_check_service()

        # 第一步：应用注音
        try:
            auto_check.apply_to_project(self._project, only_noruby=only_noruby)
            self._refresh_display()
            if hasattr(self, "_store"):
                self._store.notify("rubies")
        except Exception as e:
            InfoBar.warning(
                title="注音分析失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        # 第二步：更新节奏点（失败时保留已更新的注音）
        try:
            auto_check.update_checkpoints_for_project(self._project)
            if hasattr(self, "_store"):
                self._store.notify("checkpoints")
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
                title="节奏点更新失败",
                content=f"注音已更新，但节奏点更新失败: {e}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
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

        # 构建扩展匹配集：勾选平假名时包含小假名(ぁぃ等)+促音(っ)，勾选片假名时包含小假名(ァィ等)+促音(ッ)
        _SMALL_HIRAGANA = set("ぁぃぅぇぉゃゅょゎ")
        _SMALL_KATAKANA = set("ァィゥェォャュョヮゕゖ")
        extended = set(selected)
        if CharType.HIRAGANA in selected:
            extended.add(CharType.SOKUON)  # っ
        if CharType.KATAKANA in selected:
            extended.add(CharType.SOKUON)  # ッ

        removed = 0
        for sentence in self._project.sentences:
            for ch in sentence.characters:
                if not ch.ruby:
                    continue
                ct = get_char_type(ch.char)
                if ct in extended:
                    # SOKUON 同时覆盖平假名/片假名两侧，需按实际字符过滤
                    if ct == CharType.SOKUON:
                        if ch.char == "っ" and CharType.HIRAGANA not in selected:
                            continue
                        if ch.char == "ッ" and CharType.KATAKANA not in selected:
                            continue
                    ch.set_ruby(None)
                    removed += 1
                elif CharType.HIRAGANA in selected and ch.char in _SMALL_HIRAGANA:
                    ch.set_ruby(None)
                    removed += 1
                elif CharType.KATAKANA in selected and ch.char in _SMALL_KATAKANA:
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
