"""打轴编辑对话框集合。

包含三个字符级编辑对话框：
- ``ModifyCharacterDialog`` : 批量修改字符/注音
- ``InsertGuideSymbolDialog`` : 插入制导符号
- ``CharEditDialog`` : 单字符编辑
"""

from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import PrimaryPushButton, PushButton

from strange_uta_game.backend.domain import (
    Character,
    Ruby,
    RubyPart,
    Sentence,
)


class ModifyCharacterDialog(QDialog):
    """修改所选字符对话框 — 替换选中区间的文本、注音、节奏点、连词。

    字符级独立输入框方案（批 18 #1/#2/#3）：
      - 顶部"新字符"文本框决定字符序列
      - 下方按新文本长度动态生成每字符一行：[字符] [注音] [节奏点] [是否连词]
      - 注音框内用半角逗号分隔 RubyPart（如 わ,た,し → 3 个 RubyPart）
      - 文本修改时自动重建字符行，并按位置尽量保留已输入值
      - 单字符修改时直接原地 set_ruby/check_count/linked_to_next/push_to_ruby，保留 timestamps
      - 字符数变化时才走替换 slice 流程（必然丢旧 timestamps）
      - 连词校验：末字/句尾/行尾字符禁止 linked_to_next=True，
        提交时若有违规项则跳过该项的 linked_to_next 并在 failures 列表返回。
    """

    def __init__(self, sentence, start_idx, end_idx, parent=None):
        super().__init__(parent)
        self._sentence = sentence
        self._start_idx = start_idx
        self._end_idx = end_idx
        self._modified = False
        self._linked_failures: list[tuple[int, str, str]] = []
        # (pos, char, reason) 列表，执行后由调用方读取弹窗汇总
        self._char_rows: list[tuple[QLabel, QLineEdit, QLineEdit, QCheckBox]] = []

        self.setWindowTitle("修改所选字符")
        self.resize(520, 440)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)

        # 原字符显示 + 新字符输入
        chars = sentence.characters[start_idx : end_idx + 1]
        current_text = "".join(c.char for c in chars)

        top_form = QFormLayout()
        lbl_current = QLabel(current_text)
        lbl_current.setStyleSheet("font-size: 16px; font-weight: bold;")
        top_form.addRow("当前选中字符:", lbl_current)
        self.edit_new_chars = QLineEdit(current_text)
        self.edit_new_chars.setPlaceholderText("输入新字符")
        top_form.addRow("新字符:", self.edit_new_chars)
        layout.addLayout(top_form)

        # 字符级编辑区标题
        hint = QLabel("按字符编辑（注音用半角逗号分隔 RubyPart；节奏点为非负整数）:")
        hint.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(hint)

        # Scroll area with per-char rows
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(4)
        scroll.setWidget(self._rows_container)
        layout.addWidget(scroll, stretch=1)

        # 初始按当前字符填充
        for c in chars:
            ruby_str = (
                ",".join(p.text for p in c.ruby.parts) if c.ruby and c.ruby.parts else ""
            )
            self._append_char_row(c.char, ruby_str, str(c.check_count), c.linked_to_next)

        # 文本变更 → 重建行，保留已输入值
        self.edit_new_chars.textChanged.connect(self._rebuild_rows_on_text_change)

        # 注册词典
        self.chk_register = QCheckBox("将此词注册到读音词典")
        layout.addWidget(self.chk_register)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_exec = PrimaryPushButton("执行", self)
        btn_exec.clicked.connect(self._on_execute)
        btn_layout.addWidget(btn_exec)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _append_char_row(
        self, char_str: str, ruby_str: str, check_str: str, linked: bool = False
    ):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        lbl = QLabel(char_str)
        lbl.setFixedWidth(32)
        lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        edit_ruby = QLineEdit(ruby_str)
        edit_ruby.setPlaceholderText("注音（逗号分隔多 RubyPart）")
        edit_check = QLineEdit(check_str)
        edit_check.setPlaceholderText("节奏点")
        edit_check.setFixedWidth(64)
        chk_linked = QCheckBox("是否连词")
        chk_linked.setChecked(bool(linked))
        chk_linked.setToolTip(
            "连接到下一字符（末字/句尾/行尾不可连词，提交时将跳过并提示）"
        )
        row_layout.addWidget(lbl)
        row_layout.addWidget(edit_ruby, stretch=1)
        row_layout.addWidget(edit_check)
        row_layout.addWidget(chk_linked)
        self._rows_layout.addWidget(row_widget)
        self._char_rows.append((lbl, edit_ruby, edit_check, chk_linked))

    def _rebuild_rows_on_text_change(self, new_text: str):
        # 保留旧输入值按索引对齐
        old_vals = [
            (e_r.text(), e_c.text(), chk.isChecked())
            for _, e_r, e_c, chk in self._char_rows
        ]
        # 清空现有行
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._char_rows.clear()
        for i, ch in enumerate(new_text):
            if i < len(old_vals):
                r_val, c_val, l_val = old_vals[i]
            else:
                r_val, c_val, l_val = "", "1", False
            self._append_char_row(ch, r_val, c_val, l_val)

    def _parse_ruby(self, raw: str):
        from strange_uta_game.backend.domain.models import Ruby, RubyPart

        text = raw.strip()
        if not text:
            return None
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if not parts:
            return None
        return Ruby(parts=[RubyPart(text=p) for p in parts])

    def _on_execute(self):
        from strange_uta_game.backend.domain.models import Character

        new_text = self.edit_new_chars.text().strip()
        if not new_text:
            return

        # 收集每行值：ruby / check_count / linked_to_next
        per_char_ruby = []
        per_char_check = []
        per_char_linked_req = []  # 用户请求的 linked_to_next
        for i in range(len(new_text)):
            if i >= len(self._char_rows):
                per_char_ruby.append(None)
                per_char_check.append(1)
                per_char_linked_req.append(False)
                continue
            _, edit_ruby, edit_check, chk_linked = self._char_rows[i]
            per_char_ruby.append(self._parse_ruby(edit_ruby.text()))
            try:
                per_char_check.append(max(0, int(edit_check.text().strip())))
            except ValueError:
                per_char_check.append(1)
            per_char_linked_req.append(bool(chk_linked.isChecked()))

        old_chars = self._sentence.characters[self._start_idx : self._end_idx + 1]
        old_last_is_sentence_end = old_chars[-1].is_sentence_end if old_chars else False
        old_last_is_line_end = old_chars[-1].is_line_end if old_chars else False
        singer_id = old_chars[0].singer_id if old_chars else ""

        self._linked_failures = []

        if len(new_text) == len(old_chars):
            # 字符数不变 → 原地修改，保留 timestamps 和 offset
            for i, ch_str in enumerate(new_text):
                tgt = old_chars[i]
                tgt.char = ch_str
                # 已配套 set_ruby 替换，force=True 安全（无 mora 退化）
                tgt.set_ruby(per_char_ruby[i])
                tgt.set_check_count(per_char_check[i], force=True)
                tgt.push_to_ruby()
                # linked_to_next 校验：末字/句尾/行尾字符禁止连词
                req_linked = per_char_linked_req[i]
                abs_idx = self._start_idx + i
                sentence_len = len(self._sentence.characters)
                is_last_in_sentence = abs_idx >= sentence_len - 1
                if req_linked and (
                    is_last_in_sentence
                    or tgt.is_sentence_end
                    or tgt.is_line_end
                ):
                    reason = (
                        "最后一个字符"
                        if is_last_in_sentence
                        else ("句尾" if tgt.is_sentence_end else "行尾")
                    )
                    self._linked_failures.append((abs_idx, ch_str, reason))
                    tgt.linked_to_next = False
                else:
                    tgt.linked_to_next = req_linked
            # 句尾 / 行末由原字符保留，不动 is_sentence_end / is_line_end
        else:
            # 字符数变化 → 替换 slice（无法保留 timestamps）
            new_chars = []
            for i, ch_str in enumerate(new_text):
                new_ch = Character(
                    char=ch_str,
                    ruby=per_char_ruby[i],
                    check_count=per_char_check[i],
                    singer_id=singer_id,
                    linked_to_next=False,
                    is_line_end=False,
                    is_sentence_end=False,
                )
                new_chars.append(new_ch)
            if old_last_is_sentence_end:
                new_chars[-1].is_sentence_end = True
            if old_last_is_line_end:
                new_chars[-1].is_line_end = True
            # 应用 linked_to_next（需与新的句尾/行尾/末字状态校验）
            total_after = (
                len(self._sentence.characters) - len(old_chars) + len(new_chars)
            )
            for i, new_ch in enumerate(new_chars):
                req_linked = per_char_linked_req[i]
                abs_idx = self._start_idx + i
                is_last_in_sentence = abs_idx >= total_after - 1
                if req_linked and (
                    is_last_in_sentence
                    or new_ch.is_sentence_end
                    or new_ch.is_line_end
                ):
                    reason = (
                        "最后一个字符"
                        if is_last_in_sentence
                        else ("句尾" if new_ch.is_sentence_end else "行尾")
                    )
                    self._linked_failures.append((abs_idx, new_ch.char, reason))
                    new_ch.linked_to_next = False
                else:
                    new_ch.linked_to_next = req_linked
            self._sentence.characters[self._start_idx : self._end_idx + 1] = new_chars

        # 词典注册：用户输入的第一段 ruby 拼接
        if self.chk_register.isChecked():
            readings = []
            for r in per_char_ruby:
                if r and r.parts:
                    readings.append("".join(p.text for p in r.parts))
                else:
                    readings.append("")
            self._register_to_dictionary(new_text, readings)

        self._modified = True
        self.accept()

    def get_linked_failures(self) -> list[tuple[int, str, str]]:
        """返回应用连词时因末字/句尾/行尾被跳过的项列表（abs_idx, char, reason）。"""
        return list(self._linked_failures)

    def _register_to_dictionary(self, word: str, ruby_parts: list):
        """Register word to user dictionary (dedup + top-insert)."""
        try:
            from strange_uta_game.frontend.settings.settings_interface import (
                AppSettings,
            )

            reading = ",".join(r for r in ruby_parts if r)
            AppSettings().register_dictionary_word(word, reading)
        except Exception:
            pass

    def was_modified(self) -> bool:
        return self._modified


class InsertGuideSymbolDialog(QDialog):
    """插入导唱符对话框 — 在选中字符前插入导唱用字符"""

    def __init__(self, sentence, char_idx, parent=None):
        """
        Args:
            sentence: Sentence object
            char_idx: current selected char index (guide symbols insert BEFORE this)
            parent: parent widget
        """
        super().__init__(parent)
        self._sentence = sentence
        self._char_idx = char_idx
        self._modified = False

        self.setWindowTitle("插入导唱符")
        self.resize(400, 280)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Field 1: Current selected char (readonly)
        ch = sentence.characters[char_idx]
        lbl_current = QLabel(ch.char)
        lbl_current.setStyleSheet("font-size: 16px; font-weight: bold;")
        form.addRow("当前选中字符:", lbl_current)

        # Field 2: Guide symbol text
        self.edit_symbol = QLineEdit("")
        self.edit_symbol.setPlaceholderText("请填写要插入的导唱符")
        form.addRow("导唱符:", self.edit_symbol)

        # Field 3: Count
        self.edit_count = QLineEdit("1")
        self.edit_count.setPlaceholderText("个数")
        form.addRow("个数:", self.edit_count)

        # Field 4: Duration per symbol
        self.edit_duration = QLineEdit("1000")
        self.edit_duration.setPlaceholderText("每个导唱符持续时间（毫秒）")
        form.addRow("持续时间 (ms):", self.edit_duration)

        layout.addLayout(form)
        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_exec = PrimaryPushButton("执行", self)
        btn_exec.clicked.connect(self._on_execute)
        btn_layout.addWidget(btn_exec)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _on_execute(self):
        from strange_uta_game.backend.domain.models import Character

        symbol = self.edit_symbol.text().strip()
        if not symbol:
            return

        try:
            count = max(1, int(self.edit_count.text().strip()))
        except ValueError:
            count = 1

        try:
            duration_ms = max(100, int(self.edit_duration.text().strip()))
        except ValueError:
            duration_ms = 1000

        # Get reference char's timestamp and singer
        ref_char = self._sentence.characters[self._char_idx]
        singer_id = ref_char.singer_id

        # Get reference timestamp (first timestamp of selected char)
        ref_ts = ref_char.timestamps[0] if ref_char.timestamps else None

        # Build guide characters
        # Each guide symbol has linked_to_next=True (they chain), except last
        # Actually: if symbol is multi-char, each char of the symbol is linked.
        # If count > 1, each "symbol group" is also linked.
        # Result: all guide chars are linked_to_next=True (chained as one word)
        guide_chars = []
        for i in range(count):
            for j, ch_str in enumerate(symbol):
                is_last_of_symbol = j == len(symbol) - 1
                is_last_symbol = i == count - 1
                is_last_char = is_last_of_symbol and is_last_symbol
                new_ch = Character(
                    char=ch_str,
                    ruby=None,
                    check_count=1 if is_last_of_symbol else 0,
                    singer_id=singer_id,
                    linked_to_next=not is_last_char,
                )
                # Set timestamp if reference exists
                if ref_ts is not None and is_last_of_symbol:
                    # For i-th symbol (0-indexed), timestamp = ref_ts - duration_ms * (count - i)
                    ts = ref_ts - duration_ms * (count - i)
                    if ts >= 0:
                        new_ch.add_timestamp(ts)
                guide_chars.append(new_ch)

        # Insert guide chars BEFORE the selected char
        for idx, gc in enumerate(guide_chars):
            self._sentence.characters.insert(self._char_idx + idx, gc)

        self._modified = True
        self.accept()

    def was_modified(self) -> bool:
        return self._modified


class CharEditDialog(QDialog):
    """注音编辑对话框 — 支持连词（Ruby 合并/拆分）

    连词功能：用 + 号将相邻字符合并显示。
    在 per-char Ruby 模型中，每个字符独立拥有自己的 Ruby 对象。
    连词由 Character.linked_to_next 标记控制。

    UI 布局：
    - 当前字符显示（只读）
    - 注音文本编辑
    - 确定/取消按钮
    """

    def __init__(self, sentence: "Sentence", char_idx: int, parent=None):
        super().__init__(parent)
        self._sentence = sentence
        self._char_idx = char_idx
        self._modified = False

        self.setWindowTitle("编辑注音")
        self.resize(360, 220)
        self.setFont(QFont("Microsoft YaHei", 10))

        form = QFormLayout(self)

        # 当前字符（只读）— 显示连词组内所有字符
        ch = sentence.characters[char_idx]
        # 查找连词组范围
        word_start, word_end = sentence.get_word_char_range(char_idx)
        if word_end - word_start > 1:
            display = " + ".join(
                sentence.characters[i].char for i in range(word_start, word_end)
            )
        else:
            display = ch.char

        lbl_char = QLabel(display)
        lbl_char.setStyleSheet("font-size: 16px; font-weight: bold;")
        form.addRow("字符:", lbl_char)

        # 注音编辑 — 如果是连词组，显示逗号分隔的各字符 ruby
        if word_end - word_start > 1:
            parts = []
            for i in range(word_start, word_end):
                r = sentence.characters[i].ruby
                parts.append(r.text if r else "")
            initial_ruby = ",".join(parts) if any(parts) else ""
        else:
            initial_ruby = ch.ruby.text if ch.ruby else ""

        self.edit_ruby = QLineEdit(initial_ruby)
        self.edit_ruby.setPlaceholderText("输入注音（留空则删除注音）")
        form.addRow("注音:", self.edit_ruby)

        # 连词范围提示
        if word_end - word_start > 1:
            range_text = " + ".join(
                sentence.characters[i].char for i in range(word_start, word_end)
            )
            lbl_range = QLabel(f"当前连词范围: {range_text}（逗号分隔各字符注音）")
            lbl_range.setStyleSheet("font-size: 11px; color: gray;")
            form.addRow("", lbl_range)

        self._word_start = word_start
        self._word_end = word_end

        # 按钮
        btn_layout = QHBoxLayout()
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        form.addRow(btn_layout)

    def _on_accept(self):
        new_ruby_text = self.edit_ruby.text().strip()
        word_len = self._word_end - self._word_start

        if not new_ruby_text:
            # 清空连词组内所有字符的 ruby
            for i in range(self._word_start, self._word_end):
                if self._sentence.characters[i].ruby:
                    self._sentence.characters[i].set_ruby(None)
                    self._modified = True
            self.accept()
            return

        if word_len > 1 and "," in new_ruby_text:
            # 连词组：按逗号分隔赋给各字符
            parts = [p.strip() for p in new_ruby_text.split(",")]
            # 如果 parts 数量不足，用空字符串补齐
            while len(parts) < word_len:
                parts.append("")
            for i, part in enumerate(parts[:word_len]):
                ci = self._word_start + i
                if part:
                    self._sentence.characters[ci].set_ruby(Ruby(parts=[RubyPart(text=part)]))
                else:
                    self._sentence.characters[ci].set_ruby(None)
                self._modified = True
        elif word_len > 1:
            # 连词组但无逗号：按 mora 均分
            from strange_uta_game.backend.infrastructure.parsers.inline_format import (
                split_ruby_for_checkpoints,
            )

            split_parts = split_ruby_for_checkpoints(new_ruby_text, word_len)
            for i, part in enumerate(split_parts):
                ci = self._word_start + i
                if part:
                    self._sentence.characters[ci].set_ruby(Ruby(parts=[RubyPart(text=part)]))
                else:
                    self._sentence.characters[ci].set_ruby(None)
                self._modified = True
        else:
            # 单字符
            self._sentence.characters[self._char_idx].set_ruby(Ruby(parts=[RubyPart(text=new_ruby_text)]))
            self._modified = True

        self.accept()

    def was_modified(self) -> bool:
        return self._modified


# ──────────────────────────────────────────────
# 时间轴
# ──────────────────────────────────────────────
