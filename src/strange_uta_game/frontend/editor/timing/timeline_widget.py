"""时间轴控件。

显示音频波形、当前播放位置、打轴节奏点分布。
支持缩放和横向滚动，类似视频剪辑软件的时间线。
"""

from __future__ import annotations

import math
from typing import List, NamedTuple, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QMouseEvent,
    QWheelEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygon,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import CaptionLabel, Slider, SwitchButton

from strange_uta_game.frontend.perf_log import log_slow_method
from strange_uta_game.frontend.theme import theme


# (line_idx, char_idx, cp_idx, is_sentence_end) —— 可反查模型到具体 checkpoint 的句柄。
# 选中态、命中测试、拖拽提交都以此为身份，跨 set_time_tags 重排存活。
TagHandle = Tuple[int, int, int, bool]


class TimeTag(NamedTuple):
    """波形上一个时间标签的渲染 + 命中数据。

    ``label`` 仅在该字符第一个 checkpoint 时非空（跨 checkpoint 去重）；``ruby`` 为该
    checkpoint 对应注音文本。``handle`` 用于命中后定位模型 checkpoint。
    """

    ts: int
    label: Optional[str]
    ruby: Optional[str]
    handle: TagHandle


# ──────────────────────────────────────────────
# 波形显示区域
# ──────────────────────────────────────────────

class WaveformDisplay(QWidget):
    """波形显示区域 - 绘制音频波形 + 时间网格 + 标签 + 播放头"""

    seek_requested = pyqtSignal(int)
    scroll_position_changed = pyqtSignal(float)
    zoom_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._current_ms = 0
        # 时间标签列表（含模型句柄）；label 仅在该字符第一个 checkpoint 时非空
        self._time_tags: List[TimeTag] = []
        self._warning_time_tags: List[TimeTag] = []

        # 音频数据
        self._samples: Optional[np.ndarray] = None
        self._sample_rate: int = 44100
        self._channels: int = 2

        # 缩放和滚动
        self._zoom_factor: float = 50.0  # 默认50x缩放，减少初始渲染压力
        self._zoom_enabled: bool = True
        self._scroll_position: float = 0.0

        # 波形峰值缓存
        self._peaks_cache: Optional[List[tuple]] = None
        self._peaks_cache_key: Optional[tuple] = None  # (width, zoom, scroll, samples_id)

        # 左键拖动平移状态
        self._pan_start_x: Optional[float] = None
        self._pan_start_scroll: float = 0.0
        self._is_panning: bool = False

        # 自动滚动挂起（用户手动操作后 6s 内不跟随播放头）
        self._auto_scroll_suspended: bool = False
        self._suspension_timer = QTimer(self)
        self._suspension_timer.setSingleShot(True)
        self._suspension_timer.setInterval(6000)
        self._suspension_timer.timeout.connect(self._resume_auto_scroll)

        self.setMinimumHeight(80)
        self.setMouseTracking(True)

        # 监听主题变化，触发重绘
        theme.changed.connect(self.update)

    def _max_scroll(self) -> float:
        """当前缩放下允许的最大滚动位置，保证视窗末尾不超出音频范围。"""
        if self._zoom_factor <= 1.0:
            return 0.0
        return max(0.0, 1.0 - 1.0 / self._zoom_factor)

    def _clamp_scroll(self, position: float) -> float:
        return max(0.0, min(self._max_scroll(), position))

    def set_duration(self, ms: int):
        self._duration_ms = ms
        # 时长变化后重新 clamp，避免旧滚动位置超出新范围
        self._scroll_position = self._clamp_scroll(self._scroll_position)
        self.update()

    def _suspend_auto_scroll(self) -> None:
        """用户手动操作后挂起自动滚动，重置 6s 倒计时。"""
        self._auto_scroll_suspended = True
        self._suspension_timer.start()

    def _resume_auto_scroll(self) -> None:
        """恢复自动跟随播放头。"""
        self._auto_scroll_suspended = False
        self._suspension_timer.stop()

    def set_playing(self, playing: bool) -> None:
        """播放状态变化：重新开始播放时取消自动滚动挂起。"""
        if playing:
            self._resume_auto_scroll()

    def set_position(self, ms: int):
        self._current_ms = ms
        # 自动滚动保持播放头可见（用户手动操作后挂起）
        if self._duration_ms > 0 and self._zoom_factor > 1.0 and not self._auto_scroll_suspended:
            visible_start = self._scroll_position * self._duration_ms
            visible_end = visible_start + self._duration_ms / self._zoom_factor
            if ms < visible_start or ms > visible_end:
                new_scroll = self._clamp_scroll(
                    (ms - self._duration_ms / (2 * self._zoom_factor)) / self._duration_ms
                )
                self._scroll_position = new_scroll
                self.scroll_position_changed.emit(self._scroll_position)
        self.update()

    @log_slow_method(
        "timeline.set_time_tags",
        12,
        lambda self, args, kwargs: {"tags": len(args[0]) if args else 0},
    )
    def set_time_tags(self, tags: List[Tuple[int, str, int, int, int, bool, Optional[str]]]):
        # tags: (timestamp_ms, char_text, line_idx, char_idx, cp_idx, is_sentence_end, ruby_text)
        # 同一字符 (line_idx, char_idx) 的第一个 checkpoint 携带 char 标签，后续不重复；ruby 始终携带
        seen_chars: set = set()
        normal: List[TimeTag] = []
        warning: List[TimeTag] = []
        running_max = -1
        for ts, char, line_idx, char_idx, cp_idx, is_end, ruby_text in tags:
            char_key = (line_idx, char_idx)
            label: Optional[str] = char if char_key not in seen_chars else None
            seen_chars.add(char_key)
            handle: TagHandle = (line_idx, char_idx, cp_idx, is_end)
            item = TimeTag(ts, label, ruby_text, handle)
            if ts < running_max:
                warning.append(item)
            else:
                normal.append(item)
                running_max = ts
        self._time_tags = sorted(normal, key=lambda x: x.ts)
        self._warning_time_tags = sorted(warning, key=lambda x: x.ts)
        self.update()

    def set_audio_data(self, samples: np.ndarray, sample_rate: int, channels: int):
        self._samples = samples
        self._sample_rate = sample_rate
        self._channels = channels
        # 清除波形缓存
        self._peaks_cache = None
        self._peaks_cache_key = None
        self.update()

    def clear_audio_data(self):
        self._samples = None
        self._sample_rate = 0
        self._channels = 0
        self._peaks_cache = None
        self._peaks_cache_key = None
        self.update()

    def set_zoom(self, zoom: float):
        self._zoom_factor = max(1.0, min(100.0, zoom))
        # 缩放变化后重新 clamp，避免当前滚动位置超出新的有效范围
        self._scroll_position = self._clamp_scroll(self._scroll_position)
        self.update()

    def set_zoom_enabled(self, enabled: bool) -> None:
        self._zoom_enabled = enabled

    def set_scroll_position(self, position: float):
        self._scroll_position = self._clamp_scroll(position)
        self.update()

    @log_slow_method(
        "timeline.compute_waveform_peaks",
        20,
        lambda self, args, kwargs: {
            "width": args[0] if args else kwargs.get("width"),
            "zoom": f"{self._zoom_factor:.2f}",
        },
    )
    def _compute_waveform_peaks(self, width: int) -> Optional[List[tuple]]:
        """计算波形峰值数据（按像素降采样），带缓存"""
        if self._samples is None or self._duration_ms <= 0 or width <= 0:
            return None

        # 缓存键：(width, zoom, scroll_position, samples_id)
        samples_id = id(self._samples)
        cache_key = (width, self._zoom_factor, self._scroll_position, samples_id)

        if self._peaks_cache_key == cache_key and self._peaks_cache is not None:
            return self._peaks_cache

        visible_start_ms = self._scroll_position * self._duration_ms
        visible_duration_ms = self._duration_ms / self._zoom_factor
        visible_end_ms = visible_start_ms + visible_duration_ms

        start_sample = int((visible_start_ms / 1000.0) * self._sample_rate)
        end_sample = int((visible_end_ms / 1000.0) * self._sample_rate)
        start_sample = max(0, min(start_sample, len(self._samples) - 1))
        end_sample = max(start_sample + 1, min(end_sample, len(self._samples)))

        visible_samples = self._samples[start_sample:end_sample]

        # 立体声混合为单声道
        if self._channels > 1:
            visible_samples = np.mean(visible_samples, axis=1)

        # 按像素宽度降采样
        samples_per_pixel = max(1, len(visible_samples) // width)
        peaks = []

        for i in range(width):
            start_idx = i * samples_per_pixel
            end_idx = min(start_idx + samples_per_pixel, len(visible_samples))
            if start_idx >= len(visible_samples):
                break
            chunk = visible_samples[start_idx:end_idx]
            if len(chunk) > 0:
                peaks.append((float(np.min(chunk)), float(np.max(chunk))))

        # 更新缓存
        self._peaks_cache = peaks
        self._peaks_cache_key = cache_key

        return peaks

    @log_slow_method(
        "timeline.paint",
        20,
        lambda self, args, kwargs: {
            "tags": len(self._time_tags) + len(self._warning_time_tags),
            "zoom": f"{self._zoom_factor:.2f}",
        },
    )
    def paintEvent(self, a0: Optional[QPaintEvent]):
        _ = a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), theme.waveform_bg)

        if self._duration_ms <= 0:
            painter.setPen(theme.text_hint)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, self.tr("请加载音频文件")
            )
            return

        visible_start_ms = self._scroll_position * self._duration_ms
        visible_duration_ms = self._duration_ms / self._zoom_factor
        visible_end_ms = visible_start_ms + visible_duration_ms

        self._draw_time_grid(painter, w, h, visible_start_ms, visible_end_ms)
        self._draw_waveform(painter, w, h)
        self._draw_time_tags(painter, w, h, visible_start_ms, visible_end_ms)
        self._draw_playhead(painter, w, h, visible_start_ms, visible_duration_ms)

    def _draw_time_grid(self, painter: QPainter, w: int, h: int,
                        visible_start_ms: float, visible_end_ms: float):
        visible_duration = visible_end_ms - visible_start_ms
        if visible_duration <= 10000:
            grid_interval = 1000
        elif visible_duration <= 60000:
            grid_interval = 5000
        elif visible_duration <= 300000:
            grid_interval = 10000
        else:
            grid_interval = 60000

        painter.setPen(QPen(theme.border_primary, 1))
        first_grid = int(visible_start_ms / grid_interval) * grid_interval
        for t in range(first_grid, int(visible_end_ms) + 1, grid_interval):
            if visible_duration > 0:
                ratio = (t - visible_start_ms) / visible_duration
                x = int(ratio * w)
                painter.drawLine(x, 0, x, h)

                painter.setPen(theme.text_secondary)
                s = t // 1000
                time_text = f"{s // 60}:{s % 60:02d}"
                painter.drawText(x + 2, 12, time_text)
                painter.setPen(QPen(theme.border_primary, 1))

    def _draw_waveform(self, painter: QPainter, w: int, h: int):
        peaks = self._compute_waveform_peaks(w)
        if not peaks:
            return

        mid_y = h // 2
        amplitude_scale = h / 2.0 * 0.8

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.waveform_fill)

        # 上半部分
        for i, (_, max_val) in enumerate(peaks):
            y = int(mid_y - max_val * amplitude_scale)
            painter.drawRect(i, y, 1, mid_y - y)

        # 下半部分
        for i, (min_val, _) in enumerate(peaks):
            y = int(mid_y - min_val * amplitude_scale)
            painter.drawRect(i, mid_y, 1, y - mid_y)

        # 中心线
        painter.setPen(QPen(theme.waveform_line, 1))
        painter.drawLine(0, mid_y, w, mid_y)

    @staticmethod
    def _format_ruby_label(ruby_text: str) -> str:
        if len(ruby_text) > 4:
            return f"「{ruby_text[:4]}...」"
        return f"「{ruby_text}」"

    def _draw_time_tags(self, painter: QPainter, w: int, h: int,
                        visible_start_ms: float, visible_end_ms: float):
        visible_duration = visible_end_ms - visible_start_ms
        if visible_duration <= 0:
            return

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        fm = painter.fontMetrics()
        label_y = fm.ascent() + 1  # 所有标签统一贴顶显示，竖线在其下方展开

        # 正常时间标签
        normal_color = theme.accent_warning
        for tag in self._time_tags:
            if visible_start_ms <= tag.ts <= visible_end_ms:
                ratio = (tag.ts - visible_start_ms) / visible_duration
                x = int(ratio * w)
                painter.setPen(QPen(normal_color, 2))
                painter.drawLine(x, int(h * 0.2), x, int(h * 0.8))
                painter.setPen(normal_color)
                if tag.label:
                    display_text = tag.label
                    if tag.ruby:
                        display_text += self._format_ruby_label(tag.ruby)
                    painter.drawText(x + 2, label_y, display_text)
                elif tag.ruby:
                    painter.drawText(x + 2, label_y, self._format_ruby_label(tag.ruby))

        # 非单调时间标签：更高更粗、警告色
        if self._warning_time_tags:
            warn_color = theme.timetag_nonmonotonic
            for tag in self._warning_time_tags:
                if visible_start_ms <= tag.ts <= visible_end_ms:
                    ratio = (tag.ts - visible_start_ms) / visible_duration
                    x = int(ratio * w)
                    painter.setPen(QPen(warn_color, 3))
                    painter.drawLine(x, int(h * 0.1), x, int(h * 0.9))
                    painter.setPen(warn_color)
                    if tag.label:
                        display_text = tag.label
                        if tag.ruby:
                            display_text += self._format_ruby_label(tag.ruby)
                        painter.drawText(x + 2, label_y, display_text)
                    elif tag.ruby:
                        painter.drawText(x + 2, label_y, self._format_ruby_label(tag.ruby))

    def _draw_playhead(self, painter: QPainter, w: int, h: int,
                       visible_start_ms: float, visible_duration_ms: float):
        if visible_duration_ms <= 0:
            return

        if visible_start_ms <= self._current_ms <= visible_start_ms + visible_duration_ms:
            ratio = (self._current_ms - visible_start_ms) / visible_duration_ms
            x = int(ratio * w)

            painter.setPen(QPen(theme.accent_primary, 2))
            painter.drawLine(x, 0, x, h)

            # 播放头三角形标记
            painter.setBrush(QBrush(theme.accent_primary))
            triangle = QPolygon([
                QPoint(x - 6, 0),
                QPoint(x + 6, 0),
                QPoint(x, 10),
            ])
            painter.drawPolygon(triangle)

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None or self._duration_ms <= 0:
            return
        if a0.button() == Qt.MouseButton.LeftButton:
            self._pan_start_x = a0.position().x()
            self._pan_start_scroll = self._scroll_position
            self._is_panning = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None or self._duration_ms <= 0 or self._pan_start_x is None:
            return
        if not (a0.buttons() & Qt.MouseButton.LeftButton):
            return
        delta_x = a0.position().x() - self._pan_start_x
        if not self._is_panning and abs(delta_x) > 4:
            self._is_panning = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._is_panning:
            visible_duration_ms = self._duration_ms / self._zoom_factor
            delta_ms = delta_x / self.width() * visible_duration_ms
            new_scroll = self._clamp_scroll(
                self._pan_start_scroll - delta_ms / self._duration_ms)
            if new_scroll != self._scroll_position:
                self._suspend_auto_scroll()
                self._scroll_position = new_scroll
                self.scroll_position_changed.emit(self._scroll_position)
                self.update()

    def mouseReleaseEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None or self._duration_ms <= 0:
            return
        if a0.button() == Qt.MouseButton.LeftButton:
            if not self._is_panning and self._pan_start_x is not None:
                # 未发生平移，视为点击 → seek
                visible_duration_ms = self._duration_ms / self._zoom_factor
                visible_start_ms = self._scroll_position * self._duration_ms
                ratio = max(0.0, min(1.0, a0.position().x() / self.width()))
                target_ms = int(visible_start_ms + ratio * visible_duration_ms)
                self.seek_requested.emit(target_ms)
            self._pan_start_x = None
            self._is_panning = False
            self.unsetCursor()

    def wheelEvent(self, a0: Optional[QWheelEvent]):
        if a0 is None:
            return
        if not self._zoom_enabled:
            a0.ignore()
            return

        delta = a0.angleDelta().y()
        new_zoom = self._zoom_factor * (1.2 if delta > 0 else 1 / 1.2)
        new_zoom = max(1.0, min(100.0, new_zoom))

        if new_zoom != self._zoom_factor:
            mouse_ratio = a0.position().x() / self.width()
            visible_start = self._scroll_position
            visible_duration = 1.0 / self._zoom_factor
            audio_position = visible_start + mouse_ratio * visible_duration

            self._zoom_factor = new_zoom
            new_visible_duration = 1.0 / self._zoom_factor
            self._scroll_position = self._clamp_scroll(
                audio_position - mouse_ratio * new_visible_duration)

            self.zoom_changed.emit(self._zoom_factor)
            self.scroll_position_changed.emit(self._scroll_position)
            self.update()


# ──────────────────────────────────────────────
# 时间轴控件（包含波形显示 + 缩放控制 + 滚动条）
# ──────────────────────────────────────────────

class TimelineWidget(QWidget):
    """时间轴 - 显示音频波形 + 时间网格 + 时间标签 + 播放位置"""

    seek_requested = pyqtSignal(int)
    waveform_visibility_changed = pyqtSignal(bool)

    # 横向滚动条整数分辨率：把"整段时长"映射为 [0, _SCROLL_SCALE] 个单位，
    # pageStep = 可见时间窗占比（_SCROLL_SCALE / zoom），使滑块长度随缩放自动伸缩。
    _SCROLL_SCALE = 100000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._waveform_visible = True
        self._zoom_enabled = True
        self._init_ui()

    def changeEvent(self, event):
        """切语言时精确 retranslate——本 widget 持有 waveform 缓存，不能整体 rebuild。"""
        from PyQt6.QtCore import QEvent as _QEvent
        if event.type() == _QEvent.Type.LanguageChange:
            if hasattr(self, "switch_waveform"):
                self.switch_waveform.setToolTip(self.tr("波形显示"))
                # SwitchButton 的 On/Off 文本：本来由 FluentTranslator 处理，但
                # pseudo 模式下需要我们的 tr 接管才能显示 ⟦⟧。
                self.switch_waveform.setOnText(self.tr("开"))
                self.switch_waveform.setOffText(self.tr("关"))
            # 音频名标签：用布尔 flag 标记是否是占位，不靠字符串比较——
            # 切到 ja_JP 后 "未加载音频" 变成 "音声未読み込み"，再切到
            # en_US 时字符串比较失败导致不刷新。
            if hasattr(self, "lbl_audio_name") and getattr(self, "_audio_name_is_placeholder", True):
                self.lbl_audio_name.setText(self.tr("未加载音频"))
            # WaveformDisplay 的绘制 placeholder 自带 self.tr，自动跟随
            if hasattr(self, "waveform_display"):
                self.waveform_display.update()
        super().changeEvent(event)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 波形显示区域
        self.waveform_display = WaveformDisplay(self)
        self.waveform_display.seek_requested.connect(self.seek_requested.emit)
        self.waveform_display.zoom_changed.connect(self._on_zoom_changed)
        self.waveform_display.scroll_position_changed.connect(self._on_scroll_changed)
        layout.addWidget(self.waveform_display, stretch=1)

        # 底部控制栏
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(4, 0, 4, 2)
        bottom_layout.setSpacing(8)

        # 缩放控制（对数刻度：滑条 0-10000 线性对应 zoom 1x-100x 对数）
        self.zoom_slider = Slider(Qt.Orientation.Horizontal, self)
        self.zoom_slider.setRange(0, 10000)
        self.zoom_slider.setValue(self._zoom_to_slider(50.0))  # 默认50x
        # slider 用 minimum 而非 fixed：横幅有富余时让它跟着加宽，便于精确缩放
        self.zoom_slider.setMinimumWidth(120)
        self.zoom_slider.setMaximumWidth(220)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        bottom_layout.addWidget(self.zoom_slider)

        self.zoom_label = CaptionLabel("50.0x", self)
        self.zoom_label.setMinimumWidth(40)
        bottom_layout.addWidget(self.zoom_label)

        # 横向滚动条
        self.scroll_bar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.scroll_bar.valueChanged.connect(self._on_scroll_bar_changed)
        bottom_layout.addWidget(self.scroll_bar, stretch=1)
        # 初始按当前缩放设置 pageStep/range/value（滑块长度=可见窗占比）
        self._update_scroll_bar_metrics()

        # 音频名称标签（_audio_name_is_placeholder 标志位让 changeEvent
        # 不靠字符串比较即可判断是否需要重译）
        self.lbl_audio_name = CaptionLabel(self.tr("未加载音频"), self)
        self._audio_name_is_placeholder = True
        # 音频名很长时（长文件名 + 翻译后的"未加载音频"前缀）让标签可压缩；
        # 用 maxWidth 限制上限避免吃掉太多 toolbar 空间。
        self.lbl_audio_name.setMaximumWidth(400)
        self.lbl_audio_name.setMinimumWidth(120)
        from PyQt6.QtCore import Qt as _Qt
        self.lbl_audio_name.setTextInteractionFlags(_Qt.TextInteractionFlag.NoTextInteraction)
        self.lbl_audio_name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bottom_layout.addWidget(self.lbl_audio_name)

        # 波形显示开关
        self.switch_waveform = SwitchButton(self)
        self.switch_waveform.setChecked(True)
        self.switch_waveform.setMinimumWidth(50)
        # 显式覆盖默认 "On"/"Off"——qfluentwidgets 的 FluentTranslator 不一定能
        # 覆盖到 SUG 当前语言（且 pseudo 模式下要让 ⟦⟧ 可视化）。
        self.switch_waveform.setOnText(self.tr("开"))
        self.switch_waveform.setOffText(self.tr("关"))
        self.switch_waveform.setToolTip(self.tr("波形显示"))
        self.switch_waveform.checkedChanged.connect(self._on_waveform_visibility_changed)
        bottom_layout.addWidget(self.switch_waveform)

        layout.addLayout(bottom_layout)

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self.waveform_display.set_duration(ms)

    def set_position(self, ms: int):
        self.waveform_display.set_position(ms)

    def set_time_tags(self, tags: List[Tuple[int, str, int, int, int, bool, Optional[str]]]):
        self.waveform_display.set_time_tags(tags)

    def set_audio_data(self, samples: np.ndarray, sample_rate: int, channels: int):
        self.waveform_display.set_audio_data(samples, sample_rate, channels)

    def clear_audio_data(self):
        self.waveform_display.clear_audio_data()
        self._audio_name_is_placeholder = True
        self.lbl_audio_name.setText(self.tr("未加载音频"))

    def set_audio_name(self, name: str):
        """设置音频文件名称显示"""
        self._audio_name_is_placeholder = False
        self.lbl_audio_name.setText(name)

    def set_playing(self, playing: bool) -> None:
        self.waveform_display.set_playing(playing)

    def set_zoom_enabled(self, enabled: bool) -> None:
        self._zoom_enabled = enabled
        self.waveform_display.set_zoom_enabled(enabled)
        self.zoom_slider.setEnabled(enabled)
        self.zoom_label.setEnabled(enabled)

    # ---- 缩放对数刻度转换（zoom 范围 1x-100x，slider 范围 0-10000）----

    @staticmethod
    def _slider_to_zoom(value: int) -> float:
        """滑条整数值 → 实际放大倍数（对数映射）。"""
        return 100.0 ** (value / 10000.0)

    @staticmethod
    def _zoom_to_slider(zoom: float) -> int:
        """实际放大倍数 → 滑条整数值（对数映射）。"""
        zoom = max(1.0, min(100.0, zoom))
        return int(round(math.log(zoom) / math.log(100.0) * 10000))

    # ---- 回调 ----

    def _on_zoom_changed(self, zoom: float):
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(self._zoom_to_slider(zoom))
        self.zoom_slider.blockSignals(False)
        self.zoom_label.setText(f"{zoom:.1f}x")
        # 缩放变化 → 可见窗占比变化 → 刷新滑块长度
        self._update_scroll_bar_metrics()

    def _on_scroll_changed(self, position: float):
        self.scroll_bar.blockSignals(True)
        self.scroll_bar.setValue(int(round(position * self._SCROLL_SCALE)))
        self.scroll_bar.blockSignals(False)

    def _update_scroll_bar_metrics(self):
        """根据当前缩放刷新横向滚动条的 pageStep / range / value。

        滑块长度 = pageStep / (range跨度 + pageStep) = (1/zoom)，即可见时间窗占
        整段时长的比例：1x 时填满整条，放大越多滑块越短（下限由 QSS min-width 兜底）。
        """
        if not hasattr(self, "scroll_bar"):
            return
        zoom = max(1.0, getattr(self.waveform_display, "_zoom_factor", 1.0))
        page = max(1, int(round(self._SCROLL_SCALE / zoom)))
        position = getattr(self.waveform_display, "_scroll_position", 0.0)
        self.scroll_bar.blockSignals(True)
        self.scroll_bar.setPageStep(page)
        self.scroll_bar.setSingleStep(max(1, page // 10))
        self.scroll_bar.setRange(0, self._SCROLL_SCALE - page)
        self.scroll_bar.setValue(int(round(position * self._SCROLL_SCALE)))
        self.scroll_bar.blockSignals(False)

    def _on_zoom_slider_changed(self, value: int):
        if not self._zoom_enabled:
            return
        self.waveform_display._suspend_auto_scroll()
        zoom = self._slider_to_zoom(value)
        self.waveform_display.set_zoom(zoom)
        self.zoom_label.setText(f"{zoom:.1f}x")
        # set_zoom 不发 zoom_changed 信号（仅 Alt+滚轮缩放才发），故缩放滑条
        # 改变后需在此显式刷新滚动条 pageStep/range，使滑块长度跟随缩放比例。
        self._update_scroll_bar_metrics()

    def _on_scroll_bar_changed(self, value: int):
        self.waveform_display._suspend_auto_scroll()
        position = value / self._SCROLL_SCALE
        self.waveform_display.set_scroll_position(position)

    def _on_waveform_visibility_changed(self, checked: bool):
        self._waveform_visible = checked
        self.waveform_display.setVisible(checked)
        self.waveform_visibility_changed.emit(checked)

    def is_waveform_visible(self) -> bool:
        return self._waveform_visible

    def set_waveform_visible(self, visible: bool):
        self._waveform_visible = visible
        self.switch_waveform.setChecked(visible)
        self.waveform_display.setVisible(visible)
        self.waveform_visibility_changed.emit(visible)
