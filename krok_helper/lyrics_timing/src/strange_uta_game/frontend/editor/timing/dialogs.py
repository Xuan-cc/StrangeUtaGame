"""打轴编辑对话框集合。

包含以下编辑对话框：
- ``ModifyCharacterDialog`` : 批量修改字符/注音
- ``InsertGuideSymbolDialog`` : 插入制导符号
- ``CharEditDialog`` : 单字符编辑
- ``SetSingerByLineDialog`` : 按行设置演唱者
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (

    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QButtonGroup,
)


def _make_singer_color_pixmap(colors: list, w: int = 32, h: int = 18) -> QPixmap:
    """生成演唱者颜色预览 pixmap（支持分色，从上到下均分色带）"""
    pixmap = QPixmap(w, h)
    p = QPainter(pixmap)
    n = len(colors) if colors else 1
    base = colors[0] if colors else "#CCCCCC"
    if n == 1:
        p.fillRect(QRect(0, 0, w, h), QColor(base))
    else:
        for i, c in enumerate(colors):
            y0 = int(i * h / n)
            y1 = int((i + 1) * h / n)
            p.fillRect(QRect(0, y0, w, y1 - y0), QColor(c))
    p.end()
    return pixmap
from qfluentwidgets import (
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    CaptionLabel,
    BodyLabel,
    StrongBodyLabel,
    LineEdit,
    CheckBox,
    ScrollArea,
    SpinBox,
    setCustomStyleSheet,
)

from strange_uta_game.frontend.theme import theme, ThemeColors

from strange_uta_game.backend.domain import (
    Character,
    Ruby,
    RubyPart,
    Sentence,
    Singer,
)
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    distribute_ruby_chars_evenly,
)


def _get_ruby_split_mode() -> str:
    """获取注音分段方式配置值"""
    try:
        from strange_uta_game.frontend.settings.app_settings import AppSettings
        settings = AppSettings()
        return settings.get("ruby_split_mode", "mora")
    except Exception:
        return "mora"


def _set_ruby_split_mode(mode: str) -> None:
    """设置注音分段方式配置值"""
    try:
        from strange_uta_game.frontend.settings.app_settings import AppSettings
        settings = AppSettings()
        settings.set("ruby_split_mode", mode)
        settings.save()
    except Exception:
        pass


def _create_ruby_split_group(parent: QWidget) -> tuple[QRadioButton, QRadioButton, QRadioButton, QGroupBox]:
    """创建注音分段方式选择组

    Returns:
        (radio_direct, radio_by_char, radio_by_mora, group_box)
    """
    group_box = QGroupBox("注音分段方式")
    group_layout = QVBoxLayout(group_box)

    radio_direct = QRadioButton("直接应用（用逗号手动分段，无逗号则不分段）")
    radio_by_char = QRadioButton("按字符均分")
    radio_by_mora = QRadioButton("按 mora 均分（推荐）")

    # 读取配置值
    mode = _get_ruby_split_mode()
    if mode == "direct":
        radio_direct.setChecked(True)
    elif mode == "char":
        radio_by_char.setChecked(True)
    else:
        radio_by_mora.setChecked(True)

    group_layout.addWidget(radio_direct)
    group_layout.addWidget(radio_by_char)
    group_layout.addWidget(radio_by_mora)

    return radio_direct, radio_by_char, radio_by_mora, group_box


def _save_ruby_split_mode(radio_direct: QRadioButton, radio_by_char: QRadioButton, radio_by_mora: QRadioButton) -> None:
    """保存注音分段方式配置值"""
    if radio_direct.isChecked():
        _set_ruby_split_mode("direct")
    elif radio_by_char.isChecked():
        _set_ruby_split_mode("char")
    else:
        _set_ruby_split_mode("mora")


# ── 字符编辑类对话框（修改所选字符 / 编辑字符(F2) / 批量变更）统一外观 ──
# 三窗口共用同一默认尺寸与字体层级，保证视觉一致、信息区分度高。
CHAR_DIALOG_SIZE = (600, 700)
_FONT_FAMILY = "Microsoft YaHei"

# 字体层级（pt）：显示值 > 字符字形 > 主输入 > 字段标签/行内输入 > 说明
FONT_DIALOG_BASE = 10      # 对话框基础字体
FONT_VALUE_DISPLAY = 16    # 顶部只读"当前字符"显示值（最醒目，加粗）
FONT_CHAR_GLYPH = 14       # 每字符行的字形标签（加粗）
FONT_MAIN_INPUT = 12       # 顶部"新字符/替换为"主输入框
FONT_FIELD_LABEL = 11      # 表单字段标签（当前字符: / 新字符: / 搜索词:）
FONT_ROW_INPUT = 11        # 每字符行的注音/节奏点输入框


def char_dialog_font(size: int, bold: bool = False) -> QFont:
    """构造字符编辑类对话框统一字体。"""
    f = QFont(_FONT_FAMILY, size)
    f.setBold(bold)
    return f


def style_quick_link_button(btn: PushButton) -> None:
    """为"快速连词/取消连词"按钮应用主题感知配色。

    沿用 timing_interface 的 setCustomStyleSheet 方案：同时传入浅色 / 深色两套
    QSS，qfluentwidgets 在主题切换时自动选用对应版本，无需手动监听 theme.changed。
    用 accent_secondary（蓝）作为底色，使其与普通"执行/关闭"按钮区分开。
    """
    name = btn.objectName() or "btnQuickLink"
    btn.setObjectName(name)

    def make_qss(tc: ThemeColors) -> str:
        bg = tc.accent_secondary
        lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        fg = "#1a1a1a" if lum > 150 else "#ffffff"
        return (
            f"#{name} {{ background-color: {bg.name()}; color: {fg}; border: none; }}"
            f" #{name}:hover {{ background-color: {bg.lighter(115).name()}; color: {fg}; }}"
            f" #{name}:pressed {{ background-color: {bg.darker(110).name()}; color: {fg}; }}"
            f" #{name}:disabled {{ background-color: {tc.bg_hover.name()};"
            f" color: {tc.text_disabled.name()}; }}"
        )

    setCustomStyleSheet(
        btn, make_qss(ThemeColors(is_dark=False)), make_qss(ThemeColors(is_dark=True))
    )


def parse_ruby_text(raw: str, check_count: int = 1) -> Optional[Ruby]:
    """解析 ruby 文本，根据 check_count 自动分段

    规则：
    1. 直接应用：用逗号手动分段，无逗号则不分段
    2. 按字符均分：始终按字符拆分，忽略逗号
    3. 按 mora 均分：始终按 mora 拆分，忽略逗号
    4. 当分段数 > check_count 时，多余部分合到末段

    Args:
        raw: 用户输入的注音文本
        check_count: 节奏点数量

    Returns:
        Ruby 对象，或 None（无注音时）
    """
    text = raw.strip()
    if not text:
        return None

    # 获取用户选择的分段方式
    mode = _get_ruby_split_mode()

    if mode == "direct":
        # 直接应用：用逗号手动分段，无逗号则不分段
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if not parts:
            return None
        return Ruby(parts=[RubyPart(text=p) for p in parts if p])
    elif mode == "char":
        clean_text = text.replace(",", "")
        if not clean_text:
            return None
        if check_count <= 1:
            return Ruby(parts=[RubyPart(text=clean_text)])
        parts = distribute_ruby_chars_evenly(list(clean_text), check_count)
        return Ruby(parts=[RubyPart(text=p) for p in parts if p])
    else:
        # 按 mora 均分（始终按 mora 拆分，忽略逗号）
        from strange_uta_game.backend.infrastructure.parsers.inline_format import (
            split_ruby_for_checkpoints,
        )
        clean_text = text.replace(",", "")
        if not clean_text:
            return None
        if check_count <= 1:
            return Ruby(parts=[RubyPart(text=clean_text)])
        parts = split_ruby_for_checkpoints(clean_text, check_count)
        return Ruby(parts=[RubyPart(text=p) for p in parts if p])


class ModifyCharacterDialog(QDialog):
    """修改所选字符对话框 — 替换选中区间的文本、注音、节奏点、连词。

    字符级独立输入框方案（批 18 #1/#2/#3）：
      - 顶部"新字符"文本框决定字符序列
      - 下方按新文本长度动态生成每字符一行：[字符] [注音] [节奏点] [向后连词]
      - 注音框内用半角逗号分隔 RubyPart（如 わ,た,し → 3 个 RubyPart）
      - 文本修改时自动重建字符行，并按位置尽量保留已输入值
      - 单字符修改时直接原地 set_ruby/check_count/linked_to_next/push_to_ruby，保留 timestamps
      - 字符数变化时才走替换 slice 流程（必然丢旧 timestamps）
      - 连词校验：末字/行尾字符禁止 linked_to_next=True（句尾=语气停顿点，允许连词），
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
        self.resize(*CHAR_DIALOG_SIZE)
        self.setFont(char_dialog_font(FONT_DIALOG_BASE))

        layout = QVBoxLayout(self)

        # 原字符显示 + 新字符输入
        chars = sentence.characters[start_idx : end_idx + 1]
        current_text = "".join(c.char for c in chars)

        top_form = QFormLayout()
        lbl_current = StrongBodyLabel(current_text)
        lbl_current.setFont(char_dialog_font(FONT_VALUE_DISPLAY, bold=True))
        lbl_cur_label = BodyLabel("当前选中字符:")
        lbl_cur_label.setFont(char_dialog_font(FONT_FIELD_LABEL))
        top_form.addRow(lbl_cur_label, lbl_current)
        self.edit_new_chars = LineEdit(self)
        self.edit_new_chars.setText(current_text)
        self.edit_new_chars.setPlaceholderText("输入新字符")
        self.edit_new_chars.setFont(char_dialog_font(FONT_MAIN_INPUT))
        lbl_new_label = BodyLabel("新字符:")
        lbl_new_label.setFont(char_dialog_font(FONT_FIELD_LABEL))
        top_form.addRow(lbl_new_label, self.edit_new_chars)
        layout.addLayout(top_form)

        # 字符级编辑区标题
        hint = CaptionLabel("按字符编辑（注音用半角逗号分隔 RubyPart；节奏点为非负整数）:")
        layout.addWidget(hint)

        # Scroll area with per-char rows
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
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

        # 注册词典 + 快速连词
        register_row = QHBoxLayout()
        self.chk_register = CheckBox("将此词注册到读音词典")
        register_row.addWidget(self.chk_register)
        register_row.addStretch()
        self.btn_toggle_linked = PushButton("快速连词/取消连词", self)
        self.btn_toggle_linked.setToolTip(
            "若全部未连词，则将除最后一个字符外的向后连词全部勾选；否则全部取消连词"
        )
        self.btn_toggle_linked.clicked.connect(self._on_toggle_all_linked)
        style_quick_link_button(self.btn_toggle_linked)
        register_row.addWidget(self.btn_toggle_linked)
        layout.addLayout(register_row)

        # 注音分段方式选择
        self._radio_direct, self._radio_by_char, self._radio_by_mora, ruby_split_group = _create_ruby_split_group(self)
        layout.addWidget(ruby_split_group)

        # 预览区域
        self.preview_label = CaptionLabel("预览: ")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        # 连接信号更新预览
        self.edit_new_chars.textChanged.connect(self._update_preview)
        self._radio_direct.toggled.connect(self._update_preview)
        self._radio_by_char.toggled.connect(self._update_preview)
        self._radio_by_mora.toggled.connect(self._update_preview)

        # 初始预览
        self._update_preview()
        self._update_toggle_linked_enabled()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_exec = PrimaryPushButton("执行", self)
        btn_exec.clicked.connect(self._on_execute)
        btn_layout.addWidget(btn_exec)
        btn_query = PushButton("查询候补字典", self)
        btn_query.clicked.connect(self._on_query_dict_candidates)
        btn_layout.addWidget(btn_query)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _on_query_dict_candidates(self):
        """查询候补字典：选中条目后按其格式填充并执行（关闭两窗口）。"""
        from strange_uta_game.frontend.editor.timing.dict_candidate_dialog import (
            DictCandidateDialog,
            apply_entry_to_dialog_rows,
        )

        word = self.edit_new_chars.text().strip()
        dlg = DictCandidateDialog(word, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        entry = dlg.get_selected_entry()
        if not entry:
            return
        if apply_entry_to_dialog_rows(self, entry["word"], entry["reading"]):
            # 执行原对话框的应用逻辑（_on_execute 内部会 accept() 关闭本窗口）
            self._on_execute()

    def _append_char_row(
        self, char_str: str, ruby_str: str, check_str: str, linked: bool = False
    ):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        lbl = StrongBodyLabel(char_str)
        lbl.setFixedWidth(32)
        lbl.setFont(char_dialog_font(FONT_CHAR_GLYPH, bold=True))
        edit_ruby = LineEdit(row_widget)
        edit_ruby.setText(ruby_str)
        edit_ruby.setPlaceholderText("注音（逗号分隔多 RubyPart）")
        edit_ruby.setFont(char_dialog_font(FONT_ROW_INPUT))
        edit_check = LineEdit(row_widget)
        edit_check.setText(check_str)
        edit_check.setPlaceholderText("节奏点")
        edit_check.setFixedWidth(64)
        edit_check.setFont(char_dialog_font(FONT_ROW_INPUT))
        chk_linked = CheckBox("向后连词")
        chk_linked.setChecked(bool(linked))
        chk_linked.setToolTip(
            "连接到下一字符（末字/行尾不可连词，提交时将跳过并提示；句尾=停顿点，允许连词）"
        )
        # 监控用户手动编辑
        edit_ruby.textEdited.connect(self._on_row_user_edited)
        edit_check.textEdited.connect(self._on_row_user_edited)
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
        # 更新预览
        self._update_preview()
        self._update_toggle_linked_enabled()

    def _on_row_user_edited(self, _text: str):
        """用户手动编辑行时更新预览"""
        self._update_preview()

    def _update_toggle_linked_enabled(self):
        """字符数 <= 1 时禁用快速连词按钮。"""
        self.btn_toggle_linked.setEnabled(len(self._char_rows) > 1)

    def _on_toggle_all_linked(self):
        """快速连词/取消连词：全部未连词→除末字外全部连词；否则→全部取消连词。"""
        rows = self._char_rows
        if len(rows) <= 1:
            return
        all_unlinked = all(not row[3].isChecked() for row in rows)
        for i, row in enumerate(rows):
            row[3].setChecked(all_unlinked and i < len(rows) - 1)
        self._update_preview()

    def _update_preview(self):
        """更新预览区域"""
        preview_items = []
        for _, edit_ruby, edit_check, _ in self._char_rows:
            ruby_text = edit_ruby.text().strip()
            try:
                check_count = max(1, int(edit_check.text().strip()))
            except ValueError:
                check_count = 1

            if ruby_text:
                # 获取当前选择的分段方式
                if self._radio_direct.isChecked():
                    mode = "direct"
                elif self._radio_by_char.isChecked():
                    mode = "char"
                else:
                    mode = "mora"

                # 根据分段方式解析注音
                if mode == "direct":
                    # 直接应用：用逗号手动分段，无逗号则不分段
                    parts = [p.strip() for p in ruby_text.split(",") if p.strip()]
                elif mode == "char":
                    clean_text = ruby_text.replace(",", "")
                    parts = distribute_ruby_chars_evenly(list(clean_text), check_count)
                else:
                    # 按 mora 均分（始终按 mora 拆分，忽略逗号）
                    from strange_uta_game.backend.infrastructure.parsers.inline_format import (
                        split_ruby_for_checkpoints,
                    )
                    clean_text = ruby_text.replace(",", "")
                    parts = split_ruby_for_checkpoints(clean_text, check_count)

                preview_items.append(f"[{','.join(parts)}]")
            else:
                preview_items.append("[]")

        self.preview_label.setText(f"预览: {' '.join(preview_items)}")

    def _parse_ruby(self, raw: str, check_count: int = 1):
        """解析 ruby 文本，根据 check_count 自动分段"""
        return parse_ruby_text(raw, check_count)

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
            try:
                check_count = max(0, int(edit_check.text().strip()))
            except ValueError:
                check_count = 1
            per_char_check.append(check_count)
            per_char_ruby.append(self._parse_ruby(edit_ruby.text(), check_count))
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
                # linked_to_next 校验：末字/行尾禁止连词（句尾=语气停顿点，可以连词）
                req_linked = per_char_linked_req[i]
                abs_idx = self._start_idx + i
                sentence_len = len(self._sentence.characters)
                is_last_in_sentence = abs_idx >= sentence_len - 1
                if req_linked and (
                    is_last_in_sentence
                    or tgt.is_line_end
                ):
                    reason = "最后一个字符" if is_last_in_sentence else "行尾"
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
                    or new_ch.is_line_end
                ):
                    reason = "最后一个字符" if is_last_in_sentence else "行尾"
                    self._linked_failures.append((abs_idx, new_ch.char, reason))
                    new_ch.linked_to_next = False
                else:
                    new_ch.linked_to_next = req_linked
            self._sentence.characters[self._start_idx : self._end_idx + 1] = new_chars

        # 词典注册：传 Ruby 对象列表 + 连词信息，完整保留用户设定
        if self.chk_register.isChecked():
            self._register_to_dictionary(new_text, per_char_ruby, per_char_linked_req)

        # 保存注音分段方式配置
        _save_ruby_split_mode(self._radio_direct, self._radio_by_char, self._radio_by_mora)

        self._modified = True
        self.accept()

    def get_linked_failures(self) -> list[tuple[int, str, str]]:
        """返回应用连词时因末字/行尾被跳过的项列表（abs_idx, char, reason）。"""
        return list(self._linked_failures)

    def _register_to_dictionary(self, word: str, per_char_ruby: list, per_char_linked: list | None = None):
        """将词注册到用户词典，完整保留用户设定的 Ruby parts（mora）与连词信息。"""
        try:
            from strange_uta_game.frontend.settings.settings_interface import (
                AppSettings,
            )
            from strange_uta_game.frontend.settings.app_settings import (
                build_annotated_reading,
            )

            reading = build_annotated_reading(word, per_char_ruby, per_char_linked)
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

        # 从 AppSettings 读取记忆的设置
        from strange_uta_game.frontend.settings.settings_interface import AppSettings
        settings = AppSettings()
        saved_symbol = settings.get("timing.guide_symbol", "")
        saved_count = settings.get("timing.guide_count", 1)
        saved_duration = settings.get("timing.guide_duration_ms", 1000)

        saved_reverse = settings.get("timing.guide_reverse", False)
        saved_fill_gap = settings.get("timing.guide_fill_gap", False)

        self.setWindowTitle("插入导唱符")
        self.resize(400, 320)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Field 1: Current selected char (readonly)
        ch = sentence.characters[char_idx]
        lbl_current = QLabel(ch.char)
        lbl_current.setStyleSheet("font-size: 16px; font-weight: bold;")
        form.addRow("当前选中字符:", lbl_current)

        # Field 2: Guide symbol text
        self.edit_symbol = QLineEdit(saved_symbol)
        self.edit_symbol.setPlaceholderText("请填写要插入的导唱符")
        form.addRow("导唱符:", self.edit_symbol)

        # Field 3: Count
        self.edit_count = QLineEdit(str(saved_count))
        self.edit_count.setPlaceholderText("个数")
        form.addRow("个数:", self.edit_count)

        # Field 4: Duration per symbol
        self.edit_duration = QLineEdit(str(saved_duration))
        self.edit_duration.setPlaceholderText("每个导唱符持续时间（毫秒）")
        form.addRow("持续时间 (ms):", self.edit_duration)

        # Field 5: Fill gap — 补足间隔时间
        # 勾选后忽略手动持续时间，自动在「前一个时间戳」与「本字符首个时间戳」
        # 之间平均分配，前一个时间戳搜索不到时以 0ms（歌曲开始）为起点。
        self.chk_fill_gap = QCheckBox("补足间隔时间")
        self.chk_fill_gap.setChecked(bool(saved_fill_gap))
        self.chk_fill_gap.toggled.connect(self._on_fill_gap_toggled)
        form.addRow("", self.chk_fill_gap)

        # Field 6: Reverse timestamp order
        self.chk_reverse = QCheckBox("时间戳反向")
        self.chk_reverse.setChecked(bool(saved_reverse))
        form.addRow("", self.chk_reverse)

        # 初始化持续时间输入框可用状态
        self._on_fill_gap_toggled(self.chk_fill_gap.isChecked())

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

    def _on_fill_gap_toggled(self, checked: bool):
        """勾选「补足间隔时间」时禁用手动持续时间输入框"""
        self.edit_duration.setEnabled(not checked)

    def _find_prev_timestamp(self) -> int:
        """向前搜索最近的一个时间戳（含句尾时间戳）作为时间起点。

        从 char_idx-1 往前逐字符查找，取最近一个有时间戳字符的最大时间戳。
        搜索不到时返回 0（歌曲开始处 00:00:00）。
        """
        for i in range(self._char_idx - 1, -1, -1):
            ts_list = self._sentence.characters[i].all_timestamps
            if ts_list:
                return max(ts_list)
        return 0

    def _on_execute(self):
        from strange_uta_game.backend.domain.models import Character

        # 不再过滤 Unicode 变体选择符（U+FE00-U+FE0F）等非用户主动输入的字符
        # 例如 ☢︎ 会被解析为 ☢ + VS15(0xFE0E)，不需要过滤掉变体选择符
        symbol = self.edit_symbol.text().strip()
        if not symbol:
            return

        try:
            count = max(1, int(self.edit_count.text().strip()))
        except ValueError:
            count = 1

        reverse = self.chk_reverse.isChecked()
        fill_gap = self.chk_fill_gap.isChecked()

        # Get reference char's timestamp and singer
        ref_char = self._sentence.characters[self._char_idx]
        singer_id = ref_char.singer_id

        # Get reference timestamp (first timestamp of selected char)
        ref_ts = ref_char.timestamps[0] if ref_char.timestamps else None

        if fill_gap:
            # 补足间隔时间：起点 = 向前最近的时间戳（搜索不到则 0），
            # 终点 = 本字符首个时间戳，按个数平均分配。
            if ref_ts is None:
                InfoBar.warning(
                    title="无法补足间隔时间",
                    content="当前字符没有时间戳，无法确定间隔终点。",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
                return
            start_ts = self._find_prev_timestamp()
            if start_ts >= ref_ts:
                InfoBar.warning(
                    title="无法补足间隔时间",
                    content=f"起点 {start_ts}ms 不早于终点 {ref_ts}ms，间隔无效。",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
                return
            duration_ms = (ref_ts - start_ts) // count
        else:
            try:
                duration_ms = max(100, int(self.edit_duration.text().strip()))
            except ValueError:
                duration_ms = 1000

        # 保存设置到 AppSettings
        from strange_uta_game.frontend.settings.settings_interface import AppSettings
        settings = AppSettings()
        settings.set("timing.guide_symbol", symbol)
        settings.set("timing.guide_count", count)
        if not fill_gap:
            settings.set("timing.guide_duration_ms", duration_ms)
        settings.set("timing.guide_reverse", reverse)
        settings.set("timing.guide_fill_gap", fill_gap)
        settings.save()

        # Build guide characters
        # Each guide symbol has linked_to_next=True (they chain), except last
        # Actually: if symbol is multi-char, each char of the symbol is linked.
        # If count > 1, each "symbol group" is also linked.
        # Result: all guide chars are linked_to_next=True (chained as one word)
        #
        # Timestamp is assigned to the FIRST character of each symbol group.
        # For symbol "ABCD": "A" gets check_count=1 and timestamp, "B"/"C"/"D" get check_count=0.
        guide_chars = []
        for i in range(count):
            for j, ch_str in enumerate(symbol):
                is_first_of_symbol = j == 0
                is_last_symbol = i == count - 1
                is_last_char_of_last_symbol = (j == len(symbol) - 1) and is_last_symbol
                new_ch = Character(
                    char=ch_str,
                    ruby=None,
                    check_count=1 if is_first_of_symbol else 0,
                    singer_id=singer_id,
                    linked_to_next=not is_last_char_of_last_symbol,
                )
                # Set timestamp to the first character of each symbol group
                if ref_ts is not None and is_first_of_symbol:
                    if reverse:
                        ts = ref_ts - duration_ms * (i + 1)
                    else:
                        ts = ref_ts - duration_ms * (count - i)
                    if ts < 0:
                        InfoBar.warning(
                            title="时间戳越界",
                            content=f"导唱符时间戳 {ts}ms 小于0，已自动设为0ms",
                            orient=Qt.Orientation.Horizontal,
                            isClosable=True,
                            position=InfoBarPosition.TOP,
                            duration=3000,
                            parent=self,
                        )
                        ts = 0
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
    """注音编辑对话框 — 支持连词（Ruby 合并/拆分）和 CheckCount 编辑

    与 ModifyCharacterDialog 类似，但用于 F2 快捷键触发：
    - 直接获取对应字符的整个连词情况
    - 支持全文件替换功能

    UI 布局：
    - 当前字符显示（只读）
    - 新字符输入
    - 每字符一行：[字符] [注音] [节奏点] [向后连词]
    - 处理方式选择（直接应用/按字符均分/按 mora 均分）
    - 预览区域
    - 全文件替换选项
    - 确定/取消按钮
    """

    def __init__(self, sentence: "Sentence", char_idx: int, parent=None):
        super().__init__(parent)
        self._sentence = sentence
        self._char_idx = char_idx
        self._modified = False
        self._char_rows: list[tuple[QLabel, QLineEdit, QLineEdit, QCheckBox]] = []

        self.setWindowTitle("编辑字符")
        self.resize(*CHAR_DIALOG_SIZE)
        self.setFont(char_dialog_font(FONT_DIALOG_BASE))

        layout = QVBoxLayout(self)

        # 当前字符（只读）— 显示连词组内所有字符
        ch = sentence.characters[char_idx]
        # 查找连词组范围
        word_start, word_end = sentence.get_word_char_range(char_idx)
        word_len = word_end - word_start

        if word_len > 1:
            display = " + ".join(
                sentence.characters[i].char for i in range(word_start, word_end)
            )
        else:
            display = ch.char

        top_form = QFormLayout()
        lbl_current = StrongBodyLabel(display)
        lbl_current.setFont(char_dialog_font(FONT_VALUE_DISPLAY, bold=True))
        lbl_cur_label = BodyLabel("当前字符:")
        lbl_cur_label.setFont(char_dialog_font(FONT_FIELD_LABEL))
        top_form.addRow(lbl_cur_label, lbl_current)
        # 新字符输入框只包含字符本身，不包含 " + "
        self.edit_new_chars = LineEdit(self)
        self.edit_new_chars.setText("".join(
            sentence.characters[i].char for i in range(word_start, word_end)
        ))
        self.edit_new_chars.setPlaceholderText("输入新字符")
        self.edit_new_chars.setFont(char_dialog_font(FONT_MAIN_INPUT))
        lbl_new_label = BodyLabel("新字符:")
        lbl_new_label.setFont(char_dialog_font(FONT_FIELD_LABEL))
        top_form.addRow(lbl_new_label, self.edit_new_chars)
        layout.addLayout(top_form)

        # 字符级编辑区标题
        hint = CaptionLabel("按字符编辑（注音用半角逗号分隔 RubyPart；节奏点为非负整数）:")
        layout.addWidget(hint)

        # Scroll area with per-char rows
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(4)
        scroll.setWidget(self._rows_container)
        layout.addWidget(scroll, stretch=1)

        # 初始按当前字符填充
        for i in range(word_start, word_end):
            c = sentence.characters[i]
            ruby_str = (
                ",".join(p.text for p in c.ruby.parts) if c.ruby and c.ruby.parts else ""
            )
            self._append_char_row(c.char, ruby_str, str(c.check_count), c.linked_to_next)

        # 文本变更 → 重建行，保留已输入值
        self.edit_new_chars.textChanged.connect(self._rebuild_rows_on_text_change)

        # 注册词典 + 快速连词
        register_row = QHBoxLayout()
        self.chk_register = CheckBox("将此词注册到读音词典")
        register_row.addWidget(self.chk_register)
        register_row.addStretch()
        self.btn_toggle_linked = PushButton("快速连词/取消连词", self)
        self.btn_toggle_linked.setToolTip(
            "若全部未连词，则将除最后一个字符外的向后连词全部勾选；否则全部取消连词"
        )
        self.btn_toggle_linked.clicked.connect(self._on_toggle_all_linked)
        style_quick_link_button(self.btn_toggle_linked)
        register_row.addWidget(self.btn_toggle_linked)
        layout.addLayout(register_row)

        # 注音分段方式选择
        self._radio_direct, self._radio_by_char, self._radio_by_mora, ruby_split_group = _create_ruby_split_group(self)
        layout.addWidget(ruby_split_group)

        # 预览区域
        self.preview_label = CaptionLabel("预览: ")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        # 连接信号更新预览
        self.edit_new_chars.textChanged.connect(self._update_preview)
        self._radio_direct.toggled.connect(self._update_preview)
        self._radio_by_char.toggled.connect(self._update_preview)
        self._radio_by_mora.toggled.connect(self._update_preview)

        self._word_start = word_start
        self._word_end = word_end

        # 初始预览
        self._update_preview()
        self._update_toggle_linked_enabled()

        # 按钮
        btn_layout = QHBoxLayout()
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self._on_accept)
        btn_query = PushButton("查询候补字典", self)
        btn_query.clicked.connect(self._on_query_dict_candidates)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_query)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def _on_query_dict_candidates(self):
        """查询候补字典：选中条目后按其格式填充并执行（关闭两窗口）。"""
        from strange_uta_game.frontend.editor.timing.dict_candidate_dialog import (
            DictCandidateDialog,
            apply_entry_to_dialog_rows,
        )

        word = self.edit_new_chars.text().strip()
        dlg = DictCandidateDialog(word, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        entry = dlg.get_selected_entry()
        if not entry:
            return
        if apply_entry_to_dialog_rows(self, entry["word"], entry["reading"]):
            # _on_accept 内部会 accept() 关闭本窗口
            self._on_accept()

    def _append_char_row(
        self, char_str: str, ruby_str: str, check_str: str, linked: bool = False
    ):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        lbl = StrongBodyLabel(char_str)
        lbl.setFixedWidth(32)
        lbl.setFont(char_dialog_font(FONT_CHAR_GLYPH, bold=True))
        edit_ruby = LineEdit(row_widget)
        edit_ruby.setText(ruby_str)
        edit_ruby.setPlaceholderText("注音（逗号分隔多 RubyPart）")
        edit_ruby.setFont(char_dialog_font(FONT_ROW_INPUT))
        edit_check = LineEdit(row_widget)
        edit_check.setText(check_str)
        edit_check.setPlaceholderText("节奏点")
        edit_check.setFixedWidth(64)
        edit_check.setFont(char_dialog_font(FONT_ROW_INPUT))
        chk_linked = CheckBox("向后连词")
        chk_linked.setChecked(bool(linked))
        chk_linked.setToolTip(
            "连接到下一字符（末字/行尾不可连词，提交时将跳过并提示；句尾=停顿点，允许连词）"
        )
        # 监控用户手动编辑
        edit_ruby.textEdited.connect(self._on_row_user_edited)
        edit_check.textEdited.connect(self._on_row_user_edited)
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
        # 更新预览
        self._update_preview()
        self._update_toggle_linked_enabled()

    def _on_row_user_edited(self, _text: str):
        """用户手动编辑行时更新预览"""
        self._update_preview()

    def _update_toggle_linked_enabled(self):
        """字符数 <= 1 时禁用快速连词按钮。"""
        self.btn_toggle_linked.setEnabled(len(self._char_rows) > 1)

    def _on_toggle_all_linked(self):
        """快速连词/取消连词：全部未连词→除末字外全部连词；否则→全部取消连词。"""
        rows = self._char_rows
        if len(rows) <= 1:
            return
        all_unlinked = all(not row[3].isChecked() for row in rows)
        for i, row in enumerate(rows):
            row[3].setChecked(all_unlinked and i < len(rows) - 1)
        self._update_preview()

    def _update_preview(self):
        """更新预览区域"""
        preview_items = []
        for _, edit_ruby, edit_check, _ in self._char_rows:
            ruby_text = edit_ruby.text().strip()
            try:
                check_count = max(1, int(edit_check.text().strip()))
            except ValueError:
                check_count = 1

            if ruby_text:
                # 获取当前选择的分段方式
                if self._radio_direct.isChecked():
                    mode = "direct"
                elif self._radio_by_char.isChecked():
                    mode = "char"
                else:
                    mode = "mora"

                # 根据分段方式解析注音
                if mode == "direct":
                    # 直接应用：用逗号手动分段，无逗号则不分段
                    parts = [p.strip() for p in ruby_text.split(",") if p.strip()]
                elif mode == "char":
                    clean_text = ruby_text.replace(",", "")
                    parts = distribute_ruby_chars_evenly(list(clean_text), check_count)
                else:
                    # 按 mora 均分（始终按 mora 拆分，忽略逗号）
                    from strange_uta_game.backend.infrastructure.parsers.inline_format import (
                        split_ruby_for_checkpoints,
                    )
                    clean_text = ruby_text.replace(",", "")
                    parts = split_ruby_for_checkpoints(clean_text, check_count)

                preview_items.append(f"[{','.join(parts)}]")
            else:
                preview_items.append("[]")

        self.preview_label.setText(f"预览: {' '.join(preview_items)}")

    def _on_accept(self):
        new_text = self.edit_new_chars.text().strip()
        word_len = self._word_end - self._word_start

        if not new_text:
            self.accept()
            return

        # 收集每行值：ruby / check_count / linked_to_next
        per_char_ruby = []
        per_char_check = []
        per_char_linked_req = []
        for i in range(len(new_text)):
            if i >= len(self._char_rows):
                per_char_ruby.append(None)
                per_char_check.append(1)
                per_char_linked_req.append(False)
                continue
            _, edit_ruby, edit_check, chk_linked = self._char_rows[i]
            try:
                check_count = max(0, int(edit_check.text().strip()))
            except ValueError:
                check_count = 1
            per_char_check.append(check_count)
            per_char_ruby.append(parse_ruby_text(edit_ruby.text(), check_count))
            per_char_linked_req.append(bool(chk_linked.isChecked()))

        # 应用到当前连词组
        old_chars = [self._sentence.characters[i] for i in range(self._word_start, self._word_end)]

        if len(new_text) == len(old_chars):
            # 字符数不变 → 原地修改
            for i, ch_str in enumerate(new_text):
                tgt = old_chars[i]
                tgt.char = ch_str
                tgt.set_ruby(per_char_ruby[i])
                tgt.set_check_count(per_char_check[i], force=True)
                tgt.push_to_ruby()
                tgt.linked_to_next = per_char_linked_req[i]
        else:
            # 字符数变化 → 替换 slice
            singer_id = old_chars[0].singer_id if old_chars else ""
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
            self._sentence.characters[self._word_start:self._word_end] = new_chars

        self._modified = True

        # 词典注册：传 Ruby 对象列表 + 连词信息，完整保留用户设定
        if self.chk_register.isChecked():
            self._register_to_dictionary(new_text, per_char_ruby, per_char_linked_req)

        # 保存注音分段方式配置
        _save_ruby_split_mode(self._radio_direct, self._radio_by_char, self._radio_by_mora)

        self.accept()

    def was_modified(self) -> bool:
        return self._modified

    def _register_to_dictionary(self, word: str, per_char_ruby: list, per_char_linked: list | None = None):
        """将词注册到用户词典，完整保留用户设定的 Ruby parts（mora）与连词信息。"""
        try:
            from strange_uta_game.frontend.settings.settings_interface import (
                AppSettings,
            )
            from strange_uta_game.frontend.settings.app_settings import (
                build_annotated_reading,
            )

            reading = build_annotated_reading(word, per_char_ruby, per_char_linked)
            AppSettings().register_dictionary_word(word, reading)
        except Exception:
            pass


# ──────────────────────────────────────────────
# 按行设置演唱者对话框
# ──────────────────────────────────────────────


class SetSingerByLineDialog(QDialog):
    """按行设置演唱者对话框 — 批量为多行设置演唱者。

    显示所有行（只读），用户可通过复选框选择多行，
    然后从下拉列表中选择演唱者来批量设置。
    点击"应用"按钮后不关闭对话框，方便继续设置其他行。
    """

    apply_requested = pyqtSignal(dict)  # {line_idx: singer_id}

    def __init__(self, sentences: list[Sentence], singers: list[Singer], parent=None,
                 focus_line_idx: int = -1):
        super().__init__(parent)
        self._sentences = sentences
        self._singers = singers
        self._modified = False
        self._focus_line_idx = focus_line_idx

        # 构建 singer_id -> Singer 映射
        self._singer_map = {s.id: s for s in singers}

        self.setWindowTitle("按行设置演唱者")
        self.resize(1200, 900)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)

        # 提示标签
        hint = CaptionLabel("选择要设置演唱者的行，然后从下方选择演唱者，点击「应用」执行：")
        layout.addWidget(hint)

        # 行列表表格
        self.table = QTableWidget(len(sentences), 4, self)
        self.table.setHorizontalHeaderLabels(["选择", "行号", "歌词内容", "当前演唱者"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 60)
        self.table.setColumnWidth(2, 500)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        for idx, sentence in enumerate(sentences):
            # 复选框
            chk = QCheckBox()
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(idx, 0, chk_widget)

            # 行号
            line_num_item = QTableWidgetItem(str(idx + 1))
            line_num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(idx, 1, line_num_item)

            # 歌词内容（只读）
            text = sentence.text if sentence.characters else "(空行)"
            text_item = QTableWidgetItem(text)
            self.table.setItem(idx, 2, text_item)

            # 当前演唱者（只读）- 显示行内所有不同的演唱者
            singer_names = self._get_singer_names_for_sentence(sentence)
            singer_item = QTableWidgetItem(singer_names)
            singer_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(idx, 3, singer_item)

        layout.addWidget(self.table, stretch=1)

        if 0 <= self._focus_line_idx < len(sentences):
            focus_item = self.table.item(self._focus_line_idx, 2)
            if focus_item:
                focus_bg = theme.bg_selected
                for col in range(self.table.columnCount()):
                    item = self.table.item(self._focus_line_idx, col)
                    if item:
                        item.setBackground(focus_bg)
                self.table.scrollToItem(focus_item, QTableWidget.ScrollHint.PositionAtCenter)

        # 全选/全不选按钮
        select_layout = QHBoxLayout()
        btn_select_all = PushButton("全选", self)
        btn_select_all.clicked.connect(self._select_all)
        btn_deselect_all = PushButton("全不选", self)
        btn_deselect_all.clicked.connect(self._deselect_all)
        select_layout.addWidget(btn_select_all)
        select_layout.addWidget(btn_deselect_all)
        select_layout.addStretch()
        layout.addLayout(select_layout)

        # 演唱者选择区（名称过滤 + 分组过滤 + 列表预览）
        singer_group = QGroupBox("设置演唱者为", self)
        sg_layout = QVBoxLayout(singer_group)
        sg_layout.setSpacing(4)

        # 过滤行
        sg_filter_row = QHBoxLayout()
        sg_filter_row.addWidget(QLabel("名称:"))
        self.singer_name_filter = QLineEdit(self)
        self.singer_name_filter.setPlaceholderText("过滤名称...")
        self.singer_name_filter.textChanged.connect(self._apply_singer_filter)
        sg_filter_row.addWidget(self.singer_name_filter, stretch=1)

        sg_filter_row.addWidget(QLabel("分组:"))
        self.singer_group_filter = QComboBox(self)
        self.singer_group_filter.addItem("全部", "")
        _groups = sorted({s.group for s in singers if s.group})
        if any(not s.group for s in singers):
            self.singer_group_filter.addItem("（无分组）", "\x00nogroup")
        for _g in _groups:
            self.singer_group_filter.addItem(_g, _g)
        self.singer_group_filter.currentIndexChanged.connect(self._apply_singer_filter)
        sg_filter_row.addWidget(self.singer_group_filter)
        sg_layout.addLayout(sg_filter_row)

        # 演唱者列表（带颜色图标预览）
        self.singer_list = QListWidget(self)
        self.singer_list.setFixedHeight(160)
        self.singer_list.setIconSize(QSize(36, 18))
        for singer in singers:
            item = QListWidgetItem()
            label = singer.name
            if singer.group:
                label += f"  [{singer.group}]"
            item.setText(label)
            item.setIcon(QIcon(_make_singer_color_pixmap(singer.get_all_colors(), 36, 18)))
            item.setData(Qt.ItemDataRole.UserRole, singer.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, singer.group)
            self.singer_list.addItem(item)
        sg_layout.addWidget(self.singer_list)

        layout.addWidget(singer_group)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_apply = PrimaryPushButton("应用", self)
        btn_apply.clicked.connect(self._on_apply)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_apply)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _get_singer_names_for_sentence(self, sentence: Sentence) -> str:
        """获取句子内所有不同的演唱者名称，用逗号分隔"""
        if not sentence.characters:
            return ""
        singer_ids = set()
        for ch in sentence.characters:
            if ch.singer_id:
                singer_ids.add(ch.singer_id)
        if not singer_ids:
            return ""
        names = []
        for sid in singer_ids:
            singer = self._singer_map.get(sid)
            names.append(singer.name if singer else "未知")
        return ", ".join(names)

    def _select_all(self):
        """全选所有行"""
        for idx in range(self.table.rowCount()):
            widget = self.table.cellWidget(idx, 0)
            if widget:
                chk = widget.findChild(QCheckBox)
                if chk:
                    chk.setChecked(True)

    def _deselect_all(self):
        """全不选"""
        for idx in range(self.table.rowCount()):
            widget = self.table.cellWidget(idx, 0)
            if widget:
                chk = widget.findChild(QCheckBox)
                if chk:
                    chk.setChecked(False)

    def _apply_singer_filter(self):
        """按名称/分组过滤演唱者列表"""
        name_text = self.singer_name_filter.text().strip().lower()
        group_val = self.singer_group_filter.currentData() or ""
        for i in range(self.singer_list.count()):
            item = self.singer_list.item(i)
            item_text = item.text().lower()
            item_group = item.data(Qt.ItemDataRole.UserRole + 1) or ""
            name_ok = not name_text or name_text in item_text
            if group_val == "\x00nogroup":
                group_ok = not item_group
            elif group_val:
                group_ok = item_group == group_val
            else:
                group_ok = True
            item.setHidden(not (name_ok and group_ok))

    def _on_apply(self):
        """应用按钮点击处理 - 不关闭对话框"""
        # 获取列表中当前选中的演唱者
        sel_items = self.singer_list.selectedItems()
        if not sel_items:
            return
        singer_id = sel_items[0].data(Qt.ItemDataRole.UserRole)
        if not singer_id:
            return

        # 收集选中的行
        selected_lines = []
        for idx in range(self.table.rowCount()):
            widget = self.table.cellWidget(idx, 0)
            if widget:
                chk = widget.findChild(QCheckBox)
                if chk and chk.isChecked():
                    selected_lines.append(idx)

        if not selected_lines:
            return

        # 构建结果映射并发出信号
        result_map = {line_idx: singer_id for line_idx in selected_lines}
        self._modified = True
        self.apply_requested.emit(result_map)

        # 更新表格中已应用行的当前演唱者显示
        singer = self._singer_map.get(singer_id)
        singer_name = singer.name if singer else "未知"
        for line_idx in selected_lines:
            item = self.table.item(line_idx, 3)
            if item:
                item.setText(singer_name)
                if singer:
                    item.setForeground(QColor(singer.color))

        # 取消已应用行的复选框选中状态
        for idx in selected_lines:
            widget = self.table.cellWidget(idx, 0)
            if widget:
                chk = widget.findChild(QCheckBox)
                if chk:
                    chk.setChecked(False)

    def was_modified(self) -> bool:
        return self._modified

    def result_map(self) -> dict[int, str]:
        """返回 {line_idx: singer_id} 映射"""
        return self._result_map


class ApplySingerDialog(QDialog):
    """应用演唱者对话框 — 为选中字符设置演唱者。

    显示当前选中字符内容、当前演唱者信息、过滤器和演唱者列表。
    用户可选择一个演唱者并应用到选中的字符。
    """

    apply_requested = pyqtSignal(str)  # singer_id

    def __init__(self, char_text: str, current_singers: list[Singer], all_singers: list[Singer], parent=None):
        super().__init__(parent)
        self._current_singers = current_singers
        self._all_singers = all_singers
        self._selected_singer_id = None

        self.setWindowTitle("应用演唱者")
        self.resize(400, 500)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)

        # 第一行：当前选中的字符内容（不可编辑）
        form = QFormLayout()
        lbl_char = QLabel(char_text)
        lbl_char.setStyleSheet("font-size: 16px; font-weight: bold;")
        form.addRow("选中字符:", lbl_char)

        # 第二行：当前演唱者信息
        if current_singers:
            singer_names = ", ".join(s.name for s in current_singers)
        else:
            singer_names = "无"
        lbl_current_singer = QLabel(singer_names)
        lbl_current_singer.setStyleSheet("font-size: 14px;")
        form.addRow("当前演唱者:", lbl_current_singer)
        layout.addLayout(form)

        # 第三行：过滤器
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("过滤:"))
        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("输入演唱者名称进行过滤")
        self.edit_filter.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.edit_filter, stretch=1)
        layout.addLayout(filter_layout)

        # 第四行：演唱者列表
        self.list_singers = QTableWidget(len(all_singers), 2, self)
        self.list_singers.setHorizontalHeaderLabels(["演唱者", "颜色预览"])
        self.list_singers.horizontalHeader().setStretchLastSection(True)
        self.list_singers.setColumnWidth(1, 64)
        self.list_singers.verticalHeader().setVisible(False)
        self.list_singers.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.list_singers.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.list_singers.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.list_singers.setIconSize(QSize(48, 20))
        self.list_singers.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_singers.cellDoubleClicked.connect(self._on_double_click)

        # 填充列表
        for idx, singer in enumerate(all_singers):
            name_item = QTableWidgetItem(singer.name)
            name_item.setData(Qt.ItemDataRole.UserRole, singer.id)
            self.list_singers.setItem(idx, 0, name_item)

            color_item = QTableWidgetItem()
            color_item.setIcon(QIcon(_make_singer_color_pixmap(singer.get_all_colors(), 48, 20)))
            color_item.setFlags(color_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.list_singers.setItem(idx, 1, color_item)

        layout.addWidget(self.list_singers, stretch=1)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_apply = PrimaryPushButton("应用", self)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_apply.setEnabled(False)
        btn_layout.addWidget(self.btn_apply)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def _on_filter_changed(self, text: str):
        """过滤器文本变化时更新列表显示"""
        filter_text = text.strip().lower()
        for row in range(self.list_singers.rowCount()):
            name_item = self.list_singers.item(row, 0)
            if name_item:
                singer_name = name_item.text().lower()
                self.list_singers.setRowHidden(row, filter_text not in singer_name)

    def _on_selection_changed(self):
        """列表选择变化时更新应用按钮状态"""
        selected_items = self.list_singers.selectedItems()
        if selected_items:
            row = selected_items[0].row()
            name_item = self.list_singers.item(row, 0)
            if name_item:
                self._selected_singer_id = name_item.data(Qt.ItemDataRole.UserRole)
                self.btn_apply.setEnabled(True)
                return
        self._selected_singer_id = None
        self.btn_apply.setEnabled(False)

    def _on_apply(self):
        """应用按钮点击处理"""
        if self._selected_singer_id:
            self.apply_requested.emit(self._selected_singer_id)
            self.accept()

    def _on_double_click(self, row: int, column: int):
        """双击列表项时直接应用"""
        name_item = self.list_singers.item(row, 0)
        if name_item:
            singer_id = name_item.data(Qt.ItemDataRole.UserRole)
            if singer_id:
                self.apply_requested.emit(singer_id)
                self.accept()

    def get_selected_singer_id(self) -> str:
        """返回选中的演唱者ID"""
        return self._selected_singer_id


class CompleteTimestampDialog(QDialog):
    """补全时间戳对话框 — 自动查找需要补轴点的字符并补全时间戳。

    本功能用于所有打轴过程完成后，自动查找需要补轴点的字符（仅无普通时间戳字符），
    查找前后时间戳取平均值均分给待补偿时间戳。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("补全时间戳")
        self.resize(480, 400)
        self.setFont(QFont("Microsoft YaHei", 10))
        self._apply_clicked = False

        # 读取配置
        try:
            from strange_uta_game.frontend.settings.app_settings import AppSettings
            settings = AppSettings()
            self._saved_scope_types = settings.get("complete_timestamp.scope_types", [
                "kanji", "hiragana", "katakana", "sokuon", "long_vowel", "chon", "chisai_kana"
            ])
            self._saved_exclude_rules = settings.get("complete_timestamp.exclude_rules", ["linked"])
            self._saved_head_offset_ms = settings.get("complete_timestamp.head_offset_ms", 150)
            self._saved_tail_offset_ms = settings.get("complete_timestamp.tail_offset_ms", 150)
        except Exception:
            self._saved_scope_types = ["kanji", "hiragana", "katakana", "sokuon", "long_vowel", "chon", "chisai_kana"]
            self._saved_exclude_rules = ["linked"]
            self._saved_head_offset_ms = 150
            self._saved_tail_offset_ms = 150

        layout = QVBoxLayout(self)

        # 第一行：功能说明
        desc_label = QLabel(
            "本功能用于所有打轴过程完成后，自动查找需要补轴点的字符（仅无普通时间戳字符），"
            "查找前后时间戳取平均值均分给待补偿时间戳。\n\n"
            "边界处理：行首无前方时间戳时，向后找到第一个时间戳并减去「行首扣除」值；"
            "行尾无后方时间戳时，向前找到第一个时间戳并加上「行尾增加」值。"
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("font-size: 12px; color: #555; padding: 8px;")
        layout.addWidget(desc_label)

        # 第二行：适用范围（多选）
        scope_group = QGroupBox("适用范围")
        scope_layout = QVBoxLayout(scope_group)

        self._scope_checkboxes: dict[str, QCheckBox] = {}
        scope_items = [
            ("kanji", "汉字"),
            ("hiragana", "平假名"),
            ("katakana", "片假名"),
            ("sokuon", "促音（っ/ッ）"),
            ("long_vowel", "长音符号"),
            ("chon", "拨音（ん/ン）"),
            ("chisai_kana", "捨仮名"),
            ("alphabet", "英文字母"),
            ("number", "数字"),
            ("symbol", "特殊符号"),
        ]

        for key, label in scope_items:
            chk = QCheckBox(label)
            chk.setChecked(key in self._saved_scope_types)
            self._scope_checkboxes[key] = chk
            scope_layout.addWidget(chk)

        layout.addWidget(scope_group)

        # 第三行：排除规则（多选）
        exclude_group = QGroupBox("排除规则")
        exclude_layout = QVBoxLayout(exclude_group)

        self._exclude_checkboxes: dict[str, QCheckBox] = {}
        exclude_items = [
            ("linked", "排除被连词字符"),
        ]

        for key, label in exclude_items:
            chk = QCheckBox(label)
            chk.setChecked(key in self._saved_exclude_rules)
            self._exclude_checkboxes[key] = chk
            exclude_layout.addWidget(chk)

        layout.addWidget(exclude_group)

        # 第四行：边界时间戳偏移设置
        offset_group = QGroupBox("边界时间戳偏移")
        offset_layout = QFormLayout(offset_group)

        self._edit_head_offset = QLineEdit(str(self._saved_head_offset_ms))
        self._edit_head_offset.setPlaceholderText("150")
        self._edit_head_offset.setToolTip(
            "行首字符无前方时间戳时，向后找到第一个时间戳后减去此值"
        )
        offset_layout.addRow("行首扣除时间戳 (ms):", self._edit_head_offset)

        self._edit_tail_offset = QLineEdit(str(self._saved_tail_offset_ms))
        self._edit_tail_offset.setPlaceholderText("150")
        self._edit_tail_offset.setToolTip(
            "行尾字符无后方时间戳时，向前找到第一个时间戳后加上此值"
        )
        offset_layout.addRow("行尾增加时间戳 (ms):", self._edit_tail_offset)

        layout.addWidget(offset_group)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_apply = PrimaryPushButton("应用", self)
        btn_apply.clicked.connect(self._on_apply)
        btn_layout.addWidget(btn_apply)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def _on_apply(self):
        """应用按钮点击处理"""
        self._apply_clicked = True
        # 保存适用范围、排除规则和边界偏移到配置
        scope_types = self.get_scope_types()
        exclude_rules = self.get_exclude_rules()
        head_offset = self.get_head_offset_ms()
        tail_offset = self.get_tail_offset_ms()
        try:
            from strange_uta_game.frontend.settings.app_settings import AppSettings
            settings = AppSettings()
            settings.set("complete_timestamp.scope_types", list(scope_types))
            settings.set("complete_timestamp.exclude_rules", exclude_rules)
            settings.set("complete_timestamp.head_offset_ms", head_offset)
            settings.set("complete_timestamp.tail_offset_ms", tail_offset)
            settings.save()
        except Exception:
            pass
        self.accept()

    def was_apply_clicked(self) -> bool:
        return self._apply_clicked

    def get_scope_types(self) -> set[str]:
        """返回选中的适用范围类型集合"""
        return {key for key, chk in self._scope_checkboxes.items() if chk.isChecked()}

    def get_exclude_rules(self) -> list[str]:
        """返回选中的排除规则列表"""
        return [key for key, chk in self._exclude_checkboxes.items() if chk.isChecked()]

    def get_head_offset_ms(self) -> int:
        """返回行首扣除时间戳值（毫秒）"""
        try:
            return int(self._edit_head_offset.text())
        except ValueError:
            return 150

    def get_tail_offset_ms(self) -> int:
        """返回行尾增加时间戳值（毫秒）"""
        try:
            return int(self._edit_tail_offset.text())
        except ValueError:
            return 150


class AdjustRawTimestampDialog(QDialog):
    """调整原始时间戳对话框 — 非模态，应用后不关闭，允许边测试边调整。

    每次点击「应用」即执行一次整体偏移，结果可叠加。
    窗口与主界面并存，用户可切换回主界面试听后继续调整。
    """

    apply_requested = pyqtSignal(int)  # delta_ms

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("调整原始时间戳")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(340, 200)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        desc = QLabel(
            "正数：所有原始时间戳向后移；负数：向前移。\n"
            "每次点击「应用」立即执行偏移，可叠加多次操作。"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        form = QFormLayout()
        self.spin_delta = SpinBox(self)
        self.spin_delta.setRange(-9999, 9999)
        self.spin_delta.setValue(0)
        self.spin_delta.setSuffix(" ms")
        form.addRow("偏移量:", self.spin_delta)
        layout.addLayout(form)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_apply = PrimaryPushButton("应用", self)
        self.btn_apply.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.btn_apply)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.close)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _on_apply(self):
        delta = self.spin_delta.value()
        if delta == 0:
            self.lbl_status.setText("偏移量为 0，未做任何修改")
            return
        self.apply_requested.emit(delta)

    def set_status(self, text: str, success: bool = True):
        color = "#2d7d46" if success else "#c0392b"
        self.lbl_status.setText(f'<span style="color:{color}">{text}</span>')


# ── 分离符号时间戳 — 符号分组定义 ──────────────────────────────────────────
# 每组：(key, 中文标签, 示例显示, 字符集)
_SEPARATE_SYM_GROUPS: list[tuple[str, str, str, frozenset]] = [
    ("parentheses",        "圆括号",       "( )（）",         frozenset("()（）")),
    ("brackets",           "方括号",       "[ ]【】",          frozenset("[]【】")),
    ("exclamation",        "感叹号",       "! ！",             frozenset("!！")),
    ("angle_brackets",     "尖括号",       "< >《》〈〉",      frozenset("<>《》〈〉")),
    ("ja_quotes",          "日文引号",     "「 」",            frozenset("「」")),
    ("ja_dbl_quotes",      "日文双引号",   "『 』",            frozenset("『』")),
    ("question",           "问号",         "? ？",             frozenset("?？")),
    ("ellipsis",           "省略号",       "… .",              frozenset({"…", "."})),
    ("period",             "句号",         "。",               frozenset("。")),
    ("comma",              "逗号",         ", ，",             frozenset(",，")),
    ("ideographic_comma",  "顿号",         "、",               frozenset("、")),
    ("semicolon",          "分号",         "; ；",             frozenset(";；")),
    ("colon",              "冒号",         ": ：",             frozenset(":：")),
    ("wave",               "波浪号",       "~ ～ 〜",          frozenset("~～〜")),
    ("dash",               "破折号",       "— ―",              frozenset("—―")),
    ("middle_dot",         "中点",         "· ・ •",           frozenset("·・•")),
    ("music_note",         "音符",         "♪ ♫ ♩ ♬",         frozenset("♪♫♩♬")),
    ("heart",              "爱心",         "♡ ♥",              frozenset("♡♥")),
    ("star",               "星形",         "★ ☆",              frozenset("★☆")),
]

# 默认选中分组（用户明确列出的常见符号）
_SEPARATE_SYM_DEFAULT: frozenset[str] = frozenset({
    "parentheses", "brackets", "exclamation", "angle_brackets",
    "ja_quotes", "question", "ellipsis", "period", "comma",
})


class SeparateSymbolTimestampDialog(QDialog):
    """分离符号时间戳对话框 — 与补全时间戳类似的批量操作窗口。

    针对歌词中常见符号自动处理时间戳：

    * **后补偿**：符号 cc=0 且 is_sentence_end=True 且有 sentence_end_ts 时，
      将句尾时间戳提升为普通时间戳（cc 改为 1），并将 sentence_end_ts 后移
      「后补偿」毫秒。
    * **前补偿**：符号 cc=1 且紧跟的第一个非符号字符 cc=0 时，将符号时间戳
      传递给该后续字符（cc 改为 1），并将符号自身时间戳前移「前补偿」毫秒。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("分离符号时间戳")
        self.resize(600, 640)
        self.setFont(QFont("Microsoft YaHei", 10))
        self._apply_clicked = False

        # 读取上次设置
        try:
            from strange_uta_game.frontend.settings.app_settings import AppSettings
            settings = AppSettings()
            saved_groups = settings.get("separate_symbol_ts.groups", list(_SEPARATE_SYM_DEFAULT))
            self._saved_groups: set[str] = set(saved_groups)
            self._saved_pre_comp: int = settings.get("separate_symbol_ts.pre_comp_ms", 150)
            self._saved_post_comp: int = settings.get("separate_symbol_ts.post_comp_ms", 150)
        except Exception:
            self._saved_groups = set(_SEPARATE_SYM_DEFAULT)
            self._saved_pre_comp = 150
            self._saved_post_comp = 150

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 说明文本
        desc = QLabel(
            "针对选中的符号分组，自动处理时间戳：\n"
            "• 后补偿：符号无普通时间戳（cc=0）但有句尾停顿标记时，将停顿时间提升为普通时间戳，"
            "并将句尾时间戳后移「后补偿」值。\n"
            "• 前补偿：符号已有时间戳（cc=1）且紧跟的第一个非符号字符无时间戳（cc=0）时，"
            "将符号时间戳传递给该字符，并将符号时间戳前移「前补偿」值。"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 符号分组选择（两列布局）
        group_box = QGroupBox("适用符号分组")
        group_hlayout = QHBoxLayout(group_box)
        mid = (len(_SEPARATE_SYM_GROUPS) + 1) // 2
        left_col = QVBoxLayout()
        right_col = QVBoxLayout()

        self._group_checkboxes: dict[str, QCheckBox] = {}
        for i, (key, label, examples, _) in enumerate(_SEPARATE_SYM_GROUPS):
            chk = QCheckBox(f"{label}   {examples}")
            chk.setChecked(key in self._saved_groups)
            self._group_checkboxes[key] = chk
            if i < mid:
                left_col.addWidget(chk)
            else:
                right_col.addWidget(chk)
        left_col.addStretch()
        right_col.addStretch()
        group_hlayout.addLayout(left_col)
        group_hlayout.addLayout(right_col)
        layout.addWidget(group_box)

        # 全选 / 全不选
        sel_row = QHBoxLayout()
        btn_all = PushButton("全选", self)
        btn_all.clicked.connect(self._select_all)
        btn_none = PushButton("全不选", self)
        btn_none.clicked.connect(self._deselect_all)
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # 补偿设置
        comp_box = QGroupBox("补偿时间戳")
        comp_form = QFormLayout(comp_box)

        self.spin_pre_comp = SpinBox(self)
        self.spin_pre_comp.setRange(0, 9999)
        self.spin_pre_comp.setValue(self._saved_pre_comp)
        self.spin_pre_comp.setSuffix(" ms")
        self.spin_pre_comp.setToolTip(
            "符号已有时间戳（cc=1）时，将其时间戳前移的量；\n"
            "同时将原始符号时间戳赋给紧跟的无时间戳非符号字符"
        )
        comp_form.addRow("前补偿（前移符号时间戳）:", self.spin_pre_comp)

        self.spin_post_comp = SpinBox(self)
        self.spin_post_comp.setRange(0, 9999)
        self.spin_post_comp.setValue(self._saved_post_comp)
        self.spin_post_comp.setSuffix(" ms")
        self.spin_post_comp.setToolTip(
            "符号无普通时间戳（cc=0）但有句尾停顿时，将句尾时间戳后移的量"
        )
        comp_form.addRow("后补偿（后移句尾时间戳）:", self.spin_post_comp)

        layout.addWidget(comp_box)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_apply = PrimaryPushButton("应用", self)
        btn_apply.clicked.connect(self._on_apply)
        btn_layout.addWidget(btn_apply)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def _select_all(self):
        for chk in self._group_checkboxes.values():
            chk.setChecked(True)

    def _deselect_all(self):
        for chk in self._group_checkboxes.values():
            chk.setChecked(False)

    def _on_apply(self):
        self._apply_clicked = True
        try:
            from strange_uta_game.frontend.settings.app_settings import AppSettings
            settings = AppSettings()
            settings.set("separate_symbol_ts.groups", list(self.get_selected_groups()))
            settings.set("separate_symbol_ts.pre_comp_ms", self.get_pre_comp_ms())
            settings.set("separate_symbol_ts.post_comp_ms", self.get_post_comp_ms())
            settings.save()
        except Exception:
            pass
        self.accept()

    def was_apply_clicked(self) -> bool:
        return self._apply_clicked

    def get_selected_groups(self) -> set[str]:
        return {key for key, chk in self._group_checkboxes.items() if chk.isChecked()}

    def get_symbol_chars(self) -> frozenset:
        """返回所有选中分组的字符集合"""
        chars: set = set()
        selected = self.get_selected_groups()
        for key, _, _, char_set in _SEPARATE_SYM_GROUPS:
            if key in selected:
                chars |= char_set
        return frozenset(chars)

    def get_pre_comp_ms(self) -> int:
        return self.spin_pre_comp.value()

    def get_post_comp_ms(self) -> int:
        return self.spin_post_comp.value()
