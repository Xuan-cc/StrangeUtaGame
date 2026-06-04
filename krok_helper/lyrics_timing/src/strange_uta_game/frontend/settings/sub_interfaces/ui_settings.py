"""界面设定子页面。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from qfluentwidgets import FluentIcon as FIF, PushButton, SettingCard, SettingCardGroup

from ..cards import ComboSettingCard, DoubleSpinSettingCard, FontSettingCard, SpinSettingCard
from ..checkpoint_marker_dialog import CheckpointMarkerDialog
from .base import SubSettingInterface


class UISubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = None
        self._settings_ref = None
        self._init_ui()

    def set_store(self, store):
        self._store = store

    def _init_ui(self):
        g = SettingCardGroup("界面设定", self.scrollWidget)
        self.card_theme = ComboSettingCard(FIF.BRUSH, "主题",
            "选择界面主题，或设为自动跟随系统切换", items=["自动", "浅色", "深色"], parent=g)
        self.card_main_font = FontSettingCard(FIF.FONT, "主文字字体",
            "卡拉OK预览主文字（当前行/上下文行）字体，取自系统已安装字体；同时用于全文本编辑的字宽统计", parent=g)
        self.card_ruby_font = FontSettingCard(FIF.FONT, "注音字体",
            "卡拉OK预览 Ruby 注音字体（节奏点标记字体固定为微软雅黑，不可更改）", parent=g)
        self.card_font_size = SpinSettingCard(FIF.FONT_SIZE, "基础字体大小",
            "非当前行的歌词字体像素大小", min_val=1, max_val=99, step=2, suffix=" px", parent=g)
        self.card_current_line_font_size = SpinSettingCard(FIF.FONT_SIZE, "当前行字体大小",
            "当前高亮行的字体像素大小（放大效果）", min_val=1, max_val=99, step=2, suffix=" px", parent=g)
        self.card_ruby_size = SpinSettingCard(FIF.FONT_SIZE, "注音字体大小",
            "Ruby注音的字体像素大小", min_val=1, max_val=99, step=1, suffix=" px", parent=g)
        self.card_ruby_spacing = SpinSettingCard(FIF.FONT_SIZE, "注音与主文字间距",
            "Ruby注音与主文字之间的垂直间距", min_val=0, max_val=99, step=1, suffix=" px", parent=g)
        self.card_cp_size = SpinSettingCard(FIF.FONT_SIZE, "节奏点标记大小",
            "Checkpoint节奏点标记的字体像素大小", min_val=1, max_val=99, step=1, suffix=" px", parent=g)
        self.card_line_height_factor = DoubleSpinSettingCard(FIF.FONT_SIZE, "行间距系数",
            "行高 = (当前行字体 + 注音 + 注音间距 + 节奏点)高度 × 系数",
            min_val=-1.00, max_val=5.00, step=0.05, decimals=2, suffix=" x", parent=g)
        self.card_alignment_margin = SpinSettingCard(FIF.FONT_SIZE, "左/右对齐时页边距",
            "左对齐或右对齐时歌词与窗口边缘的间距", min_val=0, max_val=500, step=4, suffix=" px", parent=g)
        self.card_lyrics_alignment = ComboSettingCard(FIF.ALIGNMENT, "歌词对齐方式",
            "卡拉OK预览中歌词文本的水平对齐方式（左对齐时注意行号区域不被覆盖）",
            items=["左对齐", "居中对齐", "右对齐"], parent=g)
        self.card_checkpoint_markers = SettingCard(FIF.FONT_SIZE, "Checkpoint 字符设定",
            "自定义节奏点标记的显示字符（首节奏点 / 后续 / 句尾，已打轴 / 未打轴）", g)
        self.btn_cp_markers = PushButton("设置字符", self.card_checkpoint_markers)
        self.btn_cp_markers.clicked.connect(self._on_open_checkpoint_markers)
        self.card_checkpoint_markers.hBoxLayout.addWidget(
            self.btn_cp_markers, 0, Qt.AlignmentFlag.AlignRight)
        self.card_checkpoint_markers.hBoxLayout.addSpacing(16)

        for c in [self.card_theme, self.card_main_font, self.card_ruby_font,
                  self.card_font_size, self.card_current_line_font_size,
                  self.card_ruby_size, self.card_ruby_spacing, self.card_cp_size,
                  self.card_line_height_factor, self.card_alignment_margin,
                  self.card_lyrics_alignment, self.card_checkpoint_markers]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

    def _on_open_checkpoint_markers(self):
        if self._settings_ref is None:
            return
        current = self._settings_ref.get("ui.checkpoint_markers", {})
        dialog = CheckpointMarkerDialog(current, self)
        if dialog.exec() == CheckpointMarkerDialog.DialogCode.Accepted:
            markers = dialog.get_markers()
            self._settings_ref.set("ui.checkpoint_markers", markers)
            self._settings_ref.save()
            # 通知外层
            self._notify_changed()

    def connect_signals(self):
        self.card_theme.index_changed.connect(self._notify_changed)
        self.card_main_font.value_changed.connect(self._notify_changed)
        self.card_ruby_font.value_changed.connect(self._notify_changed)
        self.card_font_size.value_changed.connect(self._notify_changed)
        self.card_current_line_font_size.value_changed.connect(self._notify_changed)
        self.card_ruby_size.value_changed.connect(self._notify_changed)
        self.card_ruby_spacing.value_changed.connect(self._notify_changed)
        self.card_cp_size.value_changed.connect(self._notify_changed)
        self.card_line_height_factor.value_changed.connect(self._notify_changed)
        self.card_alignment_margin.value_changed.connect(self._notify_changed)
        self.card_lyrics_alignment.index_changed.connect(self._notify_changed)

    def load_settings(self, s):
        self._settings_ref = s
        theme_idx = {"auto": 0, "light": 1, "dark": 2}.get(s.get("ui.theme", "auto"), 0)
        self.card_theme.setCurrentIndex(theme_idx)
        self.card_main_font.setValue(s.get("ui.main_font", "Microsoft YaHei"))
        self.card_ruby_font.setValue(s.get("ui.ruby_font", "Microsoft YaHei"))
        self.card_font_size.setValue(s.get("ui.font_size", 18))
        self.card_current_line_font_size.setValue(s.get("ui.current_line_font_size", 22))
        self.card_ruby_size.setValue(s.get("ui.ruby_size", 10))
        self.card_ruby_spacing.setValue(s.get("ui.ruby_spacing", 4))
        self.card_cp_size.setValue(s.get("ui.cp_size", 8))
        self.card_line_height_factor.setValue(s.get("ui.line_height_factor", 1.20))
        self.card_alignment_margin.setValue(s.get("ui.alignment_margin", 168))
        alignment_idx = {"left": 0, "center": 1, "right": 2}.get(s.get("ui.lyrics_alignment", "center"), 1)
        self.card_lyrics_alignment.setCurrentIndex(alignment_idx)

    def collect_settings(self, s):
        s.set("ui.theme", {0: "auto", 1: "light", 2: "dark"}.get(self.card_theme.currentIndex(), "auto"))
        s.set("ui.main_font", self.card_main_font.value())
        s.set("ui.ruby_font", self.card_ruby_font.value())
        s.set("ui.font_size", self.card_font_size.value())
        s.set("ui.current_line_font_size", self.card_current_line_font_size.value())
        s.set("ui.ruby_size", self.card_ruby_size.value())
        s.set("ui.ruby_spacing", self.card_ruby_spacing.value())
        s.set("ui.cp_size", self.card_cp_size.value())
        s.set("ui.line_height_factor", self.card_line_height_factor.value())
        s.set("ui.alignment_margin", self.card_alignment_margin.value())
        s.set("ui.lyrics_alignment",
              {0: "left", 1: "center", 2: "right"}.get(self.card_lyrics_alignment.currentIndex(), "center"))
