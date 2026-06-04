"""全文本编辑界面。

以带内联时间戳的全文本格式编辑整篇歌词，自带完整时间轴（编解码委托后端
:mod:`strange_uta_game.backend.infrastructure.parsers.annotated_text`）。
应用时逐行独立解码，行的增删/重排/文本撞车都不会丢失或错配时间戳。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextEdit,
    QDialog,
    QCheckBox,
    QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QSize
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    SpinBox,
    SwitchButton,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    CaptionLabel,
)

import re
from typing import Optional, List

from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Ruby,
    RubyPart,
)
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.application.auto_check_service import (
    get_kanji_linked_indices,
)
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    CharType,
    get_char_type,
)


def _ruby_is_all_hiragana(ruby_text: str) -> bool:
    """注音文本是否全为平假名（含小假名、促音っ，范围 U+3040-U+309F）。"""
    return bool(ruby_text) and all("぀" <= c <= "ゟ" for c in ruby_text)


# 剥离行内结构化标签的正则（按顺序应用）
_STRIP_SINGER_RE = re.compile(r"【[^】]*】")
# 仅剥合规注音块：原文非空 + 含 ||（不合规块保留原样参与字宽统计）
_STRIP_RUBY_VALID_RE = re.compile(r"\{([^}]+?)\|\|[^}]*\}")
_STRIP_TIMESTAMP_RE = re.compile(r"\[>?(?:T|\d+:\d{2}\.\d{2})\]")  # 仅剥合法 token：[T]/[>T]/[mm:ss.xx]


def _strip_line_tags(line: str) -> str:
    """去除行内合规结构化标签，返回视觉文本（非法块/token 保留原样参与字宽统计）。"""
    text = _STRIP_SINGER_RE.sub("", line)
    text = _STRIP_RUBY_VALID_RE.sub(r"\1", text)
    text = _STRIP_TIMESTAMP_RE.sub("", text)
    return text


def _calc_ch_width(text: str, fm) -> float:
    """用字体度量计算文本视觉宽度（单位 ch）。

    1ch = 一个全角字符（汉字/假名）宽度的一半，即 ``fm.horizontalAdvance('一') / 2``。
    全角字符自然得到 2ch；拉丁字母按比例字体实际像素宽度折算，宽字母（如 W）
    会比窄字母（如 i）得到更大的 ch 值。整行一次测量兼顾字距调整。
    字体缺少 '一' 字形时退化为 'M' 宽度的两倍作为参考。
    """
    if not text:
        return 0.0
    ref = fm.horizontalAdvance("一")
    if ref <= 0:
        ref = fm.horizontalAdvance("M") * 2
    return fm.horizontalAdvance(text) / (ref / 2)


class _LineNumberArea(QWidget):
    """行号栏（绘制委托给 LineNumberPlainTextEdit）。"""

    def __init__(self, editor: "LineNumberPlainTextEdit"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.line_number_area_paint_event(event)


class _LineInfoArea(QWidget):
    """行字数栏（绘制委托给 LineNumberPlainTextEdit）。"""

    def __init__(self, editor: "LineNumberPlainTextEdit"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_info_area_width(), 0)

    def paintEvent(self, event):
        self._editor.line_info_area_paint_event(event)


class LineNumberPlainTextEdit(QPlainTextEdit):
    """带左侧行号栏的纯文本编辑器。

    行号即文本块编号（每行对应一条 Sentence），从 1 起。
    """

    zoom_requested = pyqtSignal(int)  # Alt+滚轮缩放：+1 放大 / -1 缩小

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_ch_width = True
        # 字宽（ch）统计专用度量：跟随卡拉OK主文字字体，与编辑器显示字体解耦。
        # None 表示尚未设置，回退到编辑器自身 fontMetrics。
        self._ch_width_fm = None
        self._line_number_area = _LineNumberArea(self)
        self._line_info_area = _LineInfoArea(self)
        self.blockCountChanged.connect(lambda _=0: self._update_width())
        self.updateRequest.connect(self._on_update_request)
        self._update_width()

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits + 6

    def line_info_area_width(self) -> int:
        if not self._show_ch_width:
            return 0
        fm = self.fontMetrics()
        return fm.horizontalAdvance("99.9ch") + 16

    def _update_width(self):
        self.setViewportMargins(
            self.line_number_area_width(), 0, self.line_info_area_width(), 0
        )

    def set_show_ch_width(self, show: bool):
        if self._show_ch_width == show:
            return
        self._show_ch_width = show
        self._line_info_area.setVisible(show)
        self._update_width()

    def set_ch_width_font(self, family: str) -> str:
        """设置字宽统计的测量字体（跟随卡拉OK主文字字体）。

        缺少全角参考字 `一` 时回退微软雅黑测量（见 font_utils）。
        返回实际用于测量的字体族名，供界面提示显示。
        """
        from strange_uta_game.frontend.font_utils import make_ch_width_metrics

        self._ch_width_fm, effective = make_ch_width_metrics(family)
        self._line_info_area.update()
        return effective

    def _ch_width_metrics(self):
        """返回字宽统计使用的度量；未设置时回退编辑器自身度量。"""
        return self._ch_width_fm if self._ch_width_fm is not None else self.fontMetrics()

    def _on_update_request(self, rect, dy):
        if dy:
            self._line_number_area.scroll(0, dy)
            self._line_info_area.scroll(0, dy)
        else:
            self._line_number_area.update(
                0, rect.y(), self._line_number_area.width(), rect.height()
            )
            self._line_info_area.update(
                0, rect.y(), self._line_info_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )
        info_w = self.line_info_area_width()
        self._line_info_area.setGeometry(
            QRect(cr.right() - info_w, cr.top(), info_w, cr.height())
        )

    def line_number_area_paint_event(self, event):
        from strange_uta_game.frontend.theme import theme

        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), theme.editor_gutter_bg)
        painter.setPen(theme.editor_gutter_fg)

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(
            self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        )
        bottom = top + round(self.blockBoundingRect(block).height())
        line_h = self.fontMetrics().height()
        width = self._line_number_area.width() - 4

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, top, width, line_h,
                    Qt.AlignmentFlag.AlignRight, str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def line_info_area_paint_event(self, event):
        from strange_uta_game.frontend.theme import theme

        painter = QPainter(self._line_info_area)
        painter.fillRect(event.rect(), theme.editor_gutter_bg)
        painter.setPen(theme.editor_gutter_fg)

        block = self.firstVisibleBlock()
        top = round(
            self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        )
        bottom = top + round(self.blockBoundingRect(block).height())
        line_h = self.fontMetrics().height()
        area_w = self._line_info_area.width()

        # 字宽以卡拉OK主文字字体度量；标签文本仍用编辑器自身字体绘制。
        fm = self._ch_width_metrics()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                stripped = _strip_line_tags(block.text())
                ch = _calc_ch_width(stripped, fm)
                ch_r = round(ch, 1)
                label = f"{int(ch_r)}ch" if ch_r == int(ch_r) else f"{ch_r}ch"
                painter.drawText(
                    0, top, area_w - 6, line_h,
                    Qt.AlignmentFlag.AlignRight, label,
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())

    def wheelEvent(self, event):
        """Alt+滚轮缩放字体（放大/缩小），其余情况维持默认滚动。

        注意：Windows 下按住 Alt 滚动时，滚轮增量会被转到水平轴
        （angleDelta().x()），故需同时读取 x/y，否则 y 恒为 0、缩放失效。
        """
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            ad = event.angleDelta()
            delta = ad.y() or ad.x()
            if delta > 0:
                self.zoom_requested.emit(1)
            elif delta < 0:
                self.zoom_requested.emit(-1)
            event.accept()
            return
        super().wheelEvent(event)


class _TimedFormatHighlighter(QSyntaxHighlighter):
    """带时间戳全文本格式的语法着色（类 VSCode）。

    着色：起始时间戳 [..]、句尾时间戳 [>..]、演唱者标签 【..】、
    花括号/分隔符 { } || | ,。颜色随深/浅主题切换。
    """

    # 合规块：{原文||读音...}（原文非空）
    _VALID_BLOCK_RE = re.compile(r"\{[^{}]+\|\|[^{}]*\}")
    _SEP_INNER_RE = re.compile(r"\|\||[{}|,]")   # 仅在合规块内着色
    _SINGER_RE = re.compile(r"【[^】]*】")
    # 仅匹配合法 token：[T] / [>T] 或 [mm:ss.xx] / [>mm:ss.xx]
    _END_TS_RE = re.compile(r"\[>(?:T|\d+:\d{2}\.\d{2})\]")
    _START_TS_RE = re.compile(r"\[(?:T|\d+:\d{2}\.\d{2})\]")

    def __init__(self, document):
        super().__init__(document)
        self._formats = self._build_formats()

    @staticmethod
    def _build_formats():
        from strange_uta_game.frontend.theme import theme

        palette = {
            "sep": theme.syntax_separator,
            "singer": theme.syntax_singer,
            "start": theme.syntax_timestamp,
            "end": theme.syntax_timestamp_end,
        }
        fmts = {}
        for key, color in palette.items():
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            fmts[key] = f
        return fmts

    def rehighlight_with_theme(self):
        """主题切换后重建颜色并重刷。"""
        self._formats = self._build_formats()
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        # 分隔符着色仅限合规块内（不合规的 {..} 当普通文字，不着色）
        sep_fmt = self._formats["sep"]
        for block_m in self._VALID_BLOCK_RE.finditer(text):
            block_start = block_m.start()
            for sep_m in self._SEP_INNER_RE.finditer(block_m.group()):
                self.setFormat(block_start + sep_m.start(), sep_m.end() - sep_m.start(), sep_fmt)

        # 演唱者标签 / 时间戳：全局范围着色
        for regex, key in (
            (self._SINGER_RE, "singer"),
            (self._START_TS_RE, "start"),
            (self._END_TS_RE, "end"),
        ):
            fmt = self._formats[key]
            for m in regex.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class DeleteRubyByTypeDialog(QDialog):
    """按字符类型选择要删除注音的对话框。

    片假名拆分为两个子类型：
    - katakana_hiragana_ruby: 注音全为平假名的片假名字符
    - katakana_english_ruby:  注音含有非平假名内容（如英文）的片假名字符
    两者默认均不启用。
    """

    _TYPE_LABELS: list[tuple] = [
        (CharType.HIRAGANA, "ひらがな（平假名）"),
        ("katakana_hiragana_ruby", "カタカナ（片假名・注音为平假名）"),
        ("katakana_english_ruby", "カタカナ（片假名・注音含有英文）"),
        (CharType.KANJI, "漢字（汉字）"),
        (CharType.ALPHABET, "アルファベット（英文字母）"),
        (CharType.NUMBER, "数字"),
        (CharType.SYMBOL, "記号（符号）"),
        (CharType.LONG_VOWEL, "長音符号（ー、～等）"),
        (CharType.OTHER, "その他（♪等特殊符号）"),
        (CharType.SPACE, "空格"),
    ]

    _TYPE_NAME_MAP: dict = {
        CharType.HIRAGANA: "hiragana",
        "katakana_hiragana_ruby": "katakana_hiragana_ruby",
        "katakana_english_ruby": "katakana_english_ruby",
        CharType.KANJI: "kanji",
        CharType.ALPHABET: "alphabet",
        CharType.NUMBER: "number",
        CharType.SYMBOL: "symbol",
        CharType.LONG_VOWEL: "long_vowel",
        CharType.SOKUON: "sokuon",
        CharType.OTHER: "other",
        CharType.SPACE: "space",
    }

    _NAME_TYPE_MAP = {v: k for k, v in _TYPE_NAME_MAP.items()}

    def __init__(self, parent=None, initial_types: list[str] | None = None):
        """
        Args:
            parent: 父组件
            initial_types: 初始选中的类型名称列表（config 格式），如 ["hiragana", "katakana_hiragana_ruby"]
        """
        super().__init__(parent)
        self.setWindowTitle("按类型删除注音")
        self.resize(320, 400)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        lbl = QLabel("选择要删除注音的字符类型：")
        lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl)

        # 确定默认选中项（片假名两个子类型默认均不启用）
        if initial_types is not None:
            default_set = {self._NAME_TYPE_MAP[n] for n in initial_types if n in self._NAME_TYPE_MAP}
        else:
            default_set = {CharType.HIRAGANA}

        self._checkboxes: list[tuple] = []
        for char_type, label in self._TYPE_LABELS:
            cb = QCheckBox(label, self)
            cb.setChecked(char_type in default_set)
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

    def selected_types(self) -> list:
        """返回用户选中的类型键列表（CharType 或特殊字符串）。"""
        return [ct for ct, cb in self._checkboxes if cb.isChecked()]

    def selected_type_names(self) -> list[str]:
        """返回用户选中的类型名称列表（config 格式）。"""
        return [self._TYPE_NAME_MAP[ct] for ct, cb in self._checkboxes if cb.isChecked()]


class RubyInterface(QWidget):
    """注音编辑界面

    全文本视图 + 批量操作。
    """

    rubies_changed = pyqtSignal()
    close_requested = pyqtSignal()  # 底部「关闭」按钮请求关闭承载它的对话框

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        # 标题
        title = QLabel("全文本编辑")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        title_tip = CaptionLabel("（编辑后点「应用更改」写回；行号对应歌词行，时间轴随文本保留）")
        title_layout = QHBoxLayout()
        title_layout.addWidget(title)
        title_layout.addWidget(title_tip)
        title_layout.addStretch()
        layout.addLayout(title_layout)

        # 说明（功能 + 格式，简洁）
        desc = CaptionLabel(
            "逐行编辑整篇歌词。格式：{原文||读音} 为注音块，"
            "注音块中`|` 分 RubyPart、`,` 分字；时间戳在字前 [分:秒.厘秒]（空=[T]），"
            "句尾 [>…] 贴在字后，演唱者切换用 【名】。"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

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

        # 字号调整（与上面按钮同栏；也可用 Alt+滚轮）
        batch_layout.addSpacing(12)

        from strange_uta_game.frontend.settings.settings_interface import AppSettings
        _settings = AppSettings()
        _saved_font_size = _settings.get("fulltext_editor.font_size", 12)
        _saved_show_ch = _settings.get("fulltext_editor.show_ch_width", True)
        self._ch_width_font = _settings.get("ui.main_font", "Microsoft YaHei")

        batch_layout.addWidget(CaptionLabel("字号"))
        self.spin_font = SpinBox(self)
        self.spin_font.setRange(8, 48)
        self.spin_font.setValue(_saved_font_size)
        self.spin_font.setMinimumWidth(130)
        self.spin_font.valueChanged.connect(self._apply_font_size)
        batch_layout.addWidget(self.spin_font)

        batch_layout.addSpacing(12)
        batch_layout.addWidget(CaptionLabel("字宽统计"))
        self.switch_ch_width = SwitchButton(self)
        self.switch_ch_width.setChecked(_saved_show_ch)
        self.switch_ch_width.setMinimumWidth(50)
        self.switch_ch_width.checkedChanged.connect(self._on_ch_width_toggled)
        batch_layout.addWidget(self.switch_ch_width)

        # 提示当前用于计算字宽的字体（跟随卡拉OK主文字字体）
        self.lbl_ch_font = CaptionLabel("")
        batch_layout.addWidget(self.lbl_ch_font)

        batch_layout.addStretch()

        layout.addLayout(batch_layout)

        # 全文本编辑器（带行号栏 + 语法着色，Alt+滚轮缩放字体）
        self.text_edit = LineNumberPlainTextEdit()
        self.text_edit.setFont(QFont("Microsoft YaHei", 12))
        self.text_edit.setPlaceholderText(
            "加载项目后，歌词将以带时间戳的注音格式显示在此处...\n"
            "示例: {大冒険||[00:01.00]だ|[00:01.20]い,...}"
        )
        self.text_edit.setMinimumHeight(300)
        self._highlighter = _TimedFormatHighlighter(self.text_edit.document())
        # 实时高亮光标所在行
        self.text_edit.cursorPositionChanged.connect(self._highlight_current_line)
        # Alt+滚轮缩放字体 → 同步到字号 SpinBox（由其驱动实际字号）
        self.text_edit.zoom_requested.connect(self._on_zoom_requested)
        # 主题切换时刷新着色 / 行号栏 / 当前行高亮颜色
        from strange_uta_game.frontend.theme import theme
        theme.changed.connect(self._on_theme_changed)
        layout.addWidget(self.text_edit, stretch=1)

        self.text_edit.set_show_ch_width(_saved_show_ch)
        # 字宽统计跟随卡拉OK主文字字体（缺字回退微软雅黑测量），并提示实际字体
        _effective_ch_font = self.text_edit.set_ch_width_font(self._ch_width_font)
        self._update_ch_font_label(_effective_ch_font)

        # 底部栏：左下角信息，右下角 应用更改 / 还原 / 关闭
        action_layout = QHBoxLayout()

        self.lbl_stats = CaptionLabel("共 0 行，0 个注音")
        action_layout.addWidget(self.lbl_stats)

        action_layout.addStretch()

        self.btn_apply = PrimaryPushButton("应用更改", self)
        self.btn_apply.setIcon(FIF.ACCEPT)
        self.btn_apply.clicked.connect(self._on_apply_changes)
        self.btn_apply.setEnabled(False)
        action_layout.addWidget(self.btn_apply)

        self.btn_revert = PushButton("还原", self)
        self.btn_revert.setIcon(FIF.CANCEL)
        self.btn_revert.clicked.connect(self._on_revert)
        self.btn_revert.setEnabled(False)
        action_layout.addWidget(self.btn_revert)

        self.btn_close = PushButton("关闭", self)
        self.btn_close.setIcon(FIF.CLOSE)
        self.btn_close.clicked.connect(self.close_requested.emit)
        action_layout.addWidget(self.btn_close)

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
        elif change_type == "settings":
            self._refresh_ch_width_font()

    def _refresh_ch_width_font(self):
        """设置变更时，重新读取卡拉OK主文字字体并应用到字宽统计。"""
        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        self._ch_width_font = AppSettings().get("ui.main_font", "Microsoft YaHei")
        effective = self.text_edit.set_ch_width_font(self._ch_width_font)
        self._update_ch_font_label(effective)

    def is_dirty(self) -> bool:
        """检查文本编辑器内容是否与项目数据不同"""
        if not self._project:
            return False
        return self.text_edit.toPlainText() != self._lines_to_text()

    def scroll_to_line(self, line_idx: int, char_idx: int = 0):
        """从打轴界面切换至全文本编辑时，将输入光标跳转到 (line_idx, char_idx)。

        文本由 _lines_to_text() 以带时间戳格式生成；这里用后端
        :func:`...annotated_text.timed_line_columns` 复算同一编码、得到目标
        字符的字形列号（计入内联时间戳 token、演唱者标签、花括号等语法长度），
        并按相同的跨行演唱者延续计算行首继承，保证列号与渲染严格对齐。
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

        from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
            sentence_to_timed_line,
            timed_line_columns,
        )

        id_to_name, _name_to_id, default_id = self._singer_context()
        offset = self._global_offset()
        # 按 _lines_to_text 的跨行演唱者延续，算出本行行首继承的 singer
        inherited = default_id
        for s in self._project.sentences[:line_idx]:
            _, inherited = sentence_to_timed_line(
                s.characters,
                singer_id_to_name=id_to_name,
                line_singer_id=s.singer_id,
                default_singer_id=default_id,
                inherited_singer_id=inherited,
                offset_ms=offset,
            )

        cols = timed_line_columns(
            chars,
            singer_id_to_name=id_to_name,
            line_singer_id=sentence.singer_id,
            default_singer_id=default_id,
            inherited_singer_id=inherited,
            offset_ms=offset,
        )
        if 0 <= char_idx < len(cols):
            column = cols[char_idx]
        elif cols:
            # char_idx == len(chars)：定位到末字符之后
            column = cols[-1] + 1
        else:
            column = 0

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

    def focus_line(self, line_idx: int, char_idx: int = 0):
        """进入界面时聚焦某行：光标定位（触发整行高亮）+ 把该行滚到视口顶部。"""
        if not self._project or not self._project.sentences:
            return
        line_idx = max(0, min(line_idx, len(self._project.sentences) - 1))
        self.scroll_to_line(line_idx, char_idx)
        # 尽量把目标行滚到视口顶部（而非跳到深处），便于从该行往下看
        bar = self.text_edit.verticalScrollBar()
        if bar is not None:
            bar.setValue(min(line_idx, bar.maximum()))

    def _highlight_current_line(self):
        """实时高亮光标所在行（随光标移动刷新，extraSelection 不改文本）。"""
        doc = self.text_edit.document()
        if doc is None:
            return
        block = self.text_edit.textCursor().block()
        if not block.isValid():
            return
        from strange_uta_game.frontend.theme import theme

        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(theme.editor_current_line)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        # 选中整个文本块（而非折叠光标），使自动换行占多视觉行的长行整行高亮
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(
            QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
        )
        sel.cursor = cursor
        self.text_edit.setExtraSelections([sel])

    def _on_theme_changed(self):
        """主题切换：重建着色配色、刷新行号栏与当前行高亮。"""
        if hasattr(self, "_highlighter"):
            self._highlighter.rehighlight_with_theme()
        self.text_edit._line_number_area.update()
        self.text_edit._line_info_area.update()
        self._highlight_current_line()

    def _apply_font_size(self, pt: int):
        """设置编辑器字号（由字号 SpinBox 与 Alt+滚轮共同驱动）。"""
        font = self.text_edit.font()
        font.setPointSize(pt)
        self.text_edit.setFont(font)
        self.text_edit._update_width()
        self.text_edit._line_number_area.update()
        self.text_edit._line_info_area.update()
        from strange_uta_game.frontend.settings.settings_interface import AppSettings
        _settings = AppSettings()
        _settings.set("fulltext_editor.font_size", pt)
        _settings.save()

    def _on_zoom_requested(self, delta: int):
        """Alt+滚轮：调整字号 SpinBox（其 valueChanged 再驱动实际字号）。"""
        self.spin_font.setValue(self.spin_font.value() + delta)

    def _update_ch_font_label(self, effective_family: str):
        """更新字宽统计字体提示。effective_family 为实际测量所用字体族。"""
        if effective_family and effective_family != self._ch_width_font:
            # 所选字体缺少全角参考字形，已回退测量
            self.lbl_ch_font.setText(f"字宽字体：{self._ch_width_font} → {effective_family}")
            self.lbl_ch_font.setToolTip(
                f"所选字体「{self._ch_width_font}」缺少全角参考字形，字宽改用「{effective_family}」测量"
            )
        else:
            self.lbl_ch_font.setText(f"字宽字体：{effective_family}")
            self.lbl_ch_font.setToolTip("字宽统计跟随卡拉OK主文字字体（设置 › 界面设定 › 主文字字体）")

    def _on_ch_width_toggled(self, checked: bool):
        self.text_edit.set_show_ch_width(checked)
        from strange_uta_game.frontend.settings.settings_interface import AppSettings
        _settings = AppSettings()
        _settings.set("fulltext_editor.show_ch_width", checked)
        _settings.save()

    # ==================== 内部方法 ====================

    def _refresh_display(self):
        """刷新全部显示"""
        has_project = self._project is not None

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

    def _global_offset(self) -> int:
        """全局偏移（项目优先，回退设置）——编解码时用它使显示与打轴一致。"""
        if not self._project:
            return 0
        offset = self._project.global_offset_ms
        if offset is None:
            try:
                from strange_uta_game.frontend.settings.settings_interface import (
                    AppSettings,
                )
                offset = AppSettings().get("export.offset_ms", 0)
            except Exception:
                offset = 0
        return offset or 0

    def _singer_context(self):
        """返回 (id→name, name→id, default_singer_id)，供带时间戳格式编解码用。"""
        singers = list(getattr(self._project, "singers", []) or [])
        id_to_name = {s.id: s.name for s in singers}
        name_to_id = {s.name: s.id for s in singers}
        default_id = ""
        for s in singers:
            if getattr(s, "is_default", False):
                default_id = s.id
                break
        if not default_id and singers:
            default_id = singers[0].id
        return id_to_name, name_to_id, default_id

    def _lines_to_text(self) -> str:
        """将项目歌词转为带内联时间戳的全文本（无损往返格式）。

        每行委托给后端
        :func:`...annotated_text.sentence_to_timed_line`，并跨行延续演唱者
        （与 Nicokara 导出 prev_singer 行为一致）。
        """
        if not self._project:
            return ""
        from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
            sentence_to_timed_line,
        )

        id_to_name, _name_to_id, default_id = self._singer_context()
        offset = self._global_offset()
        inherited = default_id
        lines: List[str] = []
        for sentence in self._project.sentences:
            line, inherited = sentence_to_timed_line(
                sentence.characters,
                singer_id_to_name=id_to_name,
                line_singer_id=sentence.singer_id,
                default_singer_id=default_id,
                inherited_singer_id=inherited,
                offset_ms=offset,
            )
            lines.append(line)
        return "\n".join(lines)

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
        user_dict = app_settings.load_effective_dictionary()
        annotate_katakana_with_english = app_settings.get(
            "ruby_dictionary.annotate_katakana_with_english", False
        )
        # 用户主动触发：不做中文检测——按下"自动分析全部注音"按钮即明确表达
        # 注音意图，避免纯汉字日文行被误判跳过。
        # LLM 整首一次发送：传入全部行文本以保留上下文（LLM 未激活时忽略）。
        lines = [s.text for s in self._project.sentences] if self._project else []
        analyzer = app_settings.build_ruby_analyzer(
            lines, annotate_katakana_with_english=annotate_katakana_with_english
        )
        return AutoCheckService(
            ruby_analyzer=analyzer,
            auto_check_flags=auto_check_flags,
            user_dictionary=user_dict,
            annotate_katakana_with_english=annotate_katakana_with_english,
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

        # 先把文本框里的修改写回项目，避免按钮刷新后丢失用户编辑
        if self.is_dirty():
            self._on_apply_changes()

        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        _llm_active = AppSettings().llm_ruby_active()
        # LLM 注音激活时不需要本地日语 IME，跳过 WinRT 安装引导。
        if not _llm_active:
            from strange_uta_game.frontend.winrt_japanese_guide import (
                ensure_winrt_japanese,
            )

            if not ensure_winrt_japanese(self):
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
        # LLM 注音时是否仍应用用户词典（非 LLM 模式恒为 True）。
        _apply_user_dict = (
            AppSettings().llm_apply_user_dict() if _llm_active else True
        )

        # 第一步：应用注音
        try:
            auto_check.apply_to_project(
                self._project, only_noruby=only_noruby,
                apply_user_dict=_apply_user_dict,
            )
            self._refresh_display()
            if hasattr(self, "_store"):
                self._store.notify("rubies")
            # LLM 注音失败时已回退本地引擎，提示用户。
            _analyzer = getattr(auto_check, "_analyzer", None)
            if getattr(_analyzer, "llm_failed", False):
                InfoBar.warning(
                    title="LLM 注音失败，已回退本地引擎",
                    content=str(getattr(_analyzer, "last_error", "") or ""),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )
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

        # 先把文本框里的修改写回项目，避免删除操作刷新后丢失用户编辑
        if self.is_dirty():
            self._on_apply_changes()

        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        app_settings = AppSettings()
        saved_types = app_settings.get("auto_check.delete_ruby_types", [])

        dlg = DeleteRubyByTypeDialog(self, initial_types=saved_types)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_types()

        # 保存用户选择到配置（无论是否有变化）
        app_settings.set("auto_check.delete_ruby_types", dlg.selected_type_names())
        app_settings.save()

        if not selected:
            return

        # 拆解选中项：区分普通 CharType 与片假名子类型
        ct_selected = {x for x in selected if isinstance(x, CharType)}
        delete_kata_hira = "katakana_hiragana_ruby" in selected
        delete_kata_eng = "katakana_english_ruby" in selected

        extended = set(ct_selected)
        if CharType.HIRAGANA in ct_selected:
            extended.add(CharType.SOKUON)  # 平假名选中时同时处理促音っ

        removed = 0
        for sentence in self._project.sentences:
            kanji_linked = get_kanji_linked_indices(sentence.characters)
            for idx, ch in enumerate(sentence.characters):
                if not ch.ruby:
                    continue
                if idx in kanji_linked:
                    continue  # 与汉字连词，视为汉字，保留注音
                ct = get_char_type(ch.char)

                # 片假名（不含促音ッ，ッ/っ 由 SOKUON 路径独立处理）
                is_kata_family = ct == CharType.KATAKANA
                if is_kata_family:
                    if delete_kata_hira or delete_kata_eng:
                        is_hira = _ruby_is_all_hiragana(ch.ruby.text)
                        if (is_hira and delete_kata_hira) or (not is_hira and delete_kata_eng):
                            ch.set_ruby(None)
                            removed += 1
                    continue

                if ct in extended:
                    if ct == CharType.SOKUON and ch.char == "っ" and CharType.HIRAGANA not in ct_selected:
                        continue
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
        """将文本编辑器内容应用回项目（逐行独立解码，自带完整时间轴）。

        采用带内联时间戳的全文本格式：每行用 ``parse_timed_line`` 独立解码成
        一条 Sentence，时间戳/句尾/连词/演唱者全部来自文本本身。因此**不再做
        任何跨行映射或 diff** —— 行的增删、重排、文本撞车都不会丢失或错配
        时间戳；新增的无 token 字符自然得到空轴。
        """
        if not self._project:
            return

        from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
            parse_timed_line,
        )

        text = self.text_edit.toPlainText()
        new_line_strs = text.split("\n")

        _id_to_name, name_to_id, default_singer = self._singer_context()
        if not default_singer:
            default_singer = (
                self._project.sentences[0].singer_id
                if self._project.sentences
                else "default"
            )

        offset = self._global_offset()
        new_sentences: List[Sentence] = []
        parse_errors = []
        inherited = default_singer
        for i, ls in enumerate(new_line_strs):
            try:
                chars, inherited = parse_timed_line(
                    ls,
                    name_to_singer_id=name_to_id,
                    default_singer_id=default_singer,
                    inherited_singer_id=inherited,
                    offset_ms=offset,
                )
            except Exception as e:
                parse_errors.append(f"第 {i + 1} 行: {e}")
                chars = []
            # 行级 singer：取首字符 singer，空行沿用 inherited
            if chars and chars[0].singer_id:
                line_singer = chars[0].singer_id
            else:
                line_singer = inherited or default_singer
            new_sentences.append(Sentence(singer_id=line_singer, characters=chars))

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

        self._project.sentences = new_sentences

        # parse_timed_line 已减去 offset 还原为原始时间戳；这里再统一 set_offset
        # 派生 global_*（含新建字符），与编码时加的 offset 对称。
        for sentence in self._project.sentences:
            for ch in sentence.characters:
                ch.set_offset(offset)

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


class FullTextEditDialog(QDialog):
    """全文本编辑对话框。

    从打轴界面以对话框形式打开 :class:`RubyInterface`，
    通过共享的 ProjectStore 与打轴界面双向同步：在此处「应用更改」会
    notify("lyrics"/"rubies")，打轴界面据此刷新。
    """

    def __init__(self, store, parent=None, current_line: int = 0, current_char: int = 0):
        super().__init__(parent)
        self.setWindowTitle("全文本编辑")
        # 支持最大化/全屏（QDialog 默认无最大化按钮）
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.resize(1400, 900)
        main_win = parent.window() if parent is not None else None
        if main_win is not None and main_win.isMaximized():
            self.setWindowState(Qt.WindowState.WindowMaximized)
        elif main_win is not None and main_win.windowState() & Qt.WindowState.WindowFullScreen:
            self.setWindowState(Qt.WindowState.WindowFullScreen)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.interface = RubyInterface(self)
        self.interface.set_store(store)
        self.interface.close_requested.connect(self.accept)
        if getattr(store, "project", None) is not None:
            self.interface.set_project(store.project)
        layout.addWidget(self.interface, stretch=1)

        # 进入时定位到当前行的当前字符（高亮该行、光标落在该字符），并滚到视口顶部
        self.interface.focus_line(current_line, current_char)
