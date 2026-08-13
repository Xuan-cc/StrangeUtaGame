"""时间轴控件。

显示音频波形、当前播放位置、打轴节奏点分布。
支持缩放和横向滚动，类似视频剪辑软件的时间线。
"""

from __future__ import annotations

import bisect
import math
from typing import List, NamedTuple, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QWheelEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygon,
    QResizeEvent,
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
    # 单击时间标签把手：(line_idx, char_idx, cp_idx, is_sentence_end)
    tag_clicked = pyqtSignal(int, int, int, bool)
    # 拖拽提交：(handles: List[TagHandle], delta_ms: int)
    tags_drag_committed = pyqtSignal(object, int)

    # ── 把手几何与命中常量 ──
    _HANDLE_HALF_W = 5          # 未选中把手命中/绘制半宽（px）
    _HANDLE_SEL_HALF_W = 7      # 选中把手放大后半宽（px）
    _HANDLE_HEIGHT = 9          # 把手块高度（px）
    _MIN_HANDLE_SPACING = 8     # 相邻把手最小间距，低于此值密度门控不绘制把手/不可命中
    _HANDLE_HIT_Y_BAND = 12     # 命中仅在波形竖直中线 ±该像素范围内有效（把手居中，与顶部标签分离）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._current_ms = 0
        self._range_start_ms: Optional[int] = None
        self._range_end_ms: Optional[int] = None
        # 时间标签列表（含模型句柄）；label 仅在该字符第一个 checkpoint 时非空
        self._time_tags: List[TimeTag] = []
        self._warning_time_tags: List[TimeTag] = []
        # 增量更新状态（B：顺序打轴快路径）。_last_tags_input 缓存上次 collect 原始
        # 列表，用于前缀比对识别"末尾追加一个"；其余为增量分类所需的累积量。
        self._last_tags_input: Optional[List[tuple]] = None
        self._running_max_ts: int = -1
        self._seen_char_keys: set = set()
        # 文件序最大键 (line, char, cp)，用于 try_append_tag 判定"末尾追加"
        self._max_file_order_key: Optional[Tuple[int, int, int]] = None
        # Raw entries keyed by stable handle. This lets an interior checkpoint be
        # inserted without collecting every timestamp from the project again.
        self._entries_by_handle: dict[TagHandle, tuple] = {}

        # 音频数据
        self._samples: Optional[np.ndarray] = None
        self._waveform_samples: Optional[np.ndarray] = None
        self._sample_rate: int = 44100
        self._channels: int = 2

        # 缩放和滚动
        self._zoom_factor: float = 50.0  # 默认50x缩放，减少初始渲染压力
        self._zoom_enabled: bool = True
        self._scroll_position: float = 0.0
        self._center_playhead_mode: bool = False
        self._is_playing: bool = False

        # 波形峰值缓存
        self._peaks_cache: Optional[List[tuple]] = None
        self._peaks_cache_key: Optional[tuple] = None  # (width, zoom, scroll, samples_id)
        # 整段音频的多分辨率峰值缓存：bin_size -> (mins, maxs)。同一缩放
        # 粒度只归约一次，播放滚动时仅从缓存中取当前窗口。
        self._waveform_peak_levels: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        # 网格、波形、范围和标签在普通播放期间不变。缓存为静态图层后，
        # 逐帧只需绘制该图层和播放头；缩放/滚动/数据变化时再失效重建。
        self._static_layer: Optional[QPixmap] = None
        self._static_layer_key: Optional[tuple] = None

        # 左键拖动平移状态
        self._pan_start_x: Optional[float] = None
        self._pan_start_scroll: float = 0.0
        self._is_panning: bool = False

        # 时间标签拖拽编辑（总开关 + 选中态 + 拖拽状态）
        self._tag_edit_enabled: bool = True
        # 是否在时间标签上显示本体字符 / 注音文本（两个独立开关）
        self._tag_char_enabled: bool = True
        self._tag_ruby_enabled: bool = True
        self._selected_handles: set = set()           # set[TagHandle]
        self._handle_index: dict = {}                 # TagHandle -> TimeTag，set_time_tags 时重建
        self._hit_boxes: List[Tuple[int, TagHandle, int]] = []  # (x_px, handle, ts)，每次绘制重建
        # 把手按下/拖拽运行态
        self._press_handle: Optional[TagHandle] = None
        self._press_handle_ts: int = 0
        self._press_x: float = 0.0
        self._drag_armed: bool = False                # 按下的是已选中把手 → 允许拖拽
        self._is_dragging_tags: bool = False
        self._drag_anchor_handle: Optional[TagHandle] = None
        self._drag_anchor_ts: int = 0
        self._drag_delta_ms: int = 0

        # 自动滚动挂起（用户手动操作后 6s 内不跟随播放头）
        self._auto_scroll_suspended: bool = False
        self._suspension_timer = QTimer(self)
        self._suspension_timer.setSingleShot(True)
        self._suspension_timer.setInterval(6000)
        self._suspension_timer.timeout.connect(self._resume_auto_scroll)

        self.setMinimumHeight(80)
        self.setMouseTracking(True)

        # 监听主题变化，触发重绘
        theme.changed.connect(self._on_theme_changed)

    def _invalidate_static_layer(self) -> None:
        self._static_layer = None
        self._static_layer_key = None

    def _on_theme_changed(self) -> None:
        self._invalidate_static_layer()
        self.update()

    def resizeEvent(self, event: Optional[QResizeEvent]) -> None:
        self._invalidate_static_layer()
        super().resizeEvent(event)

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
        # Keep this method usable by the lightweight non-QWidget test double.
        self._static_layer = None
        self._static_layer_key = None
        self.update()

    def set_playback_range(
        self, start_ms: Optional[int], end_ms: Optional[int]
    ) -> None:
        self._range_start_ms = start_ms
        self._range_end_ms = end_ms
        self._invalidate_static_layer()
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
        was_playing = self._is_playing
        self._is_playing = bool(playing)
        if playing:
            self._resume_auto_scroll()
            if self._center_playhead_mode:
                self._sync_scroll_to_playhead()
        elif was_playing and self._center_playhead_mode:
            # 离开居中播放态时把虚拟视窗收回音频有效范围，暂停画面不跳回旧位置。
            self._sync_scroll_to_playhead()
        self.update()

    def set_center_playhead_mode(self, enabled: bool) -> None:
        """播放时将播放头锁在中央，由波形和时间标签从右向左滚动。"""
        enabled = bool(enabled)
        if enabled == self._center_playhead_mode:
            return
        self._center_playhead_mode = enabled
        if self._is_playing:
            self._resume_auto_scroll()
            self._sync_scroll_to_playhead()
        self.update()

    def _sync_scroll_to_playhead(self) -> None:
        if self._duration_ms <= 0:
            return
        half_window_ms = self._visible_duration_ms() / 2.0
        self._scroll_position = self._clamp_scroll(
            (self._current_ms - half_window_ms) / self._duration_ms
        )
        self.scroll_position_changed.emit(self._scroll_position)

    def set_position(self, ms: int):
        old_scroll = self._scroll_position
        self._current_ms = ms
        if self._center_playhead_mode and self._is_playing:
            self._sync_scroll_to_playhead()
        # 自动滚动保持播放头可见（用户手动操作后挂起）
        elif self._duration_ms > 0 and self._zoom_factor > 1.0 and not self._auto_scroll_suspended:
            visible_start = self._scroll_position * self._duration_ms
            visible_end = visible_start + self._duration_ms / self._zoom_factor
            if ms < visible_start or ms > visible_end:
                new_scroll = self._clamp_scroll(
                    (ms - self._duration_ms / (2 * self._zoom_factor)) / self._duration_ms
                )
                self._scroll_position = new_scroll
                self.scroll_position_changed.emit(self._scroll_position)
        if self._center_playhead_mode or self._scroll_position != old_scroll:
            self._invalidate_static_layer()
        self.update()

    @log_slow_method(
        "timeline.set_time_tags",
        12,
        lambda self, args, kwargs: {"tags": len(args[0]) if args else 0},
    )
    def set_time_tags(self, tags: List[Tuple[int, str, int, int, int, bool, Optional[str]]]):
        # tags: (timestamp_ms, char_text, line_idx, char_idx, cp_idx, is_sentence_end, ruby_text)
        # 同一字符 (line_idx, char_idx) 的第一个 checkpoint 携带 char 标签，后续不重复；ruby 始终携带

        # ── B 快路径：顺序打轴 = 上次列表 + 末尾恰好追加一个 → O(log N) 增量插入 ──
        prev = self._last_tags_input
        if prev is not None and len(tags) == len(prev) + 1 and tags[:-1] == prev:
            self._append_time_tag(tags[-1])
            self._last_tags_input = tags
            return

        # ── 全量重建（其余所有情况：改时间 / 删除 / 乱序补打 / 结构变更）──
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
        # 重建句柄索引（命中/选中/拖拽以句柄为身份）；丢弃已不存在的选中项
        self._handle_index = {
            t.handle: t for t in (self._time_tags + self._warning_time_tags)
        }
        if self._selected_handles:
            self._selected_handles &= set(self._handle_index)
        # 刷新增量状态（collect 为文件序，末项即文件序最大键）
        self._seen_char_keys = seen_chars
        self._running_max_ts = running_max
        self._last_tags_input = tags
        self._max_file_order_key = (tags[-1][2], tags[-1][3], tags[-1][4]) if tags else None
        self._entries_by_handle = {
            (entry[2], entry[3], entry[4], entry[5]): entry for entry in tags
        }
        WaveformDisplay._invalidate_static_layer(self)
        self.update()

    def _append_time_tag(self, entry: Tuple[int, str, int, int, int, bool, Optional[str]]) -> None:
        """顺序打轴增量插入单个新标签（位于文件序末尾，不影响已有标签归类）。"""
        ts, char, line_idx, char_idx, cp_idx, is_end, ruby_text = entry
        char_key = (line_idx, char_idx)
        label: Optional[str] = char if char_key not in self._seen_char_keys else None
        self._seen_char_keys.add(char_key)
        handle: TagHandle = (line_idx, char_idx, cp_idx, is_end)
        item = TimeTag(ts, label, ruby_text, handle)
        if ts < self._running_max_ts:
            bisect.insort(self._warning_time_tags, item, key=lambda x: x.ts)
        else:
            bisect.insort(self._time_tags, item, key=lambda x: x.ts)
            self._running_max_ts = ts
        self._handle_index[handle] = item
        self._entries_by_handle[handle] = entry
        self._max_file_order_key = (line_idx, char_idx, cp_idx)
        WaveformDisplay._invalidate_static_layer(self)
        self.update()

    def try_append_tag(self, ts: int, char: str, line_idx: int, char_idx: int,
                       cp_idx: int, is_end: bool, ruby: Optional[str]) -> bool:
        """顺序打轴增量追加单个标签（绕过前端 collect + 全量重建）。

        仅当该标签是**新增**且在**文件序上位于所有现有标签之后**时成功：此时它不会改变
        任何已有标签的单调/非单调归类，O(log N) 直接插入并重绘，返回 True。否则不改任何
        状态、返回 False，调用方回退到全量 set_time_tags。
        """
        handle: TagHandle = (line_idx, char_idx, cp_idx, is_end)
        if handle in self._handle_index:
            return False  # 已存在 = 改时间，非新增
        file_key = (line_idx, char_idx, cp_idx)
        if self._max_file_order_key is not None and file_key <= self._max_file_order_key:
            return False  # 非文件序末尾 → 可能改变他者归类，回退
        self._append_time_tag((ts, char, line_idx, char_idx, cp_idx, is_end, ruby))
        # 直接增量后让下次 set_time_tags 走全量重建（避免前缀比对基准失配）
        self._last_tags_input = None
        return True

    def try_add_tag(self, ts: int, char: str, line_idx: int, char_idx: int,
                    cp_idx: int, is_end: bool, ruby: Optional[str]) -> bool:
        """Add a newly timed checkpoint without rescanning the project.

        The common file-tail case keeps the O(log N) append path. Filling an
        earlier gap rebuilds classification from the widget's local raw-entry
        cache because it can change which later timestamps are warnings.
        """
        handle: TagHandle = (line_idx, char_idx, cp_idx, is_end)
        if handle in self._handle_index:
            return False
        if self.try_append_tag(ts, char, line_idx, char_idx, cp_idx, is_end, ruby):
            return True

        entry = (ts, char, line_idx, char_idx, cp_idx, is_end, ruby)
        cached = list(self._entries_by_handle.values())
        cached.append(entry)
        cached.sort(key=lambda item: (item[2], item[3], item[4], item[5]))
        self.set_time_tags(cached)
        return True

    def set_audio_data(self, samples: np.ndarray, sample_rate: int, channels: int):
        self._samples = samples
        # 波形滚动会逐帧重算可见峰值；立体声只在加载时混合一次，避免播放时
        # 每帧扫描整首音频。
        self._waveform_samples = (
            np.mean(samples, axis=1, dtype=np.float32) if channels > 1 else samples
        )
        self._sample_rate = sample_rate
        self._channels = channels
        # 清除波形缓存
        self._peaks_cache = None
        self._peaks_cache_key = None
        self._waveform_peak_levels.clear()
        self._invalidate_static_layer()
        self.update()

    def clear_audio_data(self):
        self._samples = None
        self._waveform_samples = None
        self._sample_rate = 0
        self._channels = 0
        self._peaks_cache = None
        self._peaks_cache_key = None
        self._waveform_peak_levels.clear()
        self._invalidate_static_layer()
        self.update()

    def set_zoom(self, zoom: float):
        self._zoom_factor = max(1.0, min(100.0, zoom))
        # 缩放变化后重新 clamp，避免当前滚动位置超出新的有效范围
        self._scroll_position = self._clamp_scroll(self._scroll_position)
        self._invalidate_static_layer()
        self.update()

    def set_zoom_enabled(self, enabled: bool) -> None:
        self._zoom_enabled = enabled

    def set_scroll_position(self, position: float):
        self._scroll_position = self._clamp_scroll(position)
        self._invalidate_static_layer()
        self.update()

    # ── 时间标签拖拽编辑：总开关 + 选中态 ──

    def set_tag_edit_enabled(self, enabled: bool) -> None:
        """总开关：关闭时回退为旧的纯显示模式（不画把手/不可命中/无选中）。"""
        enabled = bool(enabled)
        if enabled == self._tag_edit_enabled:
            return
        self._tag_edit_enabled = enabled
        if not enabled:
            self._reset_drag()
            self._selected_handles.clear()
            self._press_handle = None
            self._drag_armed = False
            self.unsetCursor()
        self._invalidate_static_layer()
        self.update()

    def set_tag_char_enabled(self, enabled: bool) -> None:
        """是否在时间标签上显示本体字符文本（独立于拖拽编辑总开关）。"""
        enabled = bool(enabled)
        if enabled != self._tag_char_enabled:
            self._tag_char_enabled = enabled
            self._invalidate_static_layer()
            self.update()

    def set_tag_ruby_enabled(self, enabled: bool) -> None:
        """是否在时间标签上显示注音(ruby)文本（独立于拖拽编辑总开关）。"""
        enabled = bool(enabled)
        if enabled != self._tag_ruby_enabled:
            self._tag_ruby_enabled = enabled
            self._invalidate_static_layer()
            self.update()

    def clear_tag_selection(self) -> None:
        """清空选中集（外部数据变更后调用，避免悬空句柄）。"""
        if self._is_dragging_tags:
            self._reset_drag()
        if self._selected_handles:
            self._selected_handles.clear()
            self._invalidate_static_layer()
            self.update()

    def _reset_drag(self) -> None:
        self._is_dragging_tags = False
        self._drag_anchor_handle = None
        self._drag_anchor_ts = 0
        self._drag_delta_ms = 0
        self._press_handle = None
        self._drag_armed = False

    # ── 视窗 / 坐标换算 ──

    def _visible_start_ms(self) -> float:
        if self._center_playhead_mode and self._is_playing:
            # 不 clamp：开头/结尾保留半屏空白，播放头才能在整段播放期间严格居中。
            return self._current_ms - self._visible_duration_ms() / 2.0
        return self._scroll_position * self._duration_ms

    def _visible_duration_ms(self) -> float:
        return self._duration_ms / self._zoom_factor

    def _ts_to_x(self, ts: float, visible_start_ms: float, visible_duration_ms: float, w: int) -> int:
        if visible_duration_ms <= 0:
            return 0
        return int((ts - visible_start_ms) / visible_duration_ms * w)

    def _x_to_time(self, x: float, width: Optional[int] = None) -> int:
        """把波形横坐标换算为时间；居中播放时同样使用滚动中的虚拟视窗。"""
        w = self.width() if width is None else width
        if self._duration_ms <= 0 or w <= 0:
            return 0
        ratio = max(0.0, min(1.0, x / w))
        target_ms = self._visible_start_ms() + ratio * self._visible_duration_ms()
        return max(0, min(self._duration_ms, int(round(target_ms))))

    def _draw_ts(self, tag: "TimeTag") -> int:
        """拖拽预览：被选中且正在拖拽的标签按 delta 平移其显示时间戳。"""
        if self._is_dragging_tags and tag.handle in self._selected_handles:
            return tag.ts + self._drag_delta_ms
        return tag.ts

    def _visible_slice(self, tag_list: List["TimeTag"], vs: float, ve: float) -> List["TimeTag"]:
        """A：返回 [vs, ve] 可见窗内的标签子列表（列表按 ts 升序，二分截取）。

        拖拽态下选中标签会按 delta 偏移，可能移入/移出视窗，二分会漏，故回退全量
        （由 _draw_line 的 ts 范围判断兜底）；拖拽是短暂交互态，全量遍历可接受。
        """
        if self._is_dragging_tags:
            return tag_list
        lo = bisect.bisect_left(tag_list, vs, key=lambda t: t.ts)
        hi = bisect.bisect_right(tag_list, ve, key=lambda t: t.ts)
        return tag_list[lo:hi]

    def _hit_test_handle(self, x: float, y: float):
        """命中顶部把手块：返回 (handle, ts, x_px) 或 None。仅在编辑开启且 y 在顶部带内。"""
        if not self._tag_edit_enabled:
            return None
        if abs(y - self.height() / 2) > self._HANDLE_HIT_Y_BAND:
            return None
        best = None
        best_dx = self._HANDLE_HALF_W + 1
        for hx, handle, ts in self._hit_boxes:
            dx = abs(x - hx)
            if dx <= self._HANDLE_HALF_W and dx < best_dx:
                best = (handle, ts, hx)
                best_dx = dx
        return best

    def _clamp_drag_delta(self, delta: int) -> int:
        """组级 0 夹紧：保证所有选中标签的显示时间戳平移后不小于 0（保持刚性）。"""
        if not self._selected_handles:
            return delta
        sel_ts = [
            self._handle_index[h].ts
            for h in self._selected_handles
            if h in self._handle_index
        ]
        if not sel_ts:
            return delta
        return max(delta, -min(sel_ts))

    @log_slow_method(
        "timeline.compute_waveform_peaks",
        20,
        lambda self, args, kwargs: {
            "width": args[0] if args else kwargs.get("width"),
            "zoom": f"{self._zoom_factor:.2f}",
        },
    )
    def _compute_waveform_peaks(self, width: int) -> Optional[List[tuple]]:
        """从整段峰值层中截取当前窗口；每种采样粒度只计算一次。"""
        if self._samples is None or self._duration_ms <= 0 or width <= 0:
            return None

        # 居中播放时 visible_start 随播放位置逐帧变化，必须进入缓存键。
        samples_id = id(self._samples)
        visible_start_ms = self._visible_start_ms()
        cache_key = (width, self._zoom_factor, visible_start_ms, samples_id)

        if self._peaks_cache_key == cache_key and self._peaks_cache is not None:
            return self._peaks_cache

        samples = self._waveform_samples
        if samples is None:
            return None

        visible_duration_ms = self._visible_duration_ms()
        samples_per_pixel = max(
            1.0, visible_duration_ms / 1000.0 * self._sample_rate / width
        )
        # 量化到 2 的幂，缩放/resize 时可复用少量固定层级；选不大于目标
        # 粒度的层，确保缓存不会吃掉瞬态峰值。
        bin_size = 1 << max(0, int(math.floor(math.log2(samples_per_pixel))))
        level_min, level_max = self._waveform_peak_level(bin_size)

        ms_per_pixel = visible_duration_ms / width
        left_times = visible_start_ms + np.arange(width, dtype=np.float64) * ms_per_pixel
        center_times = left_times + ms_per_pixel * 0.5
        right_times = left_times + ms_per_pixel
        valid = (right_times > 0.0) & (left_times < self._duration_ms)

        def _level_indices(times: np.ndarray) -> np.ndarray:
            sample_indices = np.floor(
                np.clip(times, 0.0, float(self._duration_ms))
                / 1000.0
                * self._sample_rate
            ).astype(np.int64)
            return np.clip(sample_indices // bin_size, 0, len(level_min) - 1)

        left_idx = _level_indices(left_times)
        center_idx = _level_indices(center_times)
        # 右边界减一个极小量，避免恰好落入下一个像素区间。
        right_idx = _level_indices(np.nextafter(right_times, left_times))
        mins = np.minimum.reduce(
            (level_min[left_idx], level_min[center_idx], level_min[right_idx])
        )
        maxs = np.maximum.reduce(
            (level_max[left_idx], level_max[center_idx], level_max[right_idx])
        )
        mins = np.where(valid, mins, 0.0)
        maxs = np.where(valid, maxs, 0.0)
        peaks = [(float(lo), float(hi)) for lo, hi in zip(mins, maxs)]

        # 更新缓存
        self._peaks_cache = peaks
        self._peaks_cache_key = cache_key

        return peaks

    def _waveform_peak_level(self, bin_size: int) -> tuple[np.ndarray, np.ndarray]:
        """惰性生成整段音频的一个峰值层，后续播放帧直接复用。"""
        cached = self._waveform_peak_levels.get(bin_size)
        if cached is not None:
            return cached
        samples = self._waveform_samples
        if samples is None or len(samples) == 0:
            empty = np.zeros(1, dtype=np.float32)
            return empty, empty
        full_bins, remainder = divmod(len(samples), bin_size)
        if full_bins:
            main = samples[:full_bins * bin_size].reshape(full_bins, bin_size)
            mins = np.min(main, axis=1).astype(np.float32, copy=False)
            maxs = np.max(main, axis=1).astype(np.float32, copy=False)
        else:
            mins = np.empty(0, dtype=np.float32)
            maxs = np.empty(0, dtype=np.float32)
        if remainder:
            tail = samples[full_bins * bin_size:]
            mins = np.append(mins, np.float32(np.min(tail)))
            maxs = np.append(maxs, np.float32(np.max(tail)))
        level = (mins, maxs)
        self._waveform_peak_levels[bin_size] = level
        return level

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
        w, h = self.width(), self.height()

        if self._duration_ms <= 0:
            painter.fillRect(self.rect(), theme.waveform_bg)
            painter.setPen(theme.text_hint)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, self.tr("请加载音频文件")
            )
            return

        visible_start_ms = self._visible_start_ms()
        visible_duration_ms = self._duration_ms / self._zoom_factor
        visible_end_ms = visible_start_ms + visible_duration_ms
        device_pixel_ratio = max(1.0, float(self.devicePixelRatioF()))

        layer_key = (
            w,
            h,
            device_pixel_ratio,
            visible_start_ms,
            visible_duration_ms,
            self._range_start_ms,
            self._range_end_ms,
            self._tag_edit_enabled,
            self._tag_char_enabled,
            self._tag_ruby_enabled,
            frozenset(self._selected_handles),
            self._drag_delta_ms if self._is_dragging_tags else 0,
        )
        if self._static_layer is None or self._static_layer_key != layer_key:
            self._static_layer = self._render_static_layer(
                w, h, visible_start_ms, visible_end_ms, visible_duration_ms
            )
            self._static_layer_key = layer_key
        painter.drawPixmap(0, 0, self._static_layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_playhead(painter, w, h, visible_start_ms, visible_duration_ms)
        self._draw_drag_badge(painter, w, h, visible_start_ms, visible_duration_ms)

    def _render_static_layer(
        self,
        w: int,
        h: int,
        visible_start_ms: float,
        visible_end_ms: float,
        visible_duration_ms: float,
    ) -> QPixmap:
        # QPixmap dimensions are physical pixels. Without a matching DPR the
        # logical-size cache is stretched by Windows at 125%/150%/200%, which
        # blurs both the waveform and the small ruby labels.
        dpr = max(1.0, float(self.devicePixelRatioF()))
        layer = QPixmap(
            max(1, int(math.ceil(w * dpr))),
            max(1, int(math.ceil(h * dpr))),
        )
        layer.setDevicePixelRatio(dpr)
        layer.fill(theme.waveform_bg)
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        self._draw_time_grid(painter, w, h, visible_start_ms, visible_end_ms)
        self._draw_waveform(painter, w, h)
        self._draw_playback_range(
            painter, w, h, visible_start_ms, visible_duration_ms
        )
        self._draw_time_tags(painter, w, h, visible_start_ms, visible_end_ms)
        painter.end()
        return layer

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

    def _draw_playback_range(
        self,
        painter: QPainter,
        w: int,
        h: int,
        visible_start_ms: float,
        visible_duration_ms: float,
    ) -> None:
        """Shade playback-excluded areas and draw the locked A/B boundaries."""
        if visible_duration_ms <= 0:
            return
        visible_end_ms = visible_start_ms + visible_duration_ms

        shade = QColor(theme.waveform_bg)
        shade.setAlpha(150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shade))

        if self._range_start_ms is not None:
            start_x = self._ts_to_x(
                self._range_start_ms, visible_start_ms, visible_duration_ms, w
            )
            if self._range_start_ms > visible_start_ms:
                painter.drawRect(0, 0, min(w, max(0, start_x)), h)
        if self._range_end_ms is not None:
            end_x = self._ts_to_x(
                self._range_end_ms, visible_start_ms, visible_duration_ms, w
            )
            if self._range_end_ms < visible_end_ms:
                painter.drawRect(max(0, min(w, end_x)), 0, w, h)

        def draw_boundary(ms: Optional[int], color, label: str) -> None:
            if ms is None or not (visible_start_ms <= ms <= visible_end_ms):
                return
            x = self._ts_to_x(ms, visible_start_ms, visible_duration_ms, w)
            painter.setPen(QPen(color, 3))
            painter.drawLine(x, 0, x, h)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRect(max(0, x - 8), 1, 16, 15), 3, 3)
            painter.setPen(theme.waveform_bg)
            painter.drawText(QRect(max(0, x - 8), 1, 16, 15), Qt.AlignmentFlag.AlignCenter, label)

        draw_boundary(self._range_start_ms, theme.status_complete, "A")
        draw_boundary(self._range_end_ms, theme.accent_warning, "B")

    @staticmethod
    def _format_ruby_label(ruby_text: str) -> str:
        if len(ruby_text) > 4:
            return f"「{ruby_text[:4]}...」"
        return f"「{ruby_text}」"

    def _draw_label_plate(self, painter: QPainter, x: int, label_y: int,
                          fm, text: str, color, text_w: int) -> None:
        """带半透明背景底片绘制标签文字：在文字后铺一层接近不透明的背景色底，
        把文字从蓝色波形上分离出来，可读性显著优于细光晕，且仍透出少量波形。
        """
        top = label_y - fm.ascent()
        plate = QColor(theme.waveform_bg)
        plate.setAlpha(225)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(plate))
        painter.drawRoundedRect(QRect(x - 2, top - 1, text_w + 4, fm.height() + 1), 2, 2)
        painter.setPen(color)
        painter.drawText(x, label_y, text)

    def _draw_time_tags(self, painter: QPainter, w: int, h: int,
                        visible_start_ms: float, visible_end_ms: float):
        self._hit_boxes = []
        visible_duration = visible_end_ms - visible_start_ms
        if visible_duration <= 0:
            return

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        fm = painter.fontMetrics()
        label_y = fm.ascent() + 1  # 所有标签统一贴顶显示，竖线在其下方展开
        # 把手已移至波形竖直中央，与顶部标签天然分离，标签无需再右移让位
        label_dx = 2
        show_char = self._tag_char_enabled
        # 注音显示以字符显示为前提：字符关则注音也不显示
        show_ruby = self._tag_ruby_enabled and self._tag_char_enabled

        normal_color = theme.accent_warning
        warn_color = theme.timetag_nonmonotonic

        # ── pass 1：竖线（语义色不变，拖拽中的选中标签按 delta 平移）；收集可见标签 ──
        # entries：(x, tag, is_warning, color)
        entries: List[Tuple[int, TimeTag, bool, object]] = []

        def _draw_line(tag: TimeTag, color, width_px: int, y_top_ratio: float, y_bot_ratio: float, is_warning: bool):
            ts = self._draw_ts(tag)
            if not (visible_start_ms <= ts <= visible_end_ms):
                return
            x = self._ts_to_x(ts, visible_start_ms, visible_duration, w)
            painter.setPen(QPen(color, width_px))
            painter.drawLine(x, int(h * y_top_ratio), x, int(h * y_bot_ratio))
            entries.append((x, tag, is_warning, color))

        # A：只遍历可见窗内的标签（列表已按 ts 排序，二分定位），把每帧 O(N) 降到 O(可见数)
        for tag in self._visible_slice(self._time_tags, visible_start_ms, visible_end_ms):
            _draw_line(tag, normal_color, 2, 0.2, 0.8, False)
        for tag in self._visible_slice(self._warning_time_tags, visible_start_ms, visible_end_ms):
            _draw_line(tag, warn_color, 3, 0.1, 0.9, True)

        # ── pass 2：标签文字按优先级放置，避免重叠（先画，把手随后覆盖其上）──
        # 优先级：选中的标签无条件显示；其余按 x 从左到右贪心，左侧优先占位。
        labels = []  # (a, b, tag, color, text)
        for x, tag, is_warning, color in entries:
            text = ""
            if show_char and tag.label:
                text += tag.label
            if show_ruby and tag.ruby:
                text += self._format_ruby_label(tag.ruby)
            if not text:
                continue
            a = x + label_dx
            b = a + fm.horizontalAdvance(text)
            labels.append((a, b, tag, color, text))

        for a, b, tag, color, text in self._resolve_label_layout(labels):
            self._draw_label_plate(painter, a, label_y, fm, text, color, b - a)

        # ── pass 3：把手居于波形竖直中央（仅编辑开启）。与顶部标签天然分离。
        # 密度门控：相邻把手过近则跳过（不绘制 / 不可命中）──
        if self._tag_edit_enabled:
            sel_color = theme.accent_secondary
            cy = h // 2
            last_x = None
            for x, tag, is_warning, color in sorted(entries, key=lambda e: e[0]):
                if last_x is not None and (x - last_x) < self._MIN_HANDLE_SPACING:
                    continue
                last_x = x
                selected = tag.handle in self._selected_handles
                half = self._HANDLE_SEL_HALF_W if selected else self._HANDLE_HALF_W
                rect = QRect(x - half, cy - self._HANDLE_HEIGHT // 2, half * 2, self._HANDLE_HEIGHT)
                # 实心色块（选中=蓝，未选中=标签语义色）+ 背景色描边，使其在波形上清晰可辨
                painter.setPen(QPen(theme.waveform_bg, 1))
                painter.setBrush(QBrush(sel_color if selected else color))
                painter.drawRect(rect)
                self._hit_boxes.append((x, tag.handle, tag.ts))

    def _resolve_label_layout(self, labels):
        """标签防重叠优先级布局。

        labels: ``(a, b, tag, color, text)`` 列表（a/b 为标签左右像素边界）。
        规则：选中的标签无条件保留；其余按左边界从左到右贪心占位，与已占用区间
        重叠则丢弃（左侧优先）。返回需要绘制的标签子集。
        """
        occupied: List[Tuple[int, int]] = []

        def _fits(a: int, b: int) -> bool:
            return all(b < oa or a > ob for oa, ob in occupied)

        out = []
        # 选中优先：无条件保留并占位
        for item in labels:
            a, b, tag = item[0], item[1], item[2]
            if tag.handle in self._selected_handles:
                out.append(item)
                occupied.append((a, b))
        # 其余：从左到右贪心，重叠则跳过（左侧优先）
        for item in sorted(labels, key=lambda L: L[0]):
            a, b, tag = item[0], item[1], item[2]
            if tag.handle in self._selected_handles:
                continue
            if _fits(a, b):
                out.append(item)
                occupied.append((a, b))
        return out

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

    @staticmethod
    def _format_ms(ms: int) -> str:
        ms = max(0, int(ms))
        m, s = divmod(ms // 1000, 60)
        return f"{m}:{s:02d}.{ms % 1000:03d}"

    def _draw_drag_badge(self, painter: QPainter, w: int, h: int,
                         visible_start_ms: float, visible_duration_ms: float):
        """拖拽时显示偏差值徽标（主：有符号 delta；辅：锚点绝对时间）。"""
        if not self._is_dragging_tags:
            return
        delta = self._drag_delta_ms
        sign = "+" if delta >= 0 else "−"  # 减号 U+2212
        line1 = f"Δ {sign}{abs(delta)} ms"
        anchor_ts = self._drag_anchor_ts + delta
        line2 = f"→ {self._format_ms(anchor_ts)}" if self._drag_anchor_handle is not None else None

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = max(fm.horizontalAdvance(line1), fm.horizontalAdvance(line2 or ""))
        line_h = fm.height()
        pad = 4
        box_w = text_w + pad * 2
        box_h = line_h * (2 if line2 else 1) + pad * 2

        # 徽标定位在锚点当前 x 的右下角，夹到控件内
        anchor_x = self._ts_to_x(anchor_ts, visible_start_ms, visible_duration_ms, w)
        bx = anchor_x + 8
        by = h // 2 + 12
        bx = max(2, min(bx, w - box_w - 2))
        by = max(2, min(by, h - box_h - 2))

        bg = QColor(0, 0, 0, 170)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(QRect(bx, by, box_w, box_h), 3, 3)
        painter.setPen(theme.accent_primary)
        ty = by + pad + fm.ascent()
        painter.drawText(bx + pad, ty, line1)
        if line2:
            painter.drawText(bx + pad, ty + line_h, line2)

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None or self._duration_ms <= 0:
            return
        if a0.button() != Qt.MouseButton.LeftButton:
            return
        x = a0.position().x()
        y = a0.position().y()
        # 优先命中顶部把手（编辑模式）：命中则进入把手交互，不启动 pan
        if self._tag_edit_enabled:
            hit = self._hit_test_handle(x, y)
            if hit is not None:
                handle, ts, _hx = hit
                ctrl = bool(a0.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self._press_handle = handle
                self._press_handle_ts = ts
                self._press_x = x
                # 已选中且非 Ctrl → 允许拖动；否则按下仅用于点击选中
                self._drag_armed = (handle in self._selected_handles) and not ctrl
                return
        # 回退：原 pan/seek 预备态
        self._pan_start_x = x
        self._pan_start_scroll = self._scroll_position
        self._is_panning = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None or self._duration_ms <= 0:
            return
        x = a0.position().x()
        y = a0.position().y()
        if not (a0.buttons() & Qt.MouseButton.LeftButton):
            # 悬停光标反馈：把手上显示可点光标
            if self._tag_edit_enabled and self._hit_test_handle(x, y) is not None:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            return
        # 把手按下中：拖拽分支
        if self._press_handle is not None:
            if not self._is_dragging_tags:
                if self._drag_armed and abs(x - self._press_x) > 4:
                    self._is_dragging_tags = True
                    self._drag_anchor_handle = self._press_handle
                    self._drag_anchor_ts = self._press_handle_ts
                    self._suspend_auto_scroll()
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    return
            visible_duration_ms = self._visible_duration_ms()
            raw_delta = (x - self._press_x) / self.width() * visible_duration_ms
            self._drag_delta_ms = self._clamp_drag_delta(int(round(raw_delta)))
            self.update()
            return
        # 回退：原 pan 平移
        if self._pan_start_x is None:
            return
        delta_x = x - self._pan_start_x
        if not self._is_panning and abs(delta_x) > 4:
            self._is_panning = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._is_panning:
            visible_duration_ms = self._visible_duration_ms()
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
        if a0.button() != Qt.MouseButton.LeftButton:
            return
        # 把手拖拽提交
        if self._is_dragging_tags:
            if self._drag_delta_ms != 0 and self._selected_handles:
                # 锚点（被按住拖动的把手）置于首位，供下游 preview 选中同步
                handles = list(self._selected_handles)
                anchor = self._drag_anchor_handle
                if anchor in self._selected_handles:
                    handles.remove(anchor)
                    handles.insert(0, anchor)
                self.tags_drag_committed.emit(handles, self._drag_delta_ms)
            self._reset_drag()
            self.unsetCursor()
            self.update()
            return
        # 把手单击（未拖动）：选中 / 多选切换
        if self._press_handle is not None:
            handle = self._press_handle
            ts = self._press_handle_ts
            ctrl = bool(a0.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if ctrl:
                if handle in self._selected_handles:
                    self._selected_handles.discard(handle)
                else:
                    self._selected_handles.add(handle)
            else:
                self._selected_handles = {handle}
                self.seek_requested.emit(ts)
                self.tag_clicked.emit(handle[0], handle[1], handle[2], handle[3])
            self._press_handle = None
            self._drag_armed = False
            self.update()
            return
        # 回退：原 seek
        if not self._is_panning and self._pan_start_x is not None:
            self.seek_requested.emit(self._x_to_time(a0.position().x()))
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
        if delta == 0:
            a0.ignore()
            return

        if a0.modifiers() & Qt.KeyboardModifier.ControlModifier:
            new_zoom = self._zoom_factor * (1.2 if delta > 0 else 1 / 1.2)
            new_zoom = max(1.0, min(100.0, new_zoom))

            if new_zoom != self._zoom_factor:
                mouse_ratio = a0.position().x() / max(1, self.width())
                visible_start = self._scroll_position
                visible_duration = 1.0 / self._zoom_factor
                audio_position = visible_start + mouse_ratio * visible_duration

                self._suspend_auto_scroll()
                self._zoom_factor = new_zoom
                new_visible_duration = 1.0 / self._zoom_factor
                self._scroll_position = self._clamp_scroll(
                    audio_position - mouse_ratio * new_visible_duration)

                self.zoom_changed.emit(self._zoom_factor)
                self.scroll_position_changed.emit(self._scroll_position)
                self.update()
        else:
            # 一格滚轮平移可见窗口的 10%；向上滚回到更早时间，向下滚到更晚时间。
            wheel_steps = delta / 120.0
            new_scroll = self._clamp_scroll(
                self._scroll_position - wheel_steps / (self._zoom_factor * 10.0)
            )
            if new_scroll != self._scroll_position:
                self._suspend_auto_scroll()
                self._scroll_position = new_scroll
                self.scroll_position_changed.emit(self._scroll_position)
                self.update()

        a0.accept()


# ──────────────────────────────────────────────
# 时间轴控件（包含波形显示 + 缩放控制 + 滚动条）
# ──────────────────────────────────────────────

class TimelineWidget(QWidget):
    """时间轴 - 显示音频波形 + 时间网格 + 时间标签 + 播放位置"""

    seek_requested = pyqtSignal(int)
    waveform_visibility_changed = pyqtSignal(bool)
    tag_clicked = pyqtSignal(int, int, int, bool)
    tags_drag_committed = pyqtSignal(object, int)

    # 横向滚动条整数分辨率：把"整段时长"映射为 [0, _SCROLL_SCALE] 个单位，
    # pageStep = 可见时间窗占比（_SCROLL_SCALE / zoom），使滑块长度随缩放自动伸缩。
    _SCROLL_SCALE = 100000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._waveform_visible = True
        self._zoom_enabled = True
        # 程序化 setValue 时抑制缩放滑条回调，避免反馈环（但不能 blockSignals，
        # 否则 qfluentwidgets Slider 的 valueChanged→_adjustHandlePos 抓手不跟动）
        self._suppress_zoom_slider = False
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
        self.waveform_display.tag_clicked.connect(self.tag_clicked.emit)
        self.waveform_display.tags_drag_committed.connect(self.tags_drag_committed.emit)
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

    def set_playback_range(
        self, start_ms: Optional[int], end_ms: Optional[int]
    ) -> None:
        self.waveform_display.set_playback_range(start_ms, end_ms)

    def set_time_tags(self, tags: List[Tuple[int, str, int, int, int, bool, Optional[str]]]):
        self.waveform_display.set_time_tags(tags)

    def set_tag_edit_enabled(self, enabled: bool) -> None:
        self.waveform_display.set_tag_edit_enabled(enabled)

    def try_append_tag(self, ts: int, char: str, line_idx: int, char_idx: int,
                       cp_idx: int, is_end: bool, ruby: Optional[str]) -> bool:
        return self.waveform_display.try_append_tag(
            ts, char, line_idx, char_idx, cp_idx, is_end, ruby)

    def try_add_tag(self, ts: int, char: str, line_idx: int, char_idx: int,
                    cp_idx: int, is_end: bool, ruby: Optional[str]) -> bool:
        return self.waveform_display.try_add_tag(
            ts, char, line_idx, char_idx, cp_idx, is_end, ruby)

    def set_tag_char_enabled(self, enabled: bool) -> None:
        self.waveform_display.set_tag_char_enabled(enabled)

    def set_tag_ruby_enabled(self, enabled: bool) -> None:
        self.waveform_display.set_tag_ruby_enabled(enabled)

    def clear_tag_selection(self) -> None:
        self.waveform_display.clear_tag_selection()

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

    def set_center_playhead_mode(self, enabled: bool) -> None:
        self.waveform_display.set_center_playhead_mode(enabled)

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
        # 用标志位而非 blockSignals：要让 Slider 的 valueChanged→_adjustHandlePos
        # 正常触发（抓手跟随），仅抑制我们自己的 _on_zoom_slider_changed 回环。
        self._suppress_zoom_slider = True
        self.zoom_slider.setValue(self._zoom_to_slider(zoom))
        self._suppress_zoom_slider = False
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
        if self._suppress_zoom_slider or not self._zoom_enabled:
            return
        self.waveform_display._suspend_auto_scroll()
        zoom = self._slider_to_zoom(value)
        self.waveform_display.set_zoom(zoom)
        self.zoom_label.setText(f"{zoom:.1f}x")
        # set_zoom 不发 zoom_changed 信号（仅 Ctrl+滚轮缩放才发），故缩放滑条
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
