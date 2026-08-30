"""波形图高级设置对话框 — 齿轮按钮弹出的非模态 Fluent 工具窗口。

面板结构（与「AI 打轴」「自动插入导唱符」等最新工具窗口一致的
``FluentGroupBox`` 分区设计，内容区带 ScrollArea、``fit_to_screen`` 限幅）：

1. 显示模式：波形图 / 声谱图（互斥），附两种模式用途说明。
2. 网格与节拍：时间网格 / BPM 网格；BPM 手动输入 + 「自动检测」按钮
   （librosa 管线的 numpy 复刻，见 spectrum_core.detect_bpm；结果填回
   输入框供确认，并给出置信度）；节拍器开关 + 音量（播放期间按
   BPM/偏移触发节拍音，见 playback_metronome.PlaybackMetronome）。
3. 声谱参数：FFT 窗口、频率刻度、动态范围、频谱高度（仅声谱模式启用）。
4. 时间标签：标签拖拽 / 播放头居中 / 标签显示字符 / 标签显示注音
   （与设置页「打轴 → 波形时间标签」组共用同一组 timing.* 键，两处联动）。

行为要点：

- 非模态（``show()``）普通窗口，以主窗口为锚点；由 ``TimelineWidget``
  持强引用直到销毁，切歌时通过 ``set_audio_source()`` 联动刷新音源。
- BPM 检测线程由 ``task_runner`` 注册表持有并自回收；音源变化即取消在途
  任务，迟到结果按 ``sender()`` 身份丢弃（不会把旧歌 BPM 写进新歌设置）。
- 改动即时生效，经 ``applied`` 信号回传 TimelineWidget 应用并持久化。
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    LineEdit,
    SwitchButton,
    CaptionLabel,
    ComboBox,
    DoubleSpinBox,
    PushButton,
    RadioButton,
    ScrollArea,
    Slider,
)

from strange_uta_game.backend.infrastructure.audio.spectrum import first_sound_ms
from strange_uta_game.frontend.fluent_widgets import FluentGroupBox, RangeSlider
from strange_uta_game.frontend.window_sizing import fit_to_screen
from strange_uta_game.frontend.workers import BpmDetectWorker

_FFT_CHOICES = (512, 1024, 2048, 4096, 8192, 16384, 32768)
# 拍号选项（N/4，BPM 恒为四分音符时值 → 分子即每小节拍数）
_TIME_SIGNATURE_CHOICES = (2, 3, 4, 5, 6, 7, 8)
# 窗口重叠（Sonic Visualiser 口径）：overlap = 1 - hop/fft。
# 大 FFT 窗口损失的时间分辨率靠更大重叠补偿；重叠越大计算量/内存越大
#（超出矩阵预算会自动逐档降级，见 spectrum.pick_overlap_within_budget）。
_OVERLAP_CHOICES = (0.5, 0.75, 0.875, 0.9375, 0.96875, 0.984375)
# 频率范围双柄滑块的值域（Hz，对数刻度）：覆盖 CD 频带；两柄都拉到端点 = 全谱
_FREQ_RANGE_MIN_HZ = 30.0
_FREQ_RANGE_MAX_HZ = 22050.0


class WaveformAdvancedDialog(QDialog):
    """波形图/声谱图显示设置（非模态）。initial 为 display_settings() 快照。"""

    applied = pyqtSignal(dict)  # 完整设置 dict（键同 timing.* 持久化键）

    def __init__(
        self,
        initial: dict,
        audio_source: Optional[tuple] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._initial = dict(initial)
        self._audio_source = audio_source  # (mono samples, sample_rate) | None
        self._bpm_worker: Optional[BpmDetectWorker] = None
        self._bpm_running = False
        self._bpm_progress_pct = -1
        self._actual_height_cap = 400

        self.setWindowTitle(self.tr("波形图高级设置"))
        self.setWindowModality(Qt.WindowModality.NonModal)
        # Windows 下去掉标题栏的 "?" 帮助按钮
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        fit_to_screen(self, 520, 660)
        self.setMinimumSize(440, 300)

        # ── 内容区（ScrollArea 包裹，小屏可滚动） ──
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(10)

        # ── Panel 1：显示模式 ──
        self._mode_group = FluentGroupBox(self.tr("显示模式"), content)
        mode_row = QWidget(self._mode_group)
        mode_row_layout = QHBoxLayout(mode_row)
        mode_row_layout.setContentsMargins(0, 0, 0, 0)
        self.radio_waveform = RadioButton(self.tr("波形图"), mode_row)
        self.radio_spectrum = RadioButton(self.tr("声谱图"), mode_row)
        mode_row_layout.addWidget(self.radio_waveform)
        mode_row_layout.addWidget(self.radio_spectrum)
        mode_row_layout.addStretch(1)
        self.mode_hint = CaptionLabel(
            self.tr("波形图适合观察整体响度；声谱图按频率展示能量分布，适合音高定位"),
            self._mode_group,
        )
        self.mode_hint.setWordWrap(True)
        self._mode_group.contentLayout.addWidget(mode_row)
        self._mode_group.contentLayout.addWidget(self.mode_hint)

        # 双层波形开关（默认开）：外层峰值 + 内层 RMS；关闭回退纯峰值
        rms_row = QWidget(self._mode_group)
        rms_row_layout = QHBoxLayout(rms_row)
        rms_row_layout.setContentsMargins(0, 0, 0, 0)
        self._lbl_rms = BodyLabel(self.tr("双层波形（RMS）"), rms_row)
        self.rms_switch = SwitchButton(rms_row)
        self.rms_switch.setOnText(self.tr("开"))
        self.rms_switch.setOffText(self.tr("关"))
        rms_row_layout.addWidget(self._lbl_rms)
        rms_row_layout.addWidget(self.rms_switch)
        rms_row_layout.addStretch(1)
        self._mode_group.contentLayout.addWidget(rms_row)

        # ── Panel 2：网格与节拍 ──
        self._grid_group = FluentGroupBox(self.tr("网格与节拍"), content)
        grid_row = QWidget(self._grid_group)
        grid_row_layout = QHBoxLayout(grid_row)
        grid_row_layout.setContentsMargins(0, 0, 0, 0)
        self.radio_grid_time = RadioButton(self.tr("时间网格"), grid_row)
        self.radio_grid_bpm = RadioButton(self.tr("BPM 网格"), grid_row)
        grid_row_layout.addWidget(self.radio_grid_time)
        grid_row_layout.addWidget(self.radio_grid_bpm)
        grid_row_layout.addStretch(1)

        self.bpm_spin = DoubleSpinBox(self._grid_group)
        self.bpm_spin.setRange(10.0, 600.0)
        self.bpm_spin.setDecimals(1)
        self.bpm_spin.setSingleStep(0.5)
        self.bpm_spin.setMinimumWidth(90)
        self.btn_detect_bpm = PushButton(self.tr("自动检测"), self._grid_group)
        # 对齐首音：把「BPM 网格偏移」一键设到开头静音结束、首个有声信号
        # 的位置（spectrum.first_sound_ms，整曲毫秒级，无需后台线程）
        self.btn_align_first = PushButton(self.tr("对齐首音"), self._grid_group)
        self.btn_detect_bpm.setEnabled(audio_source is not None)
        self.btn_align_first.setEnabled(audio_source is not None)
        # Enter 不被默认按钮吞掉：弹窗里按回车应提交输入框（editingFinished），
        # 而不是触发焦点所在/默认的「自动检测」
        self.btn_detect_bpm.setAutoDefault(False)
        self.btn_align_first.setAutoDefault(False)
        self.bpm_status = CaptionLabel("", self._grid_group)
        self.bpm_status.setWordWrap(True)
        self.bpm_status.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        bpm_row = QWidget(self._grid_group)
        bpm_row_layout = QHBoxLayout(bpm_row)
        bpm_row_layout.setContentsMargins(0, 0, 0, 0)
        bpm_row_layout.addWidget(BodyLabel("BPM", bpm_row))
        bpm_row_layout.addWidget(self.bpm_spin)
        bpm_row_layout.addWidget(self.btn_detect_bpm)
        bpm_row_layout.addWidget(self.btn_align_first)
        bpm_row_layout.addStretch(1)

        # 网格数值行（线宽 + 偏移并排，折叠纵向高度）：
        # - 线宽：时间/BPM 网格共用；Fluent 风格 LineEdit（非 SpinBox，与
        #   AI 打轴/自动导唱等新式窗口一致），QIntValidator 限 0~100，
        #   失焦/回车提交；空串/非法/越界恢复上次有效值；0 = 不绘制网格
        # - 偏移（毫秒）：拍线相位对齐——节拍通常不从 0ms 开始，正值网格
        #   后移（延迟）、负值前移；与线宽同一套严格提交语义
        num_row = QWidget(self._grid_group)
        num_row_layout = QHBoxLayout(num_row)
        num_row_layout.setContentsMargins(0, 0, 0, 0)
        self._lbl_grid_width = BodyLabel(self.tr("网格线宽"), num_row)
        self.grid_width_edit = LineEdit(num_row)
        self.grid_width_edit.setValidator(QIntValidator(0, 100, self.grid_width_edit))
        self.grid_width_edit.setFixedWidth(56)
        self._grid_width_px_label = BodyLabel("px", num_row)
        self._lbl_grid_offset = BodyLabel(self.tr("BPM 网格偏移"), num_row)
        self.grid_offset_edit = LineEdit(num_row)
        self.grid_offset_edit.setValidator(
            QIntValidator(-600000, 600000, self.grid_offset_edit)
        )
        self.grid_offset_edit.setFixedWidth(72)
        self._grid_offset_ms_label = BodyLabel("ms", num_row)
        num_row_layout.addWidget(self._lbl_grid_width)
        num_row_layout.addWidget(self.grid_width_edit)
        num_row_layout.addWidget(self._grid_width_px_label)
        num_row_layout.addSpacing(16)
        num_row_layout.addWidget(self._lbl_grid_offset)
        num_row_layout.addWidget(self.grid_offset_edit)
        num_row_layout.addWidget(self._grid_offset_ms_label)
        num_row_layout.addStretch(1)
        self._last_valid_grid_width = 2
        self._last_valid_grid_offset = 0

        # 节拍行（拍号 + 节拍器开关并排）：
        # - 拍号（N/4）：小节线/小节号与节拍器重音共用的循环周期；BPM 为
        #   四分音符时值，分子即每小节拍数
        # - 节拍器：播放期间按上方 BPM 与「BPM 网格偏移」触发节拍音（仅
        #   播放中响；seek 重新对齐；变速下拍点仍在正确时间轴时刻）
        self.beats_per_bar_combo = ComboBox(self._grid_group)
        for numerator in _TIME_SIGNATURE_CHOICES:
            self.beats_per_bar_combo.addItem(f"{numerator}/4")
        ts_met_row = QWidget(self._grid_group)
        ts_met_row_layout = QHBoxLayout(ts_met_row)
        ts_met_row_layout.setContentsMargins(0, 0, 0, 0)
        self._lbl_beats_per_bar = BodyLabel(self.tr("拍号"), ts_met_row)
        self._lbl_metronome = BodyLabel(self.tr("节拍器"), ts_met_row)
        self.metronome_switch = SwitchButton(ts_met_row)
        self.metronome_switch.setOnText(self.tr("开"))
        self.metronome_switch.setOffText(self.tr("关"))
        ts_met_row_layout.addWidget(self._lbl_beats_per_bar)
        ts_met_row_layout.addWidget(self.beats_per_bar_combo)
        ts_met_row_layout.addSpacing(16)
        ts_met_row_layout.addWidget(self._lbl_metronome)
        ts_met_row_layout.addWidget(self.metronome_switch)
        ts_met_row_layout.addStretch(1)

        # 节拍器音量（0~100%）：结构同「显示高度」行；开关关闭时联动禁用
        # （同 tag_char→tag_ruby 的禁用模式）；拖动中只刷新数字，松手/点击
        # 轨道时应用（与其他滑条一致）
        self.met_volume_slider = Slider(Qt.Orientation.Horizontal, self._grid_group)
        self.met_volume_slider.setRange(0, 100)
        self.met_volume_caption = CaptionLabel("", self._grid_group)
        self.met_volume_caption.setMinimumWidth(56)
        met_volume_row = QWidget(self._grid_group)
        met_volume_row_layout = QHBoxLayout(met_volume_row)
        met_volume_row_layout.setContentsMargins(0, 0, 0, 0)
        self._lbl_met_volume = BodyLabel(self.tr("节拍器音量"), met_volume_row)
        met_volume_row_layout.addWidget(self._lbl_met_volume)
        met_volume_row_layout.addWidget(self.met_volume_slider)
        met_volume_row_layout.addWidget(self.met_volume_caption)

        # 拍号与节拍器共用一条说明（折叠两条 caption 为一条）
        self.ts_met_hint = CaptionLabel(
            self.tr("节拍器播放时按上述 BPM 与偏移打拍；重音与网格小节线按拍号循环"),
            self._grid_group,
        )
        self.ts_met_hint.setWordWrap(True)

        self._grid_group.contentLayout.addWidget(grid_row)
        self._grid_group.contentLayout.addWidget(bpm_row)
        self._grid_group.contentLayout.addWidget(num_row)
        self._grid_group.contentLayout.addWidget(ts_met_row)
        self._grid_group.contentLayout.addWidget(met_volume_row)
        self._grid_group.contentLayout.addWidget(self.ts_met_hint)
        self._grid_group.contentLayout.addWidget(self.bpm_status)

        # ── Panel 3：声谱参数 ──
        self._params_group = FluentGroupBox(self.tr("声谱参数"), content)
        self.fft_combo = ComboBox(self._params_group)
        for value in _FFT_CHOICES:
            self.fft_combo.addItem(str(value))
        self.overlap_combo = ComboBox(self._params_group)
        for overlap in _OVERLAP_CHOICES:
            self.overlap_combo.addItem(f"{overlap * 100:g}%")
        self.scale_combo = ComboBox(self._params_group)
        self.scale_combo.addItem(self.tr("对数"))
        self.scale_combo.addItem(self.tr("线性"))
        self.dyn_slider = Slider(Qt.Orientation.Horizontal, self._params_group)
        self.dyn_slider.setRange(20, 120)
        self.dyn_caption = CaptionLabel("", self._params_group)
        self.dyn_caption.setMinimumWidth(56)

        # 频率范围（双柄对数滑块）：两柄都拉到端点 = 全谱（收集为 0/0）。
        # 仅影响渲染期行映射与频率轴，不触发声谱重算
        self.freq_range_slider = RangeSlider(self._params_group)
        self.freq_range_slider.set_range(
            math.log(_FREQ_RANGE_MIN_HZ), math.log(_FREQ_RANGE_MAX_HZ)
        )
        self.freq_range_caption = CaptionLabel("", self._params_group)
        self.freq_range_caption.setMinimumWidth(72)

        # 显示高度（波形/声谱公共属性）放在「网格与节拍」Panel：
        # 对两种模式同时生效
        self.height_slider = Slider(Qt.Orientation.Horizontal, self._grid_group)
        self.height_slider.setRange(120, 400)
        self.height_caption = CaptionLabel("", self._grid_group)
        self.height_caption.setMinimumWidth(56)
        height_row = QWidget(self._grid_group)
        height_row_layout = QHBoxLayout(height_row)
        height_row_layout.setContentsMargins(0, 0, 0, 0)
        self._lbl_height = BodyLabel(self.tr("显示高度"), height_row)
        height_row_layout.addWidget(self._lbl_height)
        height_row_layout.addWidget(self.height_slider)
        height_row_layout.addWidget(self.height_caption)
        self._grid_group.contentLayout.addWidget(height_row)

        params_grid = QGridLayout()
        params_grid.setContentsMargins(0, 0, 0, 0)
        params_grid.setHorizontalSpacing(10)
        self._lbl_fft = BodyLabel(self.tr("FFT 窗口"), self._params_group)
        self._lbl_overlap = BodyLabel(self.tr("窗口重叠"), self._params_group)
        self._lbl_scale = BodyLabel(self.tr("频率刻度"), self._params_group)
        self._lbl_dyn = BodyLabel(self.tr("动态范围"), self._params_group)
        self._lbl_freq_range = BodyLabel(self.tr("频率范围"), self._params_group)
        self.overlap_hint = CaptionLabel("", self._params_group)
        self.overlap_hint.setWordWrap(True)
        params_grid.addWidget(self._lbl_fft, 0, 0)
        params_grid.addWidget(self.fft_combo, 0, 1)
        params_grid.addWidget(self._lbl_overlap, 1, 0)
        params_grid.addWidget(self.overlap_combo, 1, 1)
        params_grid.addWidget(self._lbl_scale, 2, 0)
        params_grid.addWidget(self.scale_combo, 2, 1)
        params_grid.addWidget(self._lbl_dyn, 3, 0)
        params_grid.addWidget(self.freq_range_slider, 4, 1)
        params_grid.addWidget(self.freq_range_caption, 4, 2)
        params_grid.addWidget(self._lbl_freq_range, 4, 0)
        # 实际 overlap 提示占位（跨三列）
        params_grid.addWidget(self.overlap_hint, 5, 0, 1, 3)
        params_grid.addWidget(self.dyn_slider, 3, 1)
        params_grid.addWidget(self.dyn_caption, 3, 2)
        params_grid.setColumnStretch(1, 1)
        self._params_group.contentLayout.addLayout(params_grid)

        # ── Panel 4：时间标签（与设置页「打轴 → 波形时间标签」组联动） ──
        self._tag_group = FluentGroupBox(self.tr("时间标签"), content)
        (
            self._lbl_tag_edit,
            self.tag_edit_switch,
        ) = self._make_tag_switch_row(self.tr("波形时间标签拖拽"))
        (
            self._lbl_center_playhead,
            self.center_playhead_switch,
        ) = self._make_tag_switch_row(self.tr("播放头居中模式"))
        (
            self._lbl_tag_char,
            self.tag_char_switch,
        ) = self._make_tag_switch_row(self.tr("波形标签显示字符"))
        (
            self._lbl_tag_ruby,
            self.tag_ruby_switch,
        ) = self._make_tag_switch_row(self.tr("波形标签显示注音"))
        self.tag_link_hint = CaptionLabel(
            self.tr("与设置页「打轴 → 波形时间标签」共用，两处修改即时同步"),
            self._tag_group,
        )
        self.tag_link_hint.setWordWrap(True)
        for row in (
            self._lbl_tag_edit.parentWidget(),
            self._lbl_center_playhead.parentWidget(),
            self._lbl_tag_char.parentWidget(),
            self._lbl_tag_ruby.parentWidget(),
        ):
            self._tag_group.contentLayout.addWidget(row)
        self._tag_group.contentLayout.addWidget(self.tag_link_hint)

        content_layout.addWidget(self._mode_group)
        content_layout.addWidget(self._grid_group)
        content_layout.addWidget(self._params_group)
        content_layout.addWidget(self._tag_group)
        content_layout.addStretch(1)
        scroll.setWidget(content)

        # ── 关闭行：改动即时生效，只需一个关闭按钮（固定在滚动区外） ──
        self.btn_close = PushButton(self.tr("关闭"), self)
        self.btn_close.setAutoDefault(False)
        self.btn_close.clicked.connect(self.close)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(self.btn_close)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.addWidget(scroll, 1)
        root.addLayout(close_row)

        self._load_initial()
        self._connect_signals()
        self._update_slider_captions()

    # ── 初始化与信号 ──

    def _make_tag_switch_row(self, label_text: str) -> tuple:
        """时间标签面板的「标签 + 开关」行（结构与双层波形行一致）。"""
        row = QWidget(self._tag_group)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        label = BodyLabel(label_text, row)
        switch = SwitchButton(row)
        switch.setOnText(self.tr("开"))
        switch.setOffText(self.tr("关"))
        row_layout.addWidget(label)
        row_layout.addWidget(switch)
        row_layout.addStretch(1)
        return label, switch

    def _load_initial(self) -> None:
        init = self._initial
        is_spectrum = init.get("display_mode") == "spectrum"
        self.radio_spectrum.setChecked(is_spectrum)
        self.radio_waveform.setChecked(not is_spectrum)
        self.rms_switch.setChecked(bool(init.get("waveform_rms_enabled", True)))
        self.refresh_overlap_hint(init)
        is_bpm = init.get("grid_mode") == "bpm"
        self.radio_grid_bpm.setChecked(is_bpm)
        self.radio_grid_time.setChecked(not is_bpm)
        grid_width = int(init.get("grid_line_width", 2))
        self._last_valid_grid_width = max(0, min(100, grid_width))
        self.grid_width_edit.setText(str(self._last_valid_grid_width))
        grid_offset = int(init.get("grid_offset_ms", 0))
        self._last_valid_grid_offset = max(-600000, min(600000, grid_offset))
        self.grid_offset_edit.setText(str(self._last_valid_grid_offset))
        beats_per_bar = int(init.get("beats_per_bar", 4) or 4)
        if beats_per_bar not in _TIME_SIGNATURE_CHOICES:
            beats_per_bar = 4
        self.beats_per_bar_combo.setCurrentIndex(
            _TIME_SIGNATURE_CHOICES.index(beats_per_bar)
        )
        self.bpm_spin.setValue(float(init.get("grid_bpm", 120.0)))
        fft = init.get("spectrum_fft_size", 8192)
        index = _FFT_CHOICES.index(fft) if fft in _FFT_CHOICES else 4
        self.fft_combo.setCurrentIndex(index)
        overlap = float(init.get("spectrum_overlap", 0.9375))
        self.overlap_combo.setCurrentIndex(
            _OVERLAP_CHOICES.index(overlap) if overlap in _OVERLAP_CHOICES else 3
        )
        self.scale_combo.setCurrentIndex(
            0 if init.get("spectrum_freq_scale", "log") == "log" else 1
        )
        self.dyn_slider.setValue(int(init.get("spectrum_dyn_range_db", 60)))
        self._set_freq_range_values(
            int(init.get("spectrum_freq_min_hz", 0) or 0),
            int(init.get("spectrum_freq_max_hz", 0) or 0),
        )
        self.height_slider.setValue(int(init.get("display_height", 120)))
        self.tag_edit_switch.setChecked(bool(init.get("tag_edit_enabled", True)))
        self.center_playhead_switch.setChecked(
            bool(init.get("center_playhead_enabled", False))
        )
        self.tag_char_switch.setChecked(bool(init.get("tag_char_enabled", True)))
        self.tag_ruby_switch.setChecked(bool(init.get("tag_ruby_enabled", True)))
        self.metronome_switch.setChecked(bool(init.get("metronome_enabled", False)))
        self.met_volume_slider.setValue(
            max(0, min(100, int(init.get("metronome_volume", 100))))
        )
        self._update_slider_captions()
        self._update_params_enabled()
        self._sync_tag_ruby_enabled()
        self._sync_met_volume_enabled()

    def _connect_signals(self) -> None:
        self.radio_spectrum.toggled.connect(self._on_mode_toggled)
        self.radio_waveform.toggled.connect(self._on_mode_toggled)
        self.radio_grid_time.toggled.connect(self._on_grid_toggled)
        self.rms_switch.checkedChanged.connect(lambda _v: self._emit_applied())
        self.radio_grid_bpm.toggled.connect(self._on_grid_toggled)
        # 严格失焦语义：只在失焦/回车（editingFinished）提交；输入过程中
        # 不自动应用。点击面板空白不转移焦点的问题由 mousePressEvent 显式
        # 清焦解决（见下），而非输入期定时提交。
        self.bpm_spin.editingFinished.connect(self._emit_applied)
        self.grid_width_edit.editingFinished.connect(self._commit_grid_width)
        self.grid_offset_edit.editingFinished.connect(self._commit_grid_offset)
        self.fft_combo.currentIndexChanged.connect(self._emit_applied)
        self.overlap_combo.currentIndexChanged.connect(self._emit_applied)
        self.scale_combo.currentIndexChanged.connect(self._emit_applied)
        self.beats_per_bar_combo.currentIndexChanged.connect(self._emit_applied)
        # 滑条：拖动中只刷新数字（避免高频写配置+重绘），
        # 松手或非拖动变更（点击轨道/键盘/滚轮）时立即应用
        self.dyn_slider.valueChanged.connect(
            lambda v: self._on_slider_value_changed(self.dyn_slider, v)
        )
        self.height_slider.valueChanged.connect(
            lambda v: self._on_slider_value_changed(self.height_slider, v)
        )
        self.dyn_slider.sliderReleased.connect(self._emit_applied)
        self.height_slider.sliderReleased.connect(self._emit_applied)
        # 频率范围双柄滑块：拖动中只刷新数字，松手应用（与其他滑条一致）
        self.freq_range_slider.rangeChanged.connect(self._on_freq_range_changed)
        self.freq_range_slider.rangeCommitted.connect(self._on_freq_range_committed)
        # 时间标签四开关：改动即时生效（applied → TimelineWidget 应用+持久化）
        self.tag_edit_switch.checkedChanged.connect(lambda _v: self._emit_applied())
        self.center_playhead_switch.checkedChanged.connect(
            lambda _v: self._emit_applied()
        )
        self.tag_char_switch.checkedChanged.connect(self._on_tag_char_toggled)
        self.tag_ruby_switch.checkedChanged.connect(lambda _v: self._emit_applied())
        # 节拍器开关即时生效；音量滑条与其他滑条同一套拖动/提交语义
        self.metronome_switch.checkedChanged.connect(self._on_metronome_toggled)
        self.met_volume_slider.valueChanged.connect(
            lambda v: self._on_slider_value_changed(self.met_volume_slider, v)
        )
        self.met_volume_slider.sliderReleased.connect(self._emit_applied)
        self.btn_detect_bpm.clicked.connect(self._on_detect_bpm)
        self.btn_align_first.clicked.connect(self._on_align_first_sound)

    def _on_tag_char_toggled(self, _checked: bool) -> None:
        """字符显示以注音显示为前提：字符关 → 注音开关禁用（与设置页联动一致）。"""
        self._sync_tag_ruby_enabled()
        self._emit_applied()

    def _sync_tag_ruby_enabled(self) -> None:
        enabled = self.tag_char_switch.isChecked()
        self.tag_ruby_switch.setEnabled(enabled)
        self._lbl_tag_ruby.setEnabled(enabled)

    def _on_metronome_toggled(self, _checked: bool) -> None:
        """节拍器开关：联动音量滑条可用性，改动即时生效。"""
        self._sync_met_volume_enabled()
        self._emit_applied()

    # ── 频率范围双柄滑块（对数域 ↔ Hz，0 = 自动/全谱） ──

    @staticmethod
    def _round_freq_hz(hz: float) -> int:
        """滑块连续值 → 设置值的合理取整（低频细、高频粗）。"""
        if hz < 100:
            return max(1, int(round(hz)))
        if hz < 1000:
            return int(round(hz / 10.0)) * 10
        return int(round(hz / 100.0)) * 100

    def _freq_range_values(self) -> tuple:
        """滑块位置 → (f_min, f_max) Hz；两柄都在端点 → (0, 0)（全谱）。"""
        lo_log = self.freq_range_slider.low()
        hi_log = self.freq_range_slider.high()
        at_min = lo_log <= math.log(_FREQ_RANGE_MIN_HZ) + 1e-9
        at_max = hi_log >= math.log(_FREQ_RANGE_MAX_HZ) - 1e-9
        if at_min and at_max:
            return 0, 0
        f_min = 0 if at_min else self._round_freq_hz(math.exp(lo_log))
        f_max = 0 if at_max else self._round_freq_hz(math.exp(hi_log))
        return f_min, f_max

    def _set_freq_range_values(self, f_min: int, f_max: int) -> None:
        """Hz → 滑块位置（0/越界钳到端点）；加载初始用，不发信号。"""
        lo_hz = _FREQ_RANGE_MIN_HZ if f_min <= 0 else min(
            max(float(f_min), _FREQ_RANGE_MIN_HZ), _FREQ_RANGE_MAX_HZ
        )
        hi_hz = _FREQ_RANGE_MAX_HZ if f_max <= 0 else min(
            max(float(f_max), _FREQ_RANGE_MIN_HZ), _FREQ_RANGE_MAX_HZ
        )
        self.freq_range_slider.set_values(math.log(lo_hz), math.log(hi_hz))

    def _freq_caption_text(self) -> str:
        f_min, f_max = self._freq_range_values()
        if f_min <= 0 and f_max <= 0:
            return self.tr("全谱")

        def fmt(v: int) -> str:
            return f"{v} Hz" if v < 1000 else f"{v / 1000:g} kHz"

        lo = fmt(f_min) if f_min > 0 else self.tr("自动")
        hi = fmt(f_max) if f_max > 0 else self.tr("自动")
        return f"{lo} ~ {hi}"

    def _update_freq_caption(self) -> None:
        self.freq_range_caption.setText(self._freq_caption_text())

    def _on_freq_range_changed(self, _lo: float, _hi: float) -> None:
        self._update_freq_caption()

    def _on_freq_range_committed(self, _lo: float, _hi: float) -> None:
        self._update_freq_caption()
        self._emit_applied()

    def _sync_met_volume_enabled(self) -> None:
        enabled = self.metronome_switch.isChecked()
        self.met_volume_slider.setEnabled(enabled)
        self._lbl_met_volume.setEnabled(enabled)

    def changeEvent(self, event) -> None:
        # 非模态窗口可能跨语言切换存活，静态文案需跟随重译
        from PyQt6.QtCore import QEvent as _QEvent

        if event.type() == _QEvent.Type.LanguageChange:
            self._retranslate()
        super().changeEvent(event)

    def _retranslate(self) -> None:
        self.setWindowTitle(self.tr("波形图高级设置"))
        self.radio_waveform.setText(self.tr("波形图"))
        self.radio_spectrum.setText(self.tr("声谱图"))
        self.radio_grid_time.setText(self.tr("时间网格"))
        self.radio_grid_bpm.setText(self.tr("BPM 网格"))
        self.btn_detect_bpm.setText(self.tr("自动检测"))
        self.btn_align_first.setText(self.tr("对齐首音"))
        self.btn_close.setText(self.tr("关闭"))
        self._lbl_grid_width.setText(self.tr("网格线宽"))
        self._lbl_grid_offset.setText(self.tr("BPM 网格偏移"))
        self._lbl_beats_per_bar.setText(self.tr("拍号"))
        self.ts_met_hint.setText(
            self.tr("节拍器播放时按上述 BPM 与偏移打拍；重音与网格小节线按拍号循环")
        )
        self._lbl_rms.setText(self.tr("双层波形（RMS）"))
        self.rms_switch.setOnText(self.tr("开"))
        self.rms_switch.setOffText(self.tr("关"))
        self._lbl_overlap.setText(self.tr("窗口重叠"))
        self.mode_hint.setText(
            self.tr("波形图适合观察整体响度；声谱图按频率展示能量分布，适合音高定位")
        )
        self._mode_group.setTitle(self.tr("显示模式"))
        self._grid_group.setTitle(self.tr("网格与节拍"))
        self._tag_group.setTitle(self.tr("时间标签"))
        self._params_group.setTitle(self.tr("声谱参数"))
        self._lbl_tag_edit.setText(self.tr("波形时间标签拖拽"))
        self._lbl_center_playhead.setText(self.tr("播放头居中模式"))
        self._lbl_tag_char.setText(self.tr("波形标签显示字符"))
        self._lbl_tag_ruby.setText(self.tr("波形标签显示注音"))
        self._lbl_metronome.setText(self.tr("节拍器"))
        self._lbl_met_volume.setText(self.tr("节拍器音量"))
        self.metronome_switch.setOnText(self.tr("开"))
        self.metronome_switch.setOffText(self.tr("关"))
        self.tag_link_hint.setText(
            self.tr("与设置页「打轴 → 波形时间标签」共用，两处修改即时同步")
        )
        for switch in (
            self.tag_edit_switch,
            self.center_playhead_switch,
            self.tag_char_switch,
            self.tag_ruby_switch,
        ):
            switch.setOnText(self.tr("开"))
            switch.setOffText(self.tr("关"))
        self._lbl_fft.setText(self.tr("FFT 窗口"))
        self._lbl_scale.setText(self.tr("频率刻度"))
        self._lbl_dyn.setText(self.tr("动态范围"))
        self._lbl_freq_range.setText(self.tr("频率范围"))
        self._update_freq_caption()
        self._lbl_height.setText(self.tr("显示高度"))
        self.scale_combo.setItemText(0, self.tr("对数"))
        self.scale_combo.setItemText(1, self.tr("线性"))

    def refresh_overlap_hint(self, settings: dict) -> None:
        """预算降级时提示实际使用的重叠率。"""
        preferred = float(settings.get("spectrum_overlap", 0.75))
        actual = settings.get("actual_spectrum_overlap")
        if actual is not None and actual < preferred:
            self.overlap_hint.setText(
                self.tr("因内存预算，实际使用 {p}% 重叠").format(
                    p=int(round(actual * 100))
                )
            )
        else:
            self.overlap_hint.setText("")

    def _on_slider_value_changed(self, slider, value: int) -> None:
        """滑条值变化：拖动中只刷新数字；非拖动（点击轨道/键盘）立即应用。"""
        self._update_slider_captions()
        if not slider.isSliderDown():
            self._emit_applied()

    def _update_slider_captions(self, *_args) -> None:
        self.dyn_caption.setText(f"{self.dyn_slider.value()} dB")
        self.met_volume_caption.setText(f"{self.met_volume_slider.value()}%")
        self._update_freq_caption()
        value = self.height_slider.value()
        if value > self._actual_height_cap:
            self.height_caption.setText(
                self.tr("实际 {px} px").format(px=self._actual_height_cap)
            )
        else:
            self.height_caption.setText(f"{value} px")

    def _update_params_enabled(self) -> None:
        self._params_group.setEnabled(self.radio_spectrum.isChecked())

    def _on_mode_toggled(self, checked: bool) -> None:
        self._update_params_enabled()
        # 同组两个 RadioButton 都连 toggled：一次切换先后触发「旧按钮
        # 取消 + 新按钮选中」，只在选中时提交（否则 applied 发两次）。
        if checked:
            self._emit_applied()

    def _on_grid_toggled(self, checked: bool) -> None:
        if checked:
            self._emit_applied()

    def _commit_grid_width(self) -> None:
        """提交网格线宽；空/非法/越界恢复上一次有效值；0 = 不绘制网格。"""
        text = self.grid_width_edit.text().strip()
        try:
            value = int(text)
        except ValueError:
            value = self._last_valid_grid_width
        if not 0 <= value <= 100:
            value = self._last_valid_grid_width
        self._last_valid_grid_width = value
        self.grid_width_edit.setText(str(value))
        self._emit_applied()

    def _commit_grid_offset(self) -> None:
        """提交 BPM 网格偏移；空/非法/越界恢复上一次有效值（±600000ms）。"""
        text = self.grid_offset_edit.text().strip()
        try:
            value = int(text)
        except ValueError:
            value = self._last_valid_grid_offset
        if not -600000 <= value <= 600000:
            value = self._last_valid_grid_offset
        self._last_valid_grid_offset = value
        self.grid_offset_edit.setText(str(value))
        self._emit_applied()

    def _collect(self) -> dict:
        freq_min_hz, freq_max_hz = self._freq_range_values()
        return {
            "display_mode": "spectrum" if self.radio_spectrum.isChecked() else "waveform",
            "grid_mode": "bpm" if self.radio_grid_bpm.isChecked() else "time",
            "grid_bpm": float(self.bpm_spin.value()),
            "grid_offset_ms": self._last_valid_grid_offset,
            "beats_per_bar": _TIME_SIGNATURE_CHOICES[
                max(0, self.beats_per_bar_combo.currentIndex())
            ],
            "grid_line_width": self._last_valid_grid_width,
            "spectrum_fft_size": _FFT_CHOICES[self.fft_combo.currentIndex()],
            "spectrum_overlap": _OVERLAP_CHOICES[
                max(0, self.overlap_combo.currentIndex())
            ],
            "spectrum_freq_scale": (
                "log" if self.scale_combo.currentIndex() == 0 else "linear"
            ),
            "spectrum_dyn_range_db": int(self.dyn_slider.value()),
            "spectrum_freq_min_hz": freq_min_hz,
            "spectrum_freq_max_hz": freq_max_hz,
            "display_height": int(self.height_slider.value()),
            "waveform_rms_enabled": bool(self.rms_switch.isChecked()),
            "tag_edit_enabled": bool(self.tag_edit_switch.isChecked()),
            "center_playhead_enabled": bool(self.center_playhead_switch.isChecked()),
            "tag_char_enabled": bool(self.tag_char_switch.isChecked()),
            "tag_ruby_enabled": bool(self.tag_ruby_switch.isChecked()),
            "metronome_enabled": bool(self.metronome_switch.isChecked()),
            "metronome_volume": int(self.met_volume_slider.value()),
        }

    def _emit_applied(self, *_args) -> None:
        self.applied.emit(self._collect())

    # ── 对外 ──

    @staticmethod
    def _same_audio_source(a: Optional[tuple], b: Optional[tuple]) -> bool:
        """音源同一性判断：数组按对象身份比较（值比较会触发 ndarray 逐元素
        比较，A 歌切 B 歌时抛 ValueError）。"""
        if a is None or b is None:
            return a is None and b is None
        return a[0] is b[0] and a[1] == b[1]

    def set_audio_source(self, audio_source: Optional[tuple]) -> None:
        """刷新 BPM 检测数据源（切歌/清空时由 TimelineWidget 调用）。

        音源变化即取消在途检测并作废其结果（中继按 is_task_current 过滤），
        防止旧歌的 BPM 写进新歌的设置。
        """
        if self._same_audio_source(self._audio_source, audio_source) and not self._bpm_running:
            return
        self._cancel_bpm_worker()
        self._audio_source = audio_source
        self._bpm_progress_pct = -1
        self.btn_detect_bpm.setEnabled(audio_source is not None)
        self.btn_align_first.setEnabled(audio_source is not None)
        if audio_source is None:
            self.bpm_status.setText("")

    def set_height_cap(self, cap_px: int) -> None:
        """当前窗口可容纳的实际显示高度（仅用于提示，不覆盖用户期望值）。"""
        cap_px = int(max(80, cap_px))
        if cap_px == self._actual_height_cap:
            return
        self._actual_height_cap = cap_px
        self._update_slider_captions()

    def sync_tag_settings(self, settings: dict) -> None:
        """外部（设置页）改了时间标签键时同步本弹窗开关。

        blockSignals 抑制 checkedChanged，避免同步本身再触发一轮
        applied → _apply_display_settings 循环；值未变时 setChecked
        本就不发 toggled，双保险。
        """
        pairs = (
            (self.tag_edit_switch, bool(settings.get("tag_edit_enabled", True))),
            (
                self.center_playhead_switch,
                bool(settings.get("center_playhead_enabled", False)),
            ),
            (self.tag_char_switch, bool(settings.get("tag_char_enabled", True))),
            (self.tag_ruby_switch, bool(settings.get("tag_ruby_enabled", True))),
        )
        for switch, value in pairs:
            if switch.isChecked() == value:
                continue
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)
        self._sync_tag_ruby_enabled()

    # ── BPM 自动检测（task_runner 自回收；槽按 sender 身份丢弃过期结果） ──

    def _on_align_first_sound(self) -> None:
        """把「BPM 网格偏移」一键对齐到开头静音结束、首个有声信号的位置。

        首音通常就是第一拍（b=0），对齐后 BPM 网格相位与节拍器拍点直接
        落在歌声起点。检测为纯 numpy 整曲毫秒级（first_sound_ms），无需
        像 BPM 检测那样走后台线程。
        """
        if self._audio_source is None:
            return
        samples, sample_rate = self._audio_source
        if not isinstance(samples, np.ndarray) or len(samples) == 0 or sample_rate <= 0:
            self.bpm_status.setText(self.tr("无音频数据，无法对齐"))
            return
        ms = first_sound_ms(samples, int(sample_rate))
        if ms is None:
            self.bpm_status.setText(self.tr("未检测到有效声音，偏移未修改"))
            return
        # 与偏移输入框同一套范围语义（±600000ms）；程序写入须显式提交
        #（editingFinished 只响应人工输入）
        value = int(round(max(-600000, min(600000, ms))))
        self._last_valid_grid_offset = value
        self.grid_offset_edit.setText(str(value))
        self._emit_applied()
        self.bpm_status.setText(
            self.tr("已对齐首音：{ms} ms（BPM 网格偏移）").format(ms=value)
        )

    def _on_detect_bpm(self) -> None:
        if self._audio_source is None or self._bpm_running:
            return
        samples, sample_rate = self._audio_source
        assert isinstance(samples, np.ndarray)
        self._bpm_running = True
        self._bpm_progress_pct = -1
        self.btn_detect_bpm.setEnabled(False)
        self.bpm_status.setText(self.tr("检测中…"))

        worker = BpmDetectWorker(samples, sample_rate)
        self._bpm_worker = worker
        from strange_uta_game.frontend.editor.timing import task_runner

        task_runner.start_task(
            self,
            worker,
            on_progress=self._on_bpm_progress,
            on_finished=self._on_bpm_result,
            on_error=self._on_bpm_error,
            is_current=lambda w: w is self._bpm_worker,
        )

    def _cancel_bpm_worker(self) -> None:
        worker = self._bpm_worker
        self._bpm_worker = None
        # 复位运行态并恢复按钮：set_audio_source 对相同音源会提前返回，
        # 不会走到 setEnabled——不在此恢复的话，检测中关闭窗口后按钮
        # 将永久禁用（P2-1）。
        self._bpm_running = False
        self.btn_detect_bpm.setEnabled(self._audio_source is not None)
        self.btn_align_first.setEnabled(self._audio_source is not None)
        if worker is not None:
            worker.request_cancel()
            self.bpm_status.setText("")  # 清掉过期的「检测中…」

    def is_task_current(self, worker) -> bool:
        """task_runner 中继的身份判定：旧任务的迟到结果不进入 UI 槽。"""
        return worker is self._bpm_worker

    def _on_bpm_progress(self, value: float) -> None:
        if not self._bpm_running:
            return
        pct = int(value * 100)
        if pct == self._bpm_progress_pct:
            return
        self._bpm_progress_pct = pct
        self.bpm_status.setText(f'{self.tr("检测中…")} {pct}%')

    def _on_bpm_result(self, result: dict) -> None:
        if not self._bpm_running:
            return  # 旧音源任务的迟到结果：只由 task_runner 回收，不更新 UI
        self._bpm_running = False
        self._bpm_worker = None
        self.btn_detect_bpm.setEnabled(self._audio_source is not None)
        self.btn_align_first.setEnabled(self._audio_source is not None)
        bpm = result.get("bpm")
        confidence = float(result.get("confidence", 0.0))
        if bpm is None or confidence < 0.05:
            self.bpm_status.setText(self.tr("未能识别 BPM，请手动输入"))
            return
        if 10.0 <= bpm <= 600.0:
            # editingFinished 只响应人工确认，程序 setValue 后必须显式提交
            self.bpm_spin.setValue(float(bpm))
            self._emit_applied()
        level = self.tr("高") if confidence > 0.66 else (
            self.tr("中") if confidence > 0.33 else self.tr("低")
        )
        self.bpm_status.setText(
            f'{self.tr("检测到")} {bpm:g} BPM · {self.tr("置信度")}{level}（{confidence:.0%}）'
        )

    def _on_bpm_error(self, message: str) -> None:
        if not self._bpm_running:
            return
        self._bpm_running = False
        self._bpm_worker = None
        self.btn_detect_bpm.setEnabled(self._audio_source is not None)
        self.btn_align_first.setEnabled(self._audio_source is not None)
        self.bpm_status.setText(f"{self.tr('检测失败')}：{message}")

    def mousePressEvent(self, event) -> None:
        # 点击空白/标签区域不转移焦点（Qt 默认），editingFinished 不会触发——
        # 显式清焦当前输入控件，使「失焦提交」语义对空白点击同样成立。
        from PyQt6.QtWidgets import QLineEdit, QAbstractSpinBox

        focus = self.focusWidget()
        if focus is not None and isinstance(focus, (QLineEdit, QAbstractSpinBox)):
            focus.clearFocus()
        super().mousePressEvent(event)

    def hideEvent(self, event) -> None:
        # 关闭时若检测仍在跑：只请求取消并立即返回，线程由 task_runner
        # 注册表持有并自回收（本弹窗销毁与否都不影响）
        self._cancel_bpm_worker()
        super().hideEvent(event)
