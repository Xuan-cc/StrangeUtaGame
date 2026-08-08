"""界面设定子页面。"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from qfluentwidgets import FluentIcon as FIF, PushButton, SettingCard, SettingCardGroup

from strange_uta_game.frontend.font_utils import DEFAULT_FONT_FAMILY, resolve_font_family

from ..cards import ComboSettingCard, DoubleSpinSettingCard, FontSettingCard, SpinSettingCard, TextSettingCard
from ..checkpoint_marker_dialog import CheckpointMarkerDialog
from ..ui_preview import InterfacePreview
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
        tr = self.tr
        # 预览直接挂在 viewport 上，而不是 scrollWidget 中：设置列表滚动时它仍固定
        # 在右上角。浮层不接收鼠标事件，不会挡住下方卡片的操作。
        self.preview = InterfacePreview(self.viewport(), floating=True)
        self.preview.show()
        QTimer.singleShot(0, self._position_preview)

        g = SettingCardGroup(tr("界面设定"), self.scrollWidget)
        self._tr_register(g, title_source="界面设定")
        self.card_theme = self._tr_register(
            ComboSettingCard(FIF.BRUSH, tr("主题"),
                tr("选择界面主题，或设为自动跟随系统切换"),
                items=[tr("自动"), tr("浅色"), tr("深色")], parent=g),
            title_source="主题", content_source="选择界面主题，或设为自动跟随系统切换")
        self.card_theme.set_item_sources(["自动", "浅色", "深色"])
        self.card_main_font = self._tr_register(
            FontSettingCard(FIF.FONT, tr("主文字字体"),
                tr("卡拉OK预览主文字（当前行/上下文行）字体，取自系统已安装字体；同时用于全文本编辑的字宽统计"), parent=g),
            title_source="主文字字体",
            content_source="卡拉OK预览主文字（当前行/上下文行）字体，取自系统已安装字体；同时用于全文本编辑的字宽统计")
        self.card_ruby_font = self._tr_register(
            FontSettingCard(FIF.FONT, tr("注音字体"),
                tr("卡拉OK预览 Ruby 注音字体（节奏点标记字体固定为微软雅黑，不可更改）"), parent=g),
            title_source="注音字体",
            content_source="卡拉OK预览 Ruby 注音字体（节奏点标记字体固定为微软雅黑，不可更改）")
        self.card_font_size = self._tr_register(
            SpinSettingCard(FIF.FONT_SIZE, tr("基础字体大小"),
                tr("非当前行的歌词字体像素大小"),
                min_val=1, max_val=99, step=2, suffix=" px", parent=g),
            title_source="基础字体大小", content_source="非当前行的歌词字体像素大小")
        self.card_current_line_font_size = self._tr_register(
            SpinSettingCard(FIF.FONT_SIZE, tr("当前行字体大小"),
                tr("当前高亮行的字体像素大小（放大效果）"),
                min_val=1, max_val=99, step=2, suffix=" px", parent=g),
            title_source="当前行字体大小", content_source="当前高亮行的字体像素大小（放大效果）")
        self.card_ruby_size = self._tr_register(
            SpinSettingCard(FIF.FONT_SIZE, tr("注音字体大小"),
                tr("Ruby注音的字体像素大小"),
                min_val=1, max_val=99, step=1, suffix=" px", parent=g),
            title_source="注音字体大小", content_source="Ruby注音的字体像素大小")
        self.card_ruby_spacing = self._tr_register(
            SpinSettingCard(FIF.FONT_SIZE, tr("注音与主文字间距"),
                tr("Ruby注音与主文字之间的垂直间距"),
                min_val=0, max_val=99, step=1, suffix=" px", parent=g),
            title_source="注音与主文字间距", content_source="Ruby注音与主文字之间的垂直间距")
        self.card_cp_size = self._tr_register(
            SpinSettingCard(FIF.FONT_SIZE, tr("节奏点标记大小"),
                tr("Checkpoint节奏点标记的字体像素大小"),
                min_val=1, max_val=99, step=1, suffix=" px", parent=g),
            title_source="节奏点标记大小", content_source="Checkpoint节奏点标记的字体像素大小")
        self.card_cp_spacing = self._tr_register(
            SpinSettingCard(FIF.FONT_SIZE, tr("节奏点与主文字间距"),
                tr("节奏点标记顶端与主文字底部的垂直间距（增大避免大字号时被文字遮挡）"),
                min_val=0, max_val=99, step=1, suffix=" px", parent=g),
            title_source="节奏点与主文字间距",
            content_source="节奏点标记顶端与主文字底部的垂直间距（增大避免大字号时被文字遮挡）")
        self.card_line_height_factor = self._tr_register(
            DoubleSpinSettingCard(FIF.FONT_SIZE, tr("行间距系数"),
                tr("行高 = (当前行字体 + 注音 + 注音间距 + 节奏点)高度 × 系数"),
                min_val=-1.00, max_val=5.00, step=0.05, decimals=2, suffix=" x", parent=g),
            title_source="行间距系数",
            content_source="行高 = (当前行字体 + 注音 + 注音间距 + 节奏点)高度 × 系数")
        self.card_alignment_margin = self._tr_register(
            SpinSettingCard(FIF.FONT_SIZE, tr("左/右对齐时页边距"),
                tr("左对齐或右对齐时歌词与窗口边缘的间距"),
                min_val=0, max_val=500, step=4, suffix=" px", parent=g),
            title_source="左/右对齐时页边距",
            content_source="左对齐或右对齐时歌词与窗口边缘的间距")
        self.card_lyrics_alignment = self._tr_register(
            ComboSettingCard(FIF.ALIGNMENT, tr("歌词对齐方式"),
                tr("卡拉OK预览中歌词文本的水平对齐方式（左对齐时注意行号区域不被覆盖）"),
                items=[tr("左对齐"), tr("居中对齐"), tr("右对齐")], parent=g),
            title_source="歌词对齐方式",
            content_source="卡拉OK预览中歌词文本的水平对齐方式（左对齐时注意行号区域不被覆盖）")
        self.card_lyrics_alignment.set_item_sources(["左对齐", "居中对齐", "右对齐"])
        self.card_checkpoint_markers = self._tr_register(
            SettingCard(FIF.FONT_SIZE, tr("Checkpoint 字符设定"),
                tr("自定义节奏点标记的显示字符（首节奏点 / 后续 / 句尾，已打轴 / 未打轴）"), g),
            title_source="Checkpoint 字符设定",
            content_source="自定义节奏点标记的显示字符（首节奏点 / 后续 / 句尾，已打轴 / 未打轴）")
        self.btn_cp_markers = PushButton(tr("设置字符"), self.card_checkpoint_markers)
        self._tr_register_text(self.btn_cp_markers, "setText", "设置字符")
        self.btn_cp_markers.clicked.connect(self._on_open_checkpoint_markers)
        self.card_checkpoint_markers.hBoxLayout.addWidget(
            self.btn_cp_markers, 0, Qt.AlignmentFlag.AlignRight)
        self.card_checkpoint_markers.hBoxLayout.addSpacing(16)
        self.card_needs_guide_symbol = self._tr_register(
            TextSettingCard(FIF.PIN, tr("导唱待办标记符号"),
                tr("标记某字符前需要插入导唱符时显示的符号（叠加在字符左上角，红色半透明）"),
                placeholder="✚", max_length=2, parent=g),
            title_source="导唱待办标记符号",
            content_source="标记某字符前需要插入导唱符时显示的符号（叠加在字符左上角，红色半透明）")
        self.card_needs_guide_size = self._tr_register(
            SpinSettingCard(FIF.FONT_SIZE, tr("导唱待办标记大小"),
                tr("导唱待办标记符号的字体像素大小"),
                min_val=4, max_val=64, step=1, suffix=" px", parent=g),
            title_source="导唱待办标记大小",
            content_source="导唱待办标记符号的字体像素大小")

        for c in [self.card_theme, self.card_main_font, self.card_ruby_font,
                  self.card_font_size, self.card_current_line_font_size,
                  self.card_ruby_size, self.card_ruby_spacing, self.card_cp_size,
                  self.card_cp_spacing,
                  self.card_line_height_factor, self.card_alignment_margin,
                  self.card_lyrics_alignment, self.card_checkpoint_markers,
                  self.card_needs_guide_symbol, self.card_needs_guide_size]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

    def _position_preview(self):
        viewport = self.viewport()
        margin = 18
        viewport_width = viewport.width()
        viewport_height = viewport.height()
        available_width = max(1, viewport_width - margin * 2)
        available_height = max(1, viewport_height - margin * 2)
        width = min(680, max(420, int(viewport_width * 0.46)), available_width)
        height = min(480, max(380, int(viewport_height * 0.48)), available_height)
        if not self.preview.user_resized:
            self.preview.resize(width, height)
        if self.preview.user_positioned:
            self.preview.clamp_to_parent()
        else:
            self.preview.move(
                max(margin, viewport_width - width - margin),
                margin,
            )
        self.preview.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "preview"):
            self._position_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "preview"):
            self._position_preview()

    def _on_open_checkpoint_markers(self):
        if self._settings_ref is None:
            return
        current = self._settings_ref.get("ui.checkpoint_markers", {})
        dialog = CheckpointMarkerDialog(current, self)
        if dialog.exec() == CheckpointMarkerDialog.DialogCode.Accepted:
            markers = dialog.get_markers()
            self._settings_ref.set("ui.checkpoint_markers", markers)
            self._settings_ref.save()
            self._refresh_preview()
            # 通知外层
            self._notify_changed()

    def connect_signals(self):
        for signal in (
            self.card_theme.index_changed,
            self.card_main_font.value_changed,
            self.card_ruby_font.value_changed,
            self.card_font_size.value_changed,
            self.card_current_line_font_size.value_changed,
            self.card_ruby_size.value_changed,
            self.card_ruby_spacing.value_changed,
            self.card_cp_size.value_changed,
            self.card_cp_spacing.value_changed,
            self.card_line_height_factor.value_changed,
            self.card_alignment_margin.value_changed,
            self.card_lyrics_alignment.index_changed,
            self.card_needs_guide_symbol.value_changed,
            self.card_needs_guide_size.value_changed,
        ):
            signal.connect(self._on_preview_setting_changed)

    def _on_preview_setting_changed(self, *_args):
        self._refresh_preview()
        self._notify_changed()

    def _refresh_preview(self):
        markers = (
            self._settings_ref.get("ui.checkpoint_markers", {})
            if self._settings_ref is not None
            else {}
        )
        self.preview.set_preview_values(
            main_font=self.card_main_font.value(),
            ruby_font=self.card_ruby_font.value(),
            font_size=self.card_font_size.value(),
            current_line_font_size=self.card_current_line_font_size.value(),
            ruby_size=self.card_ruby_size.value(),
            ruby_spacing=self.card_ruby_spacing.value(),
            cp_size=self.card_cp_size.value(),
            cp_spacing=self.card_cp_spacing.value(),
            line_height_factor=self.card_line_height_factor.value(),
            alignment_margin=self.card_alignment_margin.value(),
            alignment={0: "left", 1: "center", 2: "right"}.get(
                self.card_lyrics_alignment.currentIndex(), "center"
            ),
            needs_guide_symbol=(self.card_needs_guide_symbol.value() or "✚").strip()
            or "✚",
            needs_guide_size=self.card_needs_guide_size.value(),
            checkpoint_markers=markers,
        )

    def load_settings(self, s):
        self._settings_ref = s
        # embedded 模式下宿主接管主题（见 EMBEDDING.md §5）；隐藏 SUG 自己的
        # 主题选择卡，避免用户在子模块里改主题导致与宿主状态不一致。
        embedded = getattr(s, "_provider", None) is not None
        self.card_theme.setVisible(not embedded)
        theme_idx = {"auto": 0, "light": 1, "dark": 2}.get(s.get("ui.theme", "auto"), 0)
        self.card_theme.setCurrentIndex(theme_idx)
        # 显示实际生效的字体族：存储值在本平台不可用时（如 Windows 项目带到 mac，
        # 存的是"Microsoft YaHei"）回退到平台默认，避免选择器显示一个本机没有的字体名。
        self.card_main_font.setValue(resolve_font_family(s.get("ui.main_font", DEFAULT_FONT_FAMILY)))
        self.card_ruby_font.setValue(resolve_font_family(s.get("ui.ruby_font", DEFAULT_FONT_FAMILY)))
        self.card_font_size.setValue(s.get("ui.font_size", 18))
        self.card_current_line_font_size.setValue(s.get("ui.current_line_font_size", 22))
        self.card_ruby_size.setValue(s.get("ui.ruby_size", 10))
        self.card_ruby_spacing.setValue(s.get("ui.ruby_spacing", 4))
        self.card_cp_size.setValue(s.get("ui.cp_size", 8))
        self.card_cp_spacing.setValue(s.get("ui.cp_spacing", 4))
        self.card_line_height_factor.setValue(s.get("ui.line_height_factor", 1.20))
        self.card_alignment_margin.setValue(s.get("ui.alignment_margin", 168))
        alignment_idx = {"left": 0, "center": 1, "right": 2}.get(s.get("ui.lyrics_alignment", "center"), 1)
        self.card_lyrics_alignment.setCurrentIndex(alignment_idx)
        self.card_needs_guide_symbol.setValue(s.get("ui.needs_guide_symbol", "✚"))
        self.card_needs_guide_size.setValue(s.get("ui.needs_guide_size", 12))
        self._refresh_preview()

    def collect_settings(self, s):
        s.set("ui.theme", {0: "auto", 1: "light", 2: "dark"}.get(self.card_theme.currentIndex(), "auto"))
        s.set("ui.main_font", self.card_main_font.value())
        s.set("ui.ruby_font", self.card_ruby_font.value())
        s.set("ui.font_size", self.card_font_size.value())
        s.set("ui.current_line_font_size", self.card_current_line_font_size.value())
        s.set("ui.ruby_size", self.card_ruby_size.value())
        s.set("ui.ruby_spacing", self.card_ruby_spacing.value())
        s.set("ui.cp_size", self.card_cp_size.value())
        s.set("ui.cp_spacing", self.card_cp_spacing.value())
        s.set("ui.line_height_factor", self.card_line_height_factor.value())
        s.set("ui.alignment_margin", self.card_alignment_margin.value())
        s.set("ui.lyrics_alignment",
              {0: "left", 1: "center", 2: "right"}.get(self.card_lyrics_alignment.currentIndex(), "center"))
        sym = (self.card_needs_guide_symbol.value() or "✚").strip() or "✚"
        s.set("ui.needs_guide_symbol", sym)
        s.set("ui.needs_guide_size", self.card_needs_guide_size.value())
