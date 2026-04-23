"""批量变更对话框 (Ctrl+H)。

以"修改所选字符"对话框为模板的批量版：顶部加一个"搜索词"字段，
对项目中所有匹配该词的字符区间应用同一份字符级编辑。

行为契约：
- 搜索词空 → 禁用执行
- 新字符长度 == 搜索词长度 → 每处匹配原地修改，保留 timestamps
- 新字符长度 != 搜索词长度 → 弹确认，确认后逐处替换 slice（丢所有匹配处 timestamps）
- 执行后不关闭对话框，显示"已修改 N 处"
"""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QFormLayout,
    QLineEdit,
    QScrollArea,
    QWidget,
    QMessageBox,
)
from PyQt6.QtGui import QFont
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
)
from typing import Optional, List, Tuple

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.domain.models import Character, Ruby, RubyPart


class BulkChangeDialog(QDialog):
    """批量变更对话框 — 搜索词 + 字符级编辑，批量应用到所有匹配处。

    构造参数：
        project: 当前项目（None 时执行按钮不起作用）
        parent: 父窗口（期望具备 _store / _timing_service / refresh_lyric_display 等）
        initial_word: 初始搜索词
        initial_reading: 初始注音（逗号分隔，对应每个字符）
    """

    def __init__(
        self,
        project: Optional[Project],
        parent=None,
        initial_word: str = "",
        initial_reading: str = "",
    ):
        super().__init__(parent)
        self._project = project
        self._char_rows: List[Tuple[QLabel, QLineEdit, QLineEdit]] = []
        # 用户是否已手动编辑过 rows / 新字符框；一旦手动编辑，搜索词变化不再覆盖
        self._rows_user_edited = False
        self._new_chars_user_edited = False
        # 抑制程序性 textChanged 触发的标志
        self._suppress_row_signals = False
        self._suppress_new_chars_signal = False

        self.setWindowTitle("批量变更 (Ctrl+H)")
        self.resize(520, 480)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)

        # 搜索词行
        search_row = QHBoxLayout()
        self.edit_word = QLineEdit(initial_word)
        self.edit_word.setPlaceholderText("输入要搜索的词")
        self.lbl_match = QLabel("")
        self.lbl_match.setStyleSheet("font-size: 11px; color: gray;")
        search_row.addWidget(QLabel("搜索词:"))
        search_row.addWidget(self.edit_word, stretch=1)
        search_row.addWidget(self.lbl_match)
        layout.addLayout(search_row)

        # 新字符行
        top_form = QFormLayout()
        self.edit_new_chars = QLineEdit(initial_word)
        self.edit_new_chars.setPlaceholderText("输入替换后的字符（默认=搜索词）")
        top_form.addRow("替换为:", self.edit_new_chars)
        layout.addLayout(top_form)

        hint = QLabel(
            "按字符编辑（注音用半角逗号分隔 RubyPart；节奏点为非负整数）。\n"
            "字符数与搜索词相同 → 保留时间戳；不同 → 丢失所有匹配处时间戳。"
        )
        hint.setStyleSheet("font-size: 11px; color: gray;")
        hint.setWordWrap(True)
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

        # 注册到词典
        self.chk_register = QCheckBox("将此词注册到读音词典")
        layout.addWidget(self.chk_register)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_exec = PrimaryPushButton("执行", self)
        self.btn_exec.clicked.connect(self._on_execute)
        btn_row.addWidget(self.btn_exec)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        # 信号连接
        self.edit_word.textChanged.connect(self._on_word_changed)
        self.edit_new_chars.textChanged.connect(self._on_new_chars_changed)

        # 首次填充：按初始搜索词首匹配
        self._refresh_match_count()
        self._refill_from_first_match(initial_reading)

    # ---------- 行管理 ----------

    def _append_char_row(self, char_str: str, ruby_str: str, check_str: str):
        """追加一行：[字符] [注音] [节奏点]。"""
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
        # 监控用户手动编辑
        edit_ruby.textEdited.connect(self._on_row_user_edited)
        edit_check.textEdited.connect(self._on_row_user_edited)
        row_layout.addWidget(lbl)
        row_layout.addWidget(edit_ruby, stretch=1)
        row_layout.addWidget(edit_check)
        self._rows_layout.addWidget(row_widget)
        self._char_rows.append((lbl, edit_ruby, edit_check))

    def _clear_rows(self):
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._char_rows.clear()

    def _rebuild_rows_for_text(
        self,
        new_text: str,
        ruby_list: Optional[List[str]] = None,
        check_list: Optional[List[str]] = None,
    ):
        """按 new_text 重建行；ruby_list/check_list 为初始值（按索引对齐，不足补空/1）。"""
        # 若未传初始值，尝试保留现有 rows 的输入值
        if ruby_list is None or check_list is None:
            old_vals = [(e_r.text(), e_c.text()) for _, e_r, e_c in self._char_rows]
        else:
            old_vals = list(zip(ruby_list, check_list))
        self._suppress_row_signals = True
        try:
            self._clear_rows()
            for i, ch in enumerate(new_text):
                if i < len(old_vals):
                    r_val, c_val = old_vals[i]
                else:
                    r_val, c_val = "", "1"
                self._append_char_row(ch, r_val, c_val)
        finally:
            self._suppress_row_signals = False

    # ---------- 信号处理 ----------

    def _on_row_user_edited(self, _text: str):
        if self._suppress_row_signals:
            return
        self._rows_user_edited = True

    def _on_new_chars_changed(self, new_text: str):
        if not self._suppress_new_chars_signal:
            self._new_chars_user_edited = True
        # 文本变化 → 按新长度重建行，保留现有输入
        self._rebuild_rows_for_text(new_text)

    def _on_word_changed(self, _word: str):
        self._refresh_match_count()
        # 若用户未手动改过 rows 和新字符框 → 用新搜索词首匹配覆盖
        if not self._rows_user_edited and not self._new_chars_user_edited:
            self._refill_from_first_match("")

    # ---------- 匹配扫描 ----------

    def _iter_matches(self, word: str):
        """生成 (sentence, start_pos) 非重叠匹配；空词返回空。"""
        if not self._project or not word:
            return
        w_len = len(word)
        for sentence in self._project.sentences:
            text = sentence.text
            pos = 0
            while pos <= len(text) - w_len:
                if text[pos : pos + w_len] == word:
                    yield sentence, pos
                    pos += w_len
                else:
                    pos += 1

    def _refresh_match_count(self):
        word = self.edit_word.text().strip()
        if not word:
            self.lbl_match.setText("")
            return
        count = sum(1 for _ in self._iter_matches(word))
        self.lbl_match.setText(f"找到 {count} 处")

    def _refill_from_first_match(self, fallback_reading: str):
        """用首个匹配的字符/注音/节奏点填充新字符框和 rows。

        若无匹配：用搜索词填新字符框，rows 用搜索词字符 + fallback_reading 拆分。
        """
        word = self.edit_word.text().strip()
        if not word:
            self._suppress_new_chars_signal = True
            try:
                self.edit_new_chars.setText("")
            finally:
                self._suppress_new_chars_signal = False
            self._rebuild_rows_for_text("")
            return

        first = next(iter(self._iter_matches(word)), None)
        w_len = len(word)
        if first is not None:
            sentence, pos = first
            chars = sentence.characters[pos : pos + w_len]
            new_text = "".join(c.char for c in chars)
            ruby_list = [
                ",".join(p.text for p in c.ruby.parts)
                if c.ruby and c.ruby.parts
                else ""
                for c in chars
            ]
            check_list = [str(c.check_count) for c in chars]
        else:
            # 无匹配：用搜索词 + fallback reading
            new_text = word
            if fallback_reading:
                parts = [p.strip() for p in fallback_reading.split(",")]
                ruby_list = [parts[i] if i < len(parts) else "" for i in range(w_len)]
            else:
                ruby_list = ["" for _ in range(w_len)]
            check_list = ["1" for _ in range(w_len)]

        self._suppress_new_chars_signal = True
        try:
            self.edit_new_chars.setText(new_text)
        finally:
            self._suppress_new_chars_signal = False
        self._rebuild_rows_for_text(new_text, ruby_list, check_list)

    # ---------- 解析 ----------

    def _parse_ruby(self, raw: str) -> Optional[Ruby]:
        text = raw.strip()
        if not text:
            return None
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if not parts:
            return None
        return Ruby(parts=[RubyPart(text=p) for p in parts])

    def _collect_per_char(self, new_text: str) -> Tuple[List[Optional[Ruby]], List[int]]:
        per_char_ruby: List[Optional[Ruby]] = []
        per_char_check: List[int] = []
        for i in range(len(new_text)):
            if i >= len(self._char_rows):
                per_char_ruby.append(None)
                per_char_check.append(1)
                continue
            _, edit_ruby, edit_check = self._char_rows[i]
            per_char_ruby.append(self._parse_ruby(edit_ruby.text()))
            try:
                per_char_check.append(max(0, int(edit_check.text().strip())))
            except ValueError:
                per_char_check.append(1)
        return per_char_ruby, per_char_check

    # ---------- 执行 ----------

    def _on_execute(self):
        if not self._project:
            return
        word = self.edit_word.text().strip()
        if not word:
            return
        new_text = self.edit_new_chars.text().strip()
        if not new_text:
            return

        per_char_ruby, per_char_check = self._collect_per_char(new_text)

        # 收集所有匹配（按 sentence 分组，位置升序）
        matches_by_sentence: dict = {}
        for sentence, pos in self._iter_matches(word):
            matches_by_sentence.setdefault(id(sentence), (sentence, []))[1].append(pos)
        total_matches = sum(len(v[1]) for v in matches_by_sentence.values())
        if total_matches == 0:
            self.lbl_match.setText("找到 0 处（无改动）")
            return

        same_len = len(new_text) == len(word)
        if not same_len:
            # 丢时间戳确认
            reply = QMessageBox.question(
                self,
                "确认批量替换",
                f"替换后字符数 ({len(new_text)}) 与搜索词 ({len(word)}) 不同，\n"
                f"将丢失全部 {total_matches} 处匹配的时间戳。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        changed = 0
        word_len = len(word)
        for sentence, positions in matches_by_sentence.values():
            if same_len:
                # 原地修改，正向遍历即可（长度不变，索引稳定）
                for pos in positions:
                    for i, ch_str in enumerate(new_text):
                        ci = pos + i
                        if ci >= len(sentence.characters):
                            break
                        tgt = sentence.characters[ci]
                        tgt.char = ch_str
                        tgt.check_count = per_char_check[i]
                        tgt.set_ruby(per_char_ruby[i])
                        tgt.push_to_ruby()
                    changed += 1
            else:
                # 替换 slice，必须倒序以保持前序位置稳定
                for pos in sorted(positions, reverse=True):
                    old_chars = sentence.characters[pos : pos + word_len]
                    if not old_chars:
                        continue
                    old_last_is_sentence_end = old_chars[-1].is_sentence_end
                    old_last_is_line_end = old_chars[-1].is_line_end
                    singer_id = old_chars[0].singer_id
                    new_chars = []
                    for i, ch_str in enumerate(new_text):
                        # per_char_ruby 每次新建独立 Ruby 对象避免共享
                        src_ruby = per_char_ruby[i]
                        ruby_copy = (
                            Ruby(parts=[RubyPart(text=p.text) for p in src_ruby.parts])
                            if src_ruby is not None
                            else None
                        )
                        new_ch = Character(
                            char=ch_str,
                            ruby=ruby_copy,
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
                    sentence.characters[pos : pos + word_len] = new_chars
                    changed += 1

        # 注册到词典
        if self.chk_register.isChecked():
            self._register_to_dictionary(new_text, per_char_ruby)

        # 通知父窗口刷新
        parent = self.parent()
        timing_service = getattr(parent, "_timing_service", None)
        if timing_service is not None:
            try:
                timing_service._rebuild_global_checkpoints()
            except Exception:
                pass
        refresh = getattr(parent, "refresh_lyric_display", None)
        if callable(refresh):
            refresh()
        update_time_tags = getattr(parent, "_update_time_tags_display", None)
        if callable(update_time_tags):
            update_time_tags()
        update_status = getattr(parent, "_update_status", None)
        if callable(update_status):
            update_status()
        store = getattr(parent, "_store", None)
        if store is not None:
            store.notify("rubies")
            store.notify("checkpoints")
            store.notify("lyrics")
            store.notify("timetags")

        self.lbl_match.setText(f"已修改 {changed} 处")
        # 一次执行后，后续搜索词变化不应再覆盖 rows（用户已 commit 过）
        self._rows_user_edited = True

    def _register_to_dictionary(
        self, word: str, per_char_ruby: List[Optional[Ruby]]
    ):
        """将词注册到用户字典（去重 + 顶部插入）。"""
        try:
            from strange_uta_game.frontend.settings.settings_interface import (
                AppSettings,
            )

            readings = []
            for r in per_char_ruby:
                if r and r.parts:
                    readings.append("".join(p.text for p in r.parts))
                else:
                    readings.append("")
            reading = ",".join(s for s in readings if s)
            AppSettings().register_dictionary_word(word, reading)
        except Exception:
            pass
