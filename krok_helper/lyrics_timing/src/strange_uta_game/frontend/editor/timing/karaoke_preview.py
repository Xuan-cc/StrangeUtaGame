"""卡拉OK 歌词预览控件。

- 实时走字高亮 / Ruby 注音同步 / 连词平滑渲染
- 支持鼠标拖拽划词选区 + 右键菜单
- 渲染状态与 ``EditorInterface`` 通过信号耦合
"""

from __future__ import annotations

from bisect import bisect_right
import sys
from typing import Optional

from PyQt6.QtCore import QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QScrollBar, QWidget
from qfluentwidgets import Action, RoundMenu

from strange_uta_game.backend.domain import Character, Project, Ruby
from strange_uta_game.frontend.theme import theme


# ──────────────────────────────────────────────
# 卡拉OK 歌词预览
# ──────────────────────────────────────────────


def _draw_split_text(
    painter: "QPainter",
    x: int,
    baseline: int,
    ch: str,
    colors: "list[QColor]",
    y_top: int,
    y_bottom: int,
) -> None:
    """绘制文字，支持分色（split）模式。

    单色时直接 setPen+drawText，无额外开销。
    分色时对每个色带用 IntersectClip，可在已有 wipe clip 内正确工作。
    y_top/y_bottom 为本行实际墨水上下边界（屏幕坐标），由调用方按行预计算传入，
    保证同行所有字符的色带分界线位于同一视觉高度。
    """
    n = len(colors)
    if n <= 1:
        painter.setPen(colors[0] if colors else QColor("white"))
        painter.drawText(x, baseline, ch)
        return
    total_h = y_bottom - y_top
    if total_h <= 0:
        painter.setPen(colors[0])
        painter.drawText(x, baseline, ch)
        return
    for i, color in enumerate(colors):
        y0 = y_top + int(i * total_h / n)
        y1 = y_top + int((i + 1) * total_h / n) + 1  # +1 防止浮点精度产生空隙
        painter.save()
        painter.setClipRect(
            QRect(-32768, y0, 65536, y1 - y0),
            Qt.ClipOperation.IntersectClip,
        )
        painter.setPen(color)
        painter.drawText(x, baseline, ch)
        painter.restore()


def _anchor_ratio(anchors: list[int], current_time: int) -> float:
    """根据锚点序列计算总 wipe 进度比例。

    anchors 形如 ``[ts_0, ts_1, ..., ts_N]``，共 N+1 个时间戳，
    把演唱划成 N 个 part 段。第 i 段区间为 ``[ts_i, ts_{i+1})``。

    返回值 = ``(i + segment_ratio) / N``，其中 i 为 current_time 落入的段索引，
    segment_ratio 为段内线性比例。clamp 到 [0.0, 1.0]。

    - ``current_time < anchors[0]`` → 0.0
    - ``current_time >= anchors[-1]`` → 1.0
    - 段时长 <= 0（异常数据）→ 该段视为瞬时完成
    - 锚点数 < 2 → 1.0（无法分段，按已完成处理；调用方负责回退）
    """
    n = len(anchors) - 1
    if n <= 0:
        return 1.0
    if current_time < anchors[0]:
        return 0.0
    if current_time >= anchors[-1]:
        return 1.0
    # 线性扫描即可（part 数极少，N <= 10 量级）
    for i in range(n):
        seg_start = anchors[i]
        seg_end = anchors[i + 1]
        if current_time < seg_end:
            seg_dur = seg_end - seg_start
            seg_ratio = (current_time - seg_start) / seg_dur if seg_dur > 0 else 1.0
            seg_ratio = max(0.0, min(1.0, seg_ratio))
            return (i + seg_ratio) / n
    return 1.0


def _anchor_segment(anchors: list[int], current_time: int) -> tuple[int, float, int]:
    """与 _anchor_ratio 同义，但返回 (段索引 i, 段内比例 seg_ratio, 段数 N)。

    - 已完成（>=anchors[-1]）→ (N-1, 1.0, N)
    - 未开始（<anchors[0]）→ (0, 0.0, N)
    - anchors 不足 2 元素 → (0, 1.0, 0)
    """
    n = len(anchors) - 1
    if n <= 0:
        return (0, 1.0, 0)
    if current_time < anchors[0]:
        return (0, 0.0, n)
    if current_time >= anchors[-1]:
        return (n - 1, 1.0, n)
    for i in range(n):
        seg_start = anchors[i]
        seg_end = anchors[i + 1]
        if current_time < seg_end:
            seg_dur = seg_end - seg_start
            seg_ratio = (current_time - seg_start) / seg_dur if seg_dur > 0 else 1.0
            seg_ratio = max(0.0, min(1.0, seg_ratio))
            return (i, seg_ratio, n)
    return (n - 1, 1.0, n)


def _piecewise_wipe_ratio(
    segments: list[tuple[int, int, float, float]], current_time: int
) -> float:
    """按分段时间轴 + 累计墨水占比返回当前 wipe 比例 ∈ [0,1]。

    每个 segment = ``(t_start, t_end, w_start, w_end)``，``w`` 为该段结束时
    累计推进的墨水占比（[0,1]，单调不减）。

    - 空 part（无墨水）：``w_start == w_end`` —— 消耗时间但不推进 wipe。
    - 段间空隙（``current_time`` 落在上一段 end 与下一段 start 之间）：
      保持上一段 ``w_end``，不推进。
    - 未开始 → 0.0；已结束 → 1.0。
    """
    if not segments:
        return 1.0
    if current_time < segments[0][0]:
        return 0.0
    if current_time >= segments[-1][1]:
        return 1.0
    last_w = 0.0
    for t0, t1, w0, w1 in segments:
        if current_time < t0:
            return last_w
        if current_time < t1:
            f = (current_time - t0) / (t1 - t0) if t1 > t0 else 1.0
            return w0 + (w1 - w0) * f
        last_w = w1
    return 1.0


def _guide_colors(colors: "list[QColor]", alpha: float) -> "list[QColor]":
    """走字预览指引：将颜色列表整体套用透明度（分色逐色保留，仅改 alpha）。"""
    out = []
    for _c in colors:
        _cc = QColor(_c)
        _cc.setAlphaF(alpha)
        out.append(_cc)
    return out


def _ink_bounds(fm: QFontMetrics, text: str) -> tuple[int, int]:
    """返回 ``text`` 在给定字体度量下的墨水边界：``(ink_left, ink_width)``。

    - ``ink_left``：墨水最左像素相对于 ``drawText(x, y, text)`` 中 ``x`` 的偏移
      （即 ``tightBoundingRect`` 的 ``x()`` 字段）。
    - ``ink_width``：墨水实际占用的水平像素宽度（即 ``tightBoundingRect.width()``）。

    Qt 的 ``tightBoundingRect`` 返回紧贴墨水的最小包围盒：
    - x 可能为负（字形墨水越过 origin 左侧，例如斜体 ``f``）；
    - width 一般 ≤ ``horizontalAdvance(text)``，但极少数斜体场景可能略大；
    - 对空白字符（U+0020 / U+3000 / NBSP / Tab）通常返回零宽 → 此处保留为 (0, 0)，
      由调用方决定回退策略（一般直接跳过 wipe 绘制）。

    返回为 int；底层 Qt API 已是 int。
    """
    if not text:
        return (0, 0)
    rect = fm.tightBoundingRect(text)
    return (int(rect.x()), int(rect.width()))


class KaraokePreview(QWidget):
    """多行歌词预览，带逐字高亮、注音显示和滚动支持。

    滚动模型：_scroll_center_line 表示视口中央对应的行索引（浮点数）。
    - 自动跟随：打轴推进时自动居中当前行
    - 手动滚动：鼠标滚轮浏览，点击某行后重新居中
    - 首行居中：_scroll_center_line=0 时首行在正中央，上方留空
    """

    line_clicked = pyqtSignal(int)
    checkpoint_clicked = pyqtSignal(int, int, int)  # line_idx, char_idx, checkpoint_idx
    char_edit_requested = pyqtSignal(int, int)  # line_idx, char_idx (F2 key)
    seek_to_char_requested = pyqtSignal(int, int)  # line_idx, char_idx (click)
    seek_to_checkpoint_requested = pyqtSignal(int, int ,int)  # line_idx, char_idx, checkpoint_idx (double-click)
    char_selected = pyqtSignal(int, int)  # line_idx, char_idx
    singer_change_requested = pyqtSignal(
        int, int, int, str
    )  # line_idx, start_char, end_char, singer_id
    delete_chars_requested = pyqtSignal(int, int, int)
    delete_timestamp_requested = pyqtSignal(int, int)
    insert_space_before_requested = pyqtSignal(int, int)
    insert_space_after_requested = pyqtSignal(int, int)
    merge_line_up_requested = pyqtSignal(int)
    delete_line_requested = pyqtSignal(int)
    insert_blank_line_before_requested = pyqtSignal(int)
    insert_blank_line_requested = pyqtSignal(int)
    add_checkpoint_requested = pyqtSignal(int, int)
    remove_checkpoint_requested = pyqtSignal(int, int)
    toggle_sentence_end_requested = pyqtSignal(int, int)
    auto_scroll_line_changed = pyqtSignal()
    user_interaction_during_auto_scroll = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._current_line_idx = 0
        self._current_char_idx = 0
        self._current_checkpoint_idx: Optional[int] = None
        self._current_time_ms = 0
        self._global_offset_ms = 0
        self._duration_ms = 0  # 音频总时长（用于行尾非句尾时的wipe右边界）
        self._visible_lines = 7  # 视口内可见行数（决定行高）
        self._scroll_center_line: float = 0.0  # 视口中央对应的行索引
        self._checkpoint_hitboxes: list = []  # [(QRect, line_idx, char_idx, cp_idx)]
        self._char_hitboxes: list = []  # [(QRect, line_idx, char_idx)]
        self.setMinimumHeight(400)
        self.setMouseTracking(True)

        # 音频引擎引用（用于 paintEvent 主动拉取高精度时间）
        self._audio_engine = None

        # 划词选中状态
        self._focus_line_idx: int = -1
        self._focus_char_idx: int = -1
        self._focus_char_range_end: int = -1
        self._focus_dragging: bool = False

        self._disable_click_jump: bool = False  # 禁用单击跳转

        # 单击/双击处理：快照机制
        # 单击时锁定 hitbox 快照，双击时使用快照判断，避免居中导致 hitbox 变化
        self._pending_click: Optional[dict] = None  # 按下时记录的待处理点击
        self._click_snapshot: Optional[dict] = None  # 单击后保存的快照（用于双击判断）
        self._double_click_handled: bool = False  # 双击已处理标志，防止 Release #2 触发单击逻辑
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(300)  # 双击间隔
        self._click_timer.timeout.connect(self._clear_click_snapshot)
        self._press_pos: Optional[tuple] = None  # 鼠标按下位置 (x, y)
        self._CLICK_MOVE_THRESHOLD = 5  # 像素级防抖阈值：移动超过此距离视为划词

        # 缓存字体和 QFontMetrics，避免每帧重建
        self._font_current = QFont("Microsoft YaHei", 22, QFont.Weight.Bold)
        self._font_context = QFont("Microsoft YaHei", 18)
        self._font_ruby = QFont("Microsoft YaHei", 10)
        self._font_checkpoint = QFont("Microsoft YaHei", 8)
        self._font_line_number = QFont("Microsoft YaHei", 10)
        self._fm_current = QFontMetrics(self._font_current)
        self._fm_context = QFontMetrics(self._font_context)
        self._fm_ruby = QFontMetrics(self._font_ruby)
        self._fm_checkpoint = QFontMetrics(self._font_checkpoint)
        self._fm_line_number = QFontMetrics(self._font_line_number)
        self._line_number_margin = 45  # 行号左侧区域宽度
        self._ruby_spacing = 4  # Ruby与主文字的垂直间距

        # 歌词对齐方式："left" / "center" / "right"
        self._alignment: str = "center"
        self._alignment_margin: int = 168  # 左/右对齐时的页边距(px)

        # Checkpoint 标记字符（可配置）
        self._checkpoint_markers: dict[str, str] = {
            "cp_first_timed": "▶",
            "cp_first_empty": "▷",
            "cp_multi_timed": "▮",
            "cp_multi_empty": "▯",
            "cp_sentence_end_timed": "⬟",
            "cp_sentence_end_empty": "⬠",
        }

        # 逐句渲染数据缓存（避免每帧重复计算）
        # 每行有自己的版本号，只有数据改变的行才重新计算
        self._sentence_cache: dict = {}
        self._line_versions: dict = {}  # line_idx -> version
        self._global_version: int = 0  # 全局版本号，用于字体变化等全局刷新
        self._is_playing: bool = False
        self._preview_guide_enabled: bool = False  # 走字预览指引（仅播放打轴时光标所在行生效）
        self._auto_scroll_enabled: bool = True  # 自动滚动开关，特殊场景可关闭
        self._auto_scroll_suspended: bool = False  # 用户交互后挂起自动滚动
        self._scroll_mode: str = "auto"  # auto / always / never
        self._last_auto_scroll_line_idx: int = -1  # 上次自动滚动到的行（与 _current_line_idx 独立）
        self._line_switch_points: list[tuple[int, int]] = []  # [(switch_ms, line_idx)]
        self._current_switch_idx: int = 0  # 当前快照位置
        self._SCROLL_SCALE = 1000  # 滚动条精度缩放因子（1行=1000单位）

        # 右侧纵向滚动条，用于快速浏览整个歌词预览
        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.setVisible(False)
        self._scrollbar.valueChanged.connect(self._on_scrollbar_changed)
        self._scrollbar.setSingleStep(self._SCROLL_SCALE)      # 点击箭头 = 1行
        self._scrollbar.setPageStep(self._SCROLL_SCALE * 5)     # 点击轨道 = 5行
        self._SCROLLBAR_WIDTH = 14

        # 监听主题变化，触发重绘
        theme.changed.connect(self.update)

    def set_playing(self, playing: bool):
        """由外部同步播放状态，用于决定 paintEvent 是否旁路缓存。"""
        self._is_playing = bool(playing)
        if playing:
            self._build_line_switch_points()

    def _build_line_switch_points(self):
        """构建换行时间点快照，记录每个时间点应切换到哪一行。"""
        self._line_switch_points = []
        self._current_switch_idx = 0
        if not self._project or not self._project.sentences:
            return
        for idx, sentence in enumerate(self._project.sentences):
            ts = sentence.global_timing_start_ms
            if ts is not None:
                self._line_switch_points.append((ts, idx))
        self._line_switch_points.sort()

    def set_disable_click_jump(self, disable: bool):
        """设置是否禁用单击跳转功能。"""
        self._disable_click_jump = bool(disable)

    def set_preview_guide_enabled(self, enabled: bool):
        """设置走字预览指引开关（播放打轴时当前行用过渡色提示打轴进度）。"""
        self._preview_guide_enabled = bool(enabled)
        self.update()

    def set_auto_scroll_enabled(self, enabled: bool):
        """设置是否启用自动滚动功能。特殊场景可关闭。"""
        self._auto_scroll_enabled = bool(enabled)

    def set_scroll_mode(self, mode: str):
        """设置滚动模式：auto（操作后挂起 6 秒）/ always（始终滚动）/ never（不滚动）。"""
        self._scroll_mode = mode
        if mode == "never":
            self._auto_scroll_enabled = False
        else:
            self._auto_scroll_enabled = True
            if mode == "always":
                self._auto_scroll_suspended = False

    def _suspend_auto_scroll(self):
        """用户交互时挂起自动滚动，通知外部停止 cooldown timer。"""
        if self._scroll_mode == "always":
            return
        self._auto_scroll_suspended = True
        self.user_interaction_during_auto_scroll.emit()

    def resume_auto_scroll(self):
        """恢复自动滚动：将视口同步到当前播放行（不改变编辑光标）。"""
        self._auto_scroll_suspended = False
        self._scroll_to_line(self._last_auto_scroll_line_idx)

    def set_duration(self, duration_ms: int):
        """设置音频总时长（用于行尾非句尾时的wipe右边界）"""
        self._duration_ms = duration_ms

    def set_project(self, project: Project):
        self._project = project
        self._scroll_center_line = 0.0
        self._sentence_cache.clear()
        # focus 域默认值：首个非空行的首字符。空项目保持 -1。
        # focus 是用户视觉/操作真理来源（点字符/拖选/纯←→），与 current（cp 域反馈）独立。
        self._focus_line_idx = -1
        self._focus_char_idx = -1
        self._focus_char_range_end = -1
        self._focus_dragging = False
        if project and project.sentences:
            for idx, sentence in enumerate(project.sentences):
                if sentence.characters:
                    self._focus_line_idx = idx
                    self._focus_char_idx = 0
                    self._focus_char_range_end = 0
                    break
            # 预渲染所有句子到缓存
            self._prewarm_all_sentences()
        self._update_scrollbar_range()
        self._sync_scrollbar_to_scroll_center()
        self._update_display()

    def set_focus_position(self, line_idx:int = 0,char_idx: int = 0):
        # 用于打轴状态下更新foucs
        new_line = float(line_idx)
        if new_line == self._scroll_center_line and line_idx == self._current_char_idx:
            self._focus_char_idx = char_idx
            self._focus_char_range_end = char_idx
            self._update_display()
            return
        self._focus_line_idx = line_idx
        self._focus_char_idx = char_idx
        self._focus_char_range_end = char_idx
        if self._is_playing:
            self._warm_nearby_cache(budget=2)
        self._update_display()

    def set_current_position(self, line_idx: int, char_idx: int = 0):
        """更新当前打轴位置（行+字符），并居中滚动。"""
        if line_idx == self._current_line_idx:
            self._current_char_idx = char_idx
            self._update_display()
            return
        self._current_line_idx = line_idx
        self._current_char_idx = char_idx
        # 播放中：视口居中受滚动模式/挂起状态管控（set_current_time_ms 统一负责视口）
        # 非播放（手动导航）：始终居中
        if not self._is_playing or (self._auto_scroll_enabled and not self._auto_scroll_suspended):
            self.scroll_current_line_to_center()
        # 行切换时重新锚定预热中心（仅播放期间）
        if self._is_playing:
            self._warm_nearby_cache(budget=2)
        self._update_display()

    def scroll_current_line_to_center(self):
        """将当前行滚动到视口中央。

        幂等保护：若目标 line_idx 已是当前 scroll_center_line，
        避免短暂跳变导致空白行。
        """
        new_line = float(self._current_line_idx)
        if new_line == self._scroll_center_line:
            return
        self._scroll_center_line = new_line
        self._sync_scrollbar_to_scroll_center()
        self._update_display()

    def _scroll_to_line(self, line_idx: int):
        """纯视觉滚动：将指定行移到视口中央，不改变 _current_line_idx（编辑光标）。

        用于自动滚动，与编辑光标完全解耦。
        """
        if line_idx < 0:
            return
        new_line = float(line_idx)
        if new_line == self._scroll_center_line:
            return
        self._scroll_center_line = new_line
        self._sync_scrollbar_to_scroll_center()
        self._update_display()

    def _find_line_for_time(self, time_ms: int) -> Optional[int]:
        """查找当前时间对应的歌词行索引（使用快照索引，O(log n)判断）。

        Args:
            time_ms: 当前播放时间（毫秒）

        Returns:
            行索引，如果没有找到返回 None
        """
        points = self._line_switch_points
        if not points:
            return None

        # Manual seeks can jump backwards or far forwards. The old incremental
        # scan reset to 0 on backwards seeks, making late-song jumps O(n).
        idx = bisect_right(points, (time_ms, sys.maxsize)) - 1
        idx = max(0, min(idx, len(points) - 1))
        self._current_switch_idx = idx
        return points[idx][1]

    def set_current_time_ms(self, time_ms: int):
        self._current_time_ms = time_ms
        # 播放期间按就近扩散顺序预热少量邻近行，降低视口内首帧卡顿
        if self._is_playing:
            self._warm_nearby_cache(budget=2)
        # 自动滚动：检测播放行变化（用于 cooldown 判断），
        # 仅在未挂起时才移动视口（不改变编辑光标 _current_line_idx）
        if self._auto_scroll_enabled and self._is_playing:
            target_line_idx = self._find_line_for_time(time_ms)
            if target_line_idx is not None:
                if target_line_idx != self._last_auto_scroll_line_idx:
                    self._last_auto_scroll_line_idx = target_line_idx
                    self.auto_scroll_line_changed.emit()
                    if not self._auto_scroll_suspended:
                        self._scroll_to_line(target_line_idx)
        self.update()

    def _prewarm_all_sentences(self) -> None:
        """项目加载时预渲染所有句子到缓存，避免切换界面时卡顿。"""
        if not self._project or not self._project.sentences:
            return
        # 使用默认字体预渲染，实际渲染时会按 is_current 切换字体
        for idx, sentence in enumerate(self._project.sentences):
            if sentence.characters:
                self._get_sentence_render_data(idx, sentence, self._fm_context, "ctx")
                if idx == self._current_line_idx:
                    self._get_sentence_render_data(idx, sentence, self._fm_current, "cur")

    def _warm_nearby_cache(self, budget: int = 2) -> None:
        """按 L, L+1, L-1, L+2, L-2, ... 的就近扩散顺序预热 _sentence_cache。

        - 每次最多预热 budget 句，避免阻塞 paint
        - 已缓存/版本匹配的行直接跳过，不重复计算
        - 行号边界：center 被 clamp 到 [0, n-1]，扩散候选再次越界检查；
          两端都越界后早退，避免无意义空转
        """
        if not self._project or not self._project.sentences:
            return
        sentences = self._project.sentences
        n = len(sentences)
        if n <= 0:
            return
        # 播放+自动滚动时以播放行为中心预热，否则以编辑光标为中心
        if (
            self._is_playing
            and self._auto_scroll_enabled
            and not self._auto_scroll_suspended
            and self._last_auto_scroll_line_idx >= 0
        ):
            center = max(0, min(self._last_auto_scroll_line_idx, n - 1))
        else:
            center = max(0, min(self._current_line_idx, n - 1))
        warmed = 0
        max_radius = max(4, budget * 2, self._visible_lines)
        max_radius = min(max_radius, n - 1)
        for offset in range(max_radius + 1):
            # 扩散序：0, +1, -1, +2, -2, +3, -3, ...
            if offset == 0:
                candidates = (center,)
            else:
                candidates = (center + offset, center - offset)
            any_valid = False
            for idx in candidates:
                if idx < 0 or idx >= n:
                    continue
                any_valid = True
                entry = self._sentence_cache.get(idx)
                is_current_line = idx == self._current_line_idx
                fk = "cur" if is_current_line else "ctx"
                line_version = self._line_versions.get(idx, 0)
                if entry and entry["v"] == line_version and entry["gv"] == self._global_version and entry["fk"] == fk:
                    continue
                main_fm = self._fm_current if is_current_line else self._fm_context
                self._get_sentence_render_data(idx, sentences[idx], main_fm, fk)
                warmed += 1
                if warmed >= budget:
                    return
            # 两端都越界（向前到 0、向后到 n-1 之外）→ 无需继续扩散
            if offset > 0 and not any_valid:
                return

    def set_global_offset(self, offset_ms: int):
        """设置全局偏移量（毫秒），更新所有字符的时间戳"""
        self._global_offset_ms = offset_ms
        # 偏移变更时，清除缓存使 wipe 区间重新计算
        self._sentence_cache.clear()

    def set_audio_engine(self, engine):
        """设置音频引擎引用，使 paintEvent 可主动拉取高精度时间。"""
        self._audio_engine = engine
        self._line_versions.clear()
        self._global_version += 1
        self.update()

    def set_alignment(self, alignment: str):
        """设置歌词对齐方式。

        Args:
            alignment: "left"（左对齐）、"center"（居中对齐）或 "right"（右对齐）
        """
        if alignment not in ("left", "center", "right"):
            alignment = "center"
        if self._alignment != alignment:
            self._alignment = alignment
            self.update()

    def set_alignment_margin(self, margin: int):
        """设置左/右对齐时的页边距。

        Args:
            margin: 页边距像素值
        """
        margin = max(0, min(500, margin))
        if self._alignment_margin != margin:
            self._alignment_margin = margin
            self.update()

    def set_font_sizes(self, base_size: int, current_line_size: int = 0, ruby_size: int = 10, cp_size: int = 8, line_height_factor: float = 1.20, ruby_spacing: int = 4, main_font: str = "Microsoft YaHei", ruby_font: str = "Microsoft YaHei"):
        """设置字体大小/字体族并自动适配预览行数。

        Args:
            base_size: 基础字体大小（非当前行）
            current_line_size: 当前行放大字体大小，0 表示自动比基础大4
            ruby_size: 注音字体大小
            cp_size: 节奏点标记字体大小
            line_height_factor: 行高系数（默认1.20）
            ruby_spacing: Ruby与主文字的垂直间距（默认4px）
            main_font: 主文字（当前行/上下文行）字体族，缺失时回退微软雅黑
            ruby_font: Ruby 注音字体族，缺失时回退微软雅黑。
                节奏点标记字体固定为微软雅黑，不随设置变化。
        """
        from strange_uta_game.frontend.font_utils import resolve_font_family

        context_size = max(1, min(99, base_size))
        current_size = max(1, min(99, current_line_size if current_line_size > 0 else base_size + 4))
        ruby_size = max(1, min(99, ruby_size))
        cp_size = max(1, min(99, cp_size))
        line_height_factor = max(-1.0, min(5.0, line_height_factor))
        ruby_spacing = max(0, min(99, ruby_spacing))
        main_family = resolve_font_family(main_font)
        ruby_family = resolve_font_family(ruby_font)

        self._font_current = QFont(main_family, current_size, QFont.Weight.Bold)
        self._font_context = QFont(main_family, context_size)
        self._font_ruby = QFont(ruby_family, ruby_size)
        self._font_checkpoint = QFont("Microsoft YaHei", cp_size)
        self._fm_current = QFontMetrics(self._font_current)
        self._fm_context = QFontMetrics(self._font_context)
        self._fm_ruby = QFontMetrics(self._font_ruby)
        self._fm_checkpoint = QFontMetrics(self._font_checkpoint)
        self._ruby_spacing = ruby_spacing
        self._line_height_factor = line_height_factor

        # 行高以当前行（放大后）字体大小为准，需容纳 ruby + ruby_spacing + cp
        total_height = self._fm_current.height() + self._fm_ruby.height() + ruby_spacing + self._fm_checkpoint.height()
        # factor<=0 时视为极紧凑（显示最多行），避免除以零
        safe_factor = max(0.05, line_height_factor)
        line_h = total_height * safe_factor
        h = self.height() if self.height() > 0 else 600
        self._visible_lines = max(3, min(15, int(h / line_h)))

        # 清除缓存并重绘
        self._sentence_cache.clear()
        self._line_versions.clear()
        self._global_version += 1
        self._update_scrollbar_range()
        self.update()

    def set_checkpoint_markers(self, markers: dict[str, str]):
        """设置 checkpoint 标记字符并刷新缓存。"""
        self._checkpoint_markers.update(markers)
        self._sentence_cache.clear()
        self._line_versions.clear()
        self._global_version += 1
        self.update()

    def _update_display(self):
        self._global_version += 1
        self.update()

    def _invalidate_line(self, line_idx: int):
        """使特定行的缓存失效（用于行内数据改变时）"""
        if line_idx in self._line_versions:
            self._line_versions[line_idx] += 1
        else:
            self._line_versions[line_idx] = 0
        self.update()

    def _invalidate_all_lines(self):
        """使所有行的缓存失效（用于全局数据改变时）"""
        for line_idx in list(self._line_versions.keys()):
            self._line_versions[line_idx] += 1
        self.update()

    # ---- 窗口大小变化 ----

    def resizeEvent(self, event):
        """窗口大小变化时重新计算可见行数，保持行高不变。"""
        super().resizeEvent(event)
        if hasattr(self, '_fm_current') and hasattr(self, '_fm_ruby'):
            total_height = (self._fm_current.height() + self._fm_ruby.height()
                           + self._ruby_spacing + self._fm_checkpoint.height())
            safe_factor = max(0.05, getattr(self, '_line_height_factor', 1.20))
            line_h = total_height * safe_factor
            h = self.height() if self.height() > 0 else 600
            self._visible_lines = max(3, min(15, int(h / line_h)))
            self._update_scrollbar_range()
            self.update()
        # 滚动条靠右放置，占满高度
        scroll_w = getattr(self, '_SCROLLBAR_WIDTH', 14)
        self._scrollbar.setGeometry(self.width() - scroll_w, 0, scroll_w, self.height())

    # ---- 滚动 ----

    def wheelEvent(self, a0):
        """鼠标滚轮滚动浏览歌词"""
        if not a0 or not self._project or not self._project.sentences:
            return
        self._suspend_auto_scroll()
        delta = a0.angleDelta().y()
        # 每个滚轮 notch（120 单位）滚动 1 行
        self._scroll_center_line -= delta / 120.0
        total = len(self._project.sentences)
        self._scroll_center_line = max(
            0.0, min(float(total - 1), self._scroll_center_line)
        )
        self._sync_scrollbar_to_scroll_center()
        self.update()

    def _on_scrollbar_changed(self, value: int):
        """滚动条拖动 → 更新视口中央行索引"""
        if not self._project or not self._project.sentences:
            return
        self._suspend_auto_scroll()
        self._scroll_center_line = value / self._SCROLL_SCALE
        self.update()

    def _update_scrollbar_range(self):
        """根据当前句子总数刷新滚动条的 range 和 pageStep"""
        self._scrollbar.blockSignals(True)
        if not self._project or not self._project.sentences:
            self._scrollbar.setVisible(False)
            self._scrollbar.blockSignals(False)
            return
        total = len(self._project.sentences)
        self._scrollbar.setVisible(True)
        # range: [0, (total-1) * SCALE]，视口占 visible_lines 行
        max_val = max(0, (total - 1) * self._SCROLL_SCALE)
        self._scrollbar.setRange(0, max_val)
        self._scrollbar.setPageStep(self._SCROLL_SCALE * self._visible_lines)
        self._scrollbar.blockSignals(False)

    def _sync_scrollbar_to_scroll_center(self):
        """将 _scroll_center_line 同步到滚动条 value（阻断信号防循环）"""
        if not self._project or not self._project.sentences:
            return
        val = int(round(self._scroll_center_line * self._SCROLL_SCALE))
        val = max(self._scrollbar.minimum(), min(self._scrollbar.maximum(), val))
        self._scrollbar.blockSignals(True)
        self._scrollbar.setValue(val)
        self._scrollbar.blockSignals(False)

    # ---- 点击 ----

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if not a0 or not self._project or not self._project.sentences:
            return
        self._suspend_auto_scroll()

        click_x = int(a0.position().x())
        click_y = int(a0.position().y())

        # 右键点击 → 打开上下文菜单
        if a0.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(a0.globalPosition().toPoint(), click_x, click_y)
            return

        # 记录按下位置，用于像素级防抖判断
        self._press_pos = (click_x, click_y)

        # 检查是否点击在 checkpoint 标记上
        for marker_rect, line_idx, char_idx, cp_idx in self._checkpoint_hitboxes:
            if marker_rect.contains(click_x, click_y):
                self._pending_click = {
                    "type": "cp",
                    "line_idx": line_idx,
                    "char_idx": char_idx,
                    "cp_idx": cp_idx,
                }
                return

        # 检查字符文本点击 → 开始划词选择
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                self._focus_line_idx = line_idx
                self._focus_char_idx = char_idx
                self._focus_char_range_end = char_idx
                self._focus_dragging = True
                self._pending_click = {
                    "type": "char",
                    "line_idx": line_idx,
                    "char_idx": char_idx,
                }
                self.update()
                return

        # 点击在行内空白区域：按水平距离找最近的 hitbox
        nearest = self._find_nearest_hitbox(click_x, click_y)
        if nearest:
            hit_type, line_idx, char_idx, cp_idx = nearest
            self._focus_line_idx = line_idx
            self._focus_char_idx = char_idx
            self._focus_char_range_end = char_idx
            self._focus_dragging = True
            if hit_type == "cp":
                self._pending_click = {
                    "type": "cp",
                    "line_idx": line_idx,
                    "char_idx": char_idx,
                    "cp_idx": cp_idx,
                }
            else:
                self._pending_click = {
                    "type": "char",
                    "line_idx": line_idx,
                    "char_idx": char_idx,
                }
            self.update()
            return

        # 回退到行级别点击（纯空行或行间空隙）：根据 y 坐标反算行索引
        # 先清除旧快照（避免上次单击 300ms 计时器延迟触发 set_current_position）
        self._focus_line_idx = -1
        self._focus_char_idx = -1
        self._focus_char_range_end = -1
        self._clear_click_snapshot()

        h = self.height()
        line_height = h / self._visible_lines
        center_y = h / 2.0
        clicked_line = self._scroll_center_line + (click_y - center_y) / line_height
        target_idx = int(round(clicked_line))
        total = len(self._project.sentences)
        if 0 <= target_idx < total:
            # 设置 focus 到目标行首位，走正常 pending_click 流程：
            # mouseReleaseEvent 会发出 char_selected(target_idx, 0)，
            # _on_char_selected 负责居中并通过 timing_service 向前找最近节奏点。
            self._focus_line_idx = target_idx
            self._focus_char_idx = 0
            self._focus_char_range_end = 0
            self._focus_dragging = True
            self._pending_click = {
                "type": "char",
                "line_idx": target_idx,
                "char_idx": 0,
            }
        self.update()

    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):
        """鼠标拖拽 → 扩展划词选择范围"""
        if not a0:
            return

        move_x = int(a0.position().x())
        move_y = int(a0.position().y())

        # 像素级防抖：移动超过阈值时清除待处理点击（说明用户是划词而非单击）
        if self._press_pos is not None and self._pending_click is not None:
            dx = move_x - self._press_pos[0]
            dy = move_y - self._press_pos[1]
            if dx * dx + dy * dy > self._CLICK_MOVE_THRESHOLD * self._CLICK_MOVE_THRESHOLD:
                self._pending_click = None

        if not self._focus_dragging:
            return

        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(move_x, move_y) and line_idx == self._focus_line_idx:
                self._focus_char_range_end = char_idx
                self.update()
                return

    def mouseReleaseEvent(self, a0: Optional[QMouseEvent]):
        """鼠标释放 → 结束划词，或触发单击"""
        if not a0 or a0.button() != Qt.MouseButton.LeftButton:
            return

        self._focus_dragging = False

        # 双击的 Release #2：双击事件已处理，跳过一切单击逻辑
        if self._double_click_handled:
            self._double_click_handled = False
            self._pending_click = None
            self._press_pos = None
            return

        # 如果已有快照（双击的第二次松开），跳过单击
        if self._click_snapshot is not None:
            self._pending_click = None
            self._press_pos = None
            return

        # 如果有待处理点击 且 移动距离 < 阈值，立即执行单击
        if self._pending_click is not None and self._press_pos is not None:
            click = self._pending_click
            self._pending_click = None
            self._press_pos = None

            if not self._disable_click_jump:
                if click["type"] == "cp":
                    self.checkpoint_clicked.emit(
                        click["line_idx"], click["char_idx"], click["cp_idx"]
                    )
                elif click["type"] == "char":
                    self.char_selected.emit(click["line_idx"], click["char_idx"])
                    self.line_clicked.emit(click["line_idx"])

                # 保存快照用于双击判断，300ms 后清除
                self._click_snapshot = click
                self._click_timer.start()
        else:
            # _pending_click 被拖拽检测清掉了，但 focus 已在 mousePressEvent
            # 里更新。用 focus 位置作为单击坐标，确保 current 域同步。
            if (
                not self._disable_click_jump
                and self._focus_line_idx >= 0
                and self._focus_char_idx >= 0
            ):
                self.char_selected.emit(self._focus_line_idx, self._focus_char_idx)
                self.line_clicked.emit(self._focus_line_idx)
            self._pending_click = None
            self._press_pos = None

    def _clear_click_snapshot(self):
        """清除单击快照和相关定时器。

        300ms 超时 → 确认是单击而非双击，兜底同步 current 域到 focus 位置，
        防止期间有状态漂移。

        例外：当 focus 落在无 checkpoint 字符（cc=0 且非句尾）时跳过同步，
        保持 _on_char_selected 中已正确设置的 current 域（指向最近有效 cp）。
        """
        self._click_snapshot = None
        self._click_timer.stop()
        if self._focus_line_idx >= 0 and self._focus_char_idx >= 0:
            if self._project and self._focus_line_idx < len(self._project.sentences):
                sentence = self._project.sentences[self._focus_line_idx]
                if self._focus_char_idx < len(sentence.characters):
                    ch = sentence.characters[self._focus_char_idx]
                    if ch.check_count == 0 and not ch.is_sentence_end:
                        return
            self.set_current_position(self._focus_line_idx, self._focus_char_idx)

    def _show_context_menu(self, global_pos, click_x: int, click_y: int):
        """显示字符上下文菜单。"""
        if not self._project or not self._project.sentences:
            return

        target_line_idx = self._current_line_idx
        target_char_idx = self._current_char_idx
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                target_line_idx = line_idx
                target_char_idx = char_idx
                self._current_line_idx = line_idx
                self._current_char_idx = char_idx
                break
        else:
            # 未命中字符 hitbox：先试最近 hitbox（行内空白区域），
            # 再按 y 坐标反算行（纯空行 / 行间空隙）
            nearest = self._find_nearest_hitbox(click_x, click_y)
            if nearest:
                _, target_line_idx, target_char_idx, _ = nearest
            else:
                h = self.height()
                line_height = h / self._visible_lines
                center_y = h / 2.0
                clicked_line = self._scroll_center_line + (click_y - center_y) / line_height
                calc_idx = int(round(clicked_line))
                total = len(self._project.sentences)
                if 0 <= calc_idx < total:
                    target_line_idx = calc_idx
                    target_char_idx = 0
            # 右键非字符区域同步 focus 域（与左键回退路径一致，
            # 保证菜单关闭后 F4/F5 等操作指向正确位置）
            self._focus_line_idx = target_line_idx
            self._focus_char_idx = target_char_idx
            self._focus_char_range_end = target_char_idx

        if target_line_idx < 0 or target_line_idx >= len(self._project.sentences):
            return

        sentence = self._project.sentences[target_line_idx]
        if target_char_idx < 0:
            target_char_idx = 0
        if sentence.characters and target_char_idx >= len(sentence.characters):
            target_char_idx = len(sentence.characters) - 1

        in_selection = False
        if (
            self._focus_line_idx == target_line_idx
            and self._focus_char_idx >= 0
            and self._focus_char_range_end >= 0
        ):
            sel_start = min(self._focus_char_idx, self._focus_char_range_end)
            sel_end = max(self._focus_char_idx, self._focus_char_range_end)
            in_selection = sel_start <= target_char_idx <= sel_end
        else:
            sel_start = target_char_idx
            sel_end = target_char_idx

        delete_start = sel_start if in_selection else target_char_idx
        delete_end = sel_end + 1 if in_selection else target_char_idx + 1

        menu = RoundMenu(parent=self)

        delete_action = Action("删除字符", menu)
        delete_action.triggered.connect(
            lambda checked=False: self.delete_chars_requested.emit(
                target_line_idx, delete_start, delete_end
            )
        )
        menu.addAction(delete_action)

        delete_timestamp = Action("删除当前时间戳并回滚", menu)
        delete_timestamp.triggered.connect(
            lambda checked=False: self.delete_timestamp_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(delete_timestamp)

        insert_space_before_action = Action("在此前插入空格", menu)
        insert_space_before_action.triggered.connect(
            lambda checked=False: self.insert_space_before_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(insert_space_before_action)

        insert_space_after_action = Action("在此后插入空格", menu)
        insert_space_after_action.triggered.connect(
            lambda checked=False: self.insert_space_after_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(insert_space_after_action)
        menu.addSeparator()

        merge_up_action = Action("合并上一行", menu)
        merge_up_action.setEnabled(target_line_idx > 0)
        merge_up_action.triggered.connect(
            lambda checked=False: self.merge_line_up_requested.emit(target_line_idx)
        )
        menu.addAction(merge_up_action)

        delete_line_action = Action("删除本行", menu)
        delete_line_action.triggered.connect(
            lambda checked=False: self.delete_line_requested.emit(target_line_idx)
        )
        menu.addAction(delete_line_action)

        insert_blank_line_before_action = Action("在此前插入空行", menu)
        insert_blank_line_before_action.triggered.connect(
            lambda checked=False: self.insert_blank_line_before_requested.emit(target_line_idx)
        )
        menu.addAction(insert_blank_line_before_action)

        insert_blank_line_after_action = Action("在此后插入空行", menu)
        insert_blank_line_after_action.triggered.connect(
            lambda checked=False: self.insert_blank_line_requested.emit(target_line_idx)
        )
        menu.addAction(insert_blank_line_after_action)
        menu.addSeparator()

        add_checkpoint_action = Action("增加节奏点", menu)
        add_checkpoint_action.triggered.connect(
            lambda checked=False: self.add_checkpoint_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(add_checkpoint_action)

        remove_checkpoint_action = Action("减少节奏点", menu)
        remove_checkpoint_action.triggered.connect(
            lambda checked=False: self.remove_checkpoint_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(remove_checkpoint_action)

        toggle_sentence_end_action = Action("设置/取消句尾", menu)
        toggle_sentence_end_action.triggered.connect(
            lambda checked=False: self.toggle_sentence_end_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(toggle_sentence_end_action)
        menu.addSeparator()

        singer_start = delete_start if in_selection else target_char_idx
        singer_end = delete_end - 1 if in_selection else target_char_idx
        singer_menu = RoundMenu("设置演唱者", self)
        default_singer = self._project.get_default_singer()
        default_action = Action("默认演唱者", singer_menu)
        default_action.triggered.connect(
            lambda checked=False: self.singer_change_requested.emit(
                target_line_idx, singer_start, singer_end, default_singer.id
            )
        )
        singer_menu.addAction(default_action)
        singer_menu.addSeparator()

        for singer in self._project.singers:
            action = Action(singer.name, singer_menu)
            action.triggered.connect(
                lambda checked=False, sid=singer.id: self.singer_change_requested.emit(
                    target_line_idx, singer_start, singer_end, sid
                )
            )
            singer_menu.addAction(action)

        menu.addMenu(singer_menu)
        menu.exec(global_pos)

    def _find_next_line_first_timestamp(self, current_line_idx: int) -> Optional[int]:
        """查找下一行的第一个checkpoint时间戳，用于行尾非句尾时的wipe右边界。

        Returns:
            下一行第一个字符的 global_timestamps[0]，如果不存在返回 None
        """
        if not self._project or not self._project.sentences:
            return None
        next_line_idx = current_line_idx + 1
        if next_line_idx >= len(self._project.sentences):
            return None
        next_sentence = self._project.sentences[next_line_idx]
        for ch in next_sentence.characters:
            if ch.global_timestamps:
                return int(ch.global_timestamps[0])
        return None

    def _find_prev_line_last_timestamp(self, current_line_idx: int) -> Optional[int]:
        """向前逐行查找最近一个有时间戳行的最后时间戳，用于行首 cc=0 字符的 wipe 起始锚点。

        对每一行执行早停检测：若该行存在 is_sentence_end=True 但无 global_sentence_end_ts，
        或存在 check_count>0 但无 global_timestamps，视为该行未完整打轴，则结束（返回 None）。
        若该行全为 cc=0 且无时间戳，则跳过继续向前找。

        Returns:
            最近一个有时间戳行的最后时间戳，或 None（遇到未完整打轴行或找到头）。
        """
        if not self._project or not self._project.sentences or current_line_idx <= 0:
            return None
        for line_idx in range(current_line_idx - 1, -1, -1):
            line = self._project.sentences[line_idx]
            last_ts: Optional[int] = None
            for ch in line.characters:
                if ch.is_sentence_end and ch.global_sentence_end_ts is None:
                    return None  # 未完整打轴，作为屏障终止查找
                if ch.check_count > 0 and not ch.global_timestamps:
                    return None  # 未完整打轴，作为屏障终止查找
                if ch.is_sentence_end and ch.global_sentence_end_ts is not None:
                    t = int(ch.global_sentence_end_ts)
                    if last_ts is None or t > last_ts:
                        last_ts = t
                if ch.global_timestamps:
                    t = int(ch.global_timestamps[-1])
                    if last_ts is None or t > last_ts:
                        last_ts = t
            if last_ts is not None:
                return last_ts
            # 该行全为 cc=0 且无时间戳，继续向前找
        return None

    def _get_sentence_render_data(
        self, idx: int, sentence, main_fm, font_key: str
    ) -> dict:
        """返回逐句渲染数据。

        渲染模型：
          - wipe 时间线的单位 = 一整行（sentence），与打轴的连词组解耦
          - 行内所有带 global_timestamps 的字符 + 行尾（is_sentence_end +
            global_sentence_end_ts）构成"锚点"
          - 相邻锚点之间的所有字符（含无时间戳的）按字符像素宽度加权线性插值
            分配 wipe 区间——解决等字符数分配导致的宽度/节奏不匹配跳变
          - 首锚之前 / 末锚之后的字符贴首/末锚（wipe 恒 0 或 1）
          - linked_to_next 只影响视觉渲染层（连词不拆字画），不参与 wipe 计算

        缓存策略：
          - 每行有自己的版本号，只有数据改变的行才重新计算
          - 全局版本号用于字体变化等全局刷新
        """
        line_version = self._line_versions.get(idx, 0)
        entry = self._sentence_cache.get(idx)
        if entry and entry["v"] == line_version and entry["gv"] == self._global_version and entry["fk"] == font_key:
            return entry

        chars = sentence.chars
        characters = sentence.characters
        n_chars = len(chars)

        # 字符像素宽度（初始为字符本身的 advance width，含侧 bearings/字间距）
        # 同时计算字符的"墨水"边界（tightBoundingRect）—— wipe 严格按墨水边界裁剪，
        # 不再扫过字形周围的透明像素或句尾扩展区，确保 wipe 的视觉起止与时间戳一致。
        fm_ruby = self._fm_ruby
        avg_char_w = main_fm.averageCharWidth()
        char_widths = []
        # char_ink_offsets[ci] = ink_left（相对 drawText 的 x）
        # char_ink_widths[ci]  = ink 实际像素宽度
        # 空白字符 ink_width = 0，wipe 时跳过裁剪绘制（视觉上保留空白等待）。
        char_ink_offsets: list[int] = []
        char_ink_widths: list[int] = []
        for ci, ch in enumerate(chars):
            char_w = main_fm.horizontalAdvance(ch) if ch != ' ' else avg_char_w
            char_widths.append(char_w)
            ink_off, ink_w = _ink_bounds(main_fm, ch)
            char_ink_offsets.append(ink_off)
            char_ink_widths.append(ink_w)

        # ---------- 连词组（仅用于视觉层，与 wipe 计算无关） ----------
        char_groups: list = []
        cur_grp: Optional[list] = None
        for ci in range(n_chars):
            if cur_grp is None:
                cur_grp = [ci]
                char_groups.append(cur_grp)
            elif ci > 0 and characters[ci - 1].linked_to_next:
                cur_grp.append(ci)
            else:
                cur_grp = [ci]
                char_groups.append(cur_grp)

        linked_leader_groups: dict = {}
        linked_non_leader: set = set()
        for group in char_groups:
            if len(group) > 1:
                linked_leader_groups[group[0]] = group
                for _ci in group[1:]:
                    linked_non_leader.add(_ci)

        # 连词组：将合并后的 ruby 宽度平均分配到组内每个字符
        for leader_ci, group in linked_leader_groups.items():
            merged_ruby_text = ""
            for _gci in group:
                _r = characters[_gci].ruby
                if _r:
                    merged_ruby_text += _r.text
            if merged_ruby_text:
                merged_ruby_w = fm_ruby.horizontalAdvance(merged_ruby_text)
                group_total_char_w = sum(char_widths[g] for g in group)
                # 确保组的总宽度 >= 合并后ruby的宽度
                target_total_w = max(group_total_char_w, merged_ruby_w)
                # 将宽度平均分配到组内每个字符
                per_char_w = target_total_w / len(group)
                for g in group:
                    char_widths[g] = max(char_widths[g], per_char_w)

        # 单字符：确保宽度 >= ruby宽度（跳过连词组成员，已由连词组逻辑处理）
        for ci, ch in enumerate(chars):
            if ci in linked_leader_groups or ci in linked_non_leader:
                continue  # 连词组成员已处理
            ruby = characters[ci].ruby
            if ruby:
                ruby_w = fm_ruby.horizontalAdvance(ruby.text)
                char_widths[ci] = max(char_widths[ci], ruby_w)

        # 确保字符宽度 >= CP标记总宽度（避免CP标记重叠）
        # 句尾marker独立于普通marker，在字符右侧扩展半字符宽度
        fm_checkpoint = self._fm_checkpoint
        end_sentence_w: dict[int, int] = {}
        for ci, ch_obj in enumerate(characters):
            if ch_obj.total_timing_points > 0:
                total_cp_w = 0
                for cp_idx in range(ch_obj.total_timing_points):
                    is_sentence_end_marker = (
                        ch_obj.is_sentence_end and cp_idx == ch_obj.check_count
                    )
                    if is_sentence_end_marker:
                        continue  # 句尾marker不计入普通CP宽度
                    elif cp_idx == 0:
                        marker_char = self._checkpoint_markers["cp_first_timed"] if cp_idx < len(ch_obj.global_timestamps) else self._checkpoint_markers["cp_first_empty"]
                    else:
                        marker_char = self._checkpoint_markers["cp_multi_timed"] if cp_idx < len(ch_obj.global_timestamps) else self._checkpoint_markers["cp_multi_empty"]
                    total_cp_w += fm_checkpoint.horizontalAdvance(marker_char)
                char_widths[ci] = max(char_widths[ci], total_cp_w)
            # 句尾字符扩展该字符自身宽度的一半用于放置句尾marker（单独追踪，不混入 char_widths）
            if ch_obj.is_sentence_end:
                end_sentence_w[ci] = char_widths[ci] // 2

        # ---------- wipe 时间线（离散字符开始时间模型） ----------
        # 每个字符的 wipe 开始时间 = 该字符第一个 cp 的时间戳（global_timestamps[0]）。
        # 字符 wipe 结束时间 = 同句子内下一个有 start_ts 的字符的开始时间；
        # 若后面无 start_ts，则使用句尾时间戳（global_sentence_end_ts）。
        # 中间无 timestamp 的字符与上一个有 timestamp 的字符连读，共享同一段 wipe。
        # 一行可能含多个句子（多个 is_sentence_end），各句子独立计算段。
        start_times: dict[int, int] = {}
        for ci, ch in enumerate(characters):
            if ch.global_timestamps:
                start_times[ci] = int(ch.global_timestamps[0])

        # 按 is_sentence_end 拆分为句子范围 [(sent_start, sent_end)]，含边界
        sent_ranges: list[tuple[int, int]] = []
        s_start = 0
        for ci, ch in enumerate(characters):
            if ch.is_sentence_end:
                sent_ranges.append((s_start, ci))
                s_start = ci + 1
        if s_start < n_chars:
            sent_ranges.append((s_start, n_chars - 1))

        # 预处理：为行尾非句尾的情况准备右边界
        # 如果最后一个 sent_range 的最后一个字符不是句尾，去下一行借时间戳
        fallback_sentence_end_ts: Optional[int] = None
        if sent_ranges:
            last_sent_start, last_sent_end = sent_ranges[-1]
            last_char = characters[last_sent_end]
            if not last_char.is_sentence_end:
                next_ts = self._find_next_line_first_timestamp(idx)
                if next_ts is not None:
                    fallback_sentence_end_ts = next_ts
                elif self._duration_ms > 0:
                    fallback_sentence_end_ts = self._duration_ms

        char_wipe_times: dict = {}
        _prev_line_last_ts: Optional[int] = None  # lazy-loaded，仅行首无 leader 时使用
        for sent_start, sent_end in sent_ranges:
            # 句子内有 start_ts 的 leader 字符索引
            leaders = [ci for ci in range(sent_start, sent_end + 1) if ci in start_times]
            if not leaders:
                # 整句无时间戳，仅发生在行首。
                # 只收集行首连续的 cc=0 字符；遇到第一个 cc>0 的字符立即停止，
                # 剩余字符回到老算法（无 char_wipe_times，显示 base color）。
                no_cc_end = sent_start
                for ci in range(sent_start, sent_end + 1):
                    if characters[ci].check_count == 0:
                        no_cc_end = ci + 1
                    else:
                        break
                if no_cc_end <= sent_start:
                    continue  # 行首第一个字符就有 cc，无需处理
                # 终点：优先取本句句尾字符自身的 sentence_end_ts（cc=0 + is_sentence_end 场景），
                # 其次找本行后续第一个有时间戳字符的 ts，最后回落到 fallback
                end_ts_nl: Optional[int] = None
                sent_end_char = characters[sent_end]
                if sent_end_char.is_sentence_end and sent_end_char.global_sentence_end_ts is not None:
                    end_ts_nl = int(sent_end_char.global_sentence_end_ts)
                if end_ts_nl is None:
                    for ci in range(sent_end + 1, n_chars):
                        if ci in start_times:
                            end_ts_nl = start_times[ci]
                            break
                if end_ts_nl is None:
                    end_ts_nl = fallback_sentence_end_ts
                if end_ts_nl is None:
                    continue
                if _prev_line_last_ts is None:
                    _prev_line_last_ts = self._find_prev_line_last_timestamp(idx)
                if _prev_line_last_ts is None:
                    continue  # 上一行未完整打轴或不存在，无有效起点，不 wipe
                start_ts_nl = _prev_line_last_ts
                if end_ts_nl <= start_ts_nl:
                    continue
                seg_total_w = sum(char_widths[ci] for ci in range(sent_start, no_cc_end))
                cum_w = 0
                for ci in range(sent_start, no_cc_end):
                    w = char_widths[ci]
                    ratio = cum_w / seg_total_w if seg_total_w > 0 else 0.0
                    next_ratio = (cum_w + w) / seg_total_w if seg_total_w > 0 else 1.0
                    char_wipe_times[ci] = (
                        int(start_ts_nl + (end_ts_nl - start_ts_nl) * ratio),
                        int(start_ts_nl + (end_ts_nl - start_ts_nl) * next_ratio),
                    )
                    cum_w += w
                continue

            for i, leader in enumerate(leaders):
                next_leader = leaders[i + 1] if i + 1 < len(leaders) else None
                seg_end = (next_leader - 1) if next_leader is not None else sent_end

                if next_leader is not None:
                    end_ts = start_times[next_leader]
                else:
                    # 句子最后一段：找 sentence_end_ts
                    end_ts = None
                    for ci in range(leader, sent_end + 1):
                        if characters[ci].is_sentence_end and characters[ci].global_sentence_end_ts is not None:
                            end_ts = int(characters[ci].global_sentence_end_ts)
                            break
                if end_ts is None:
                    end_ts = fallback_sentence_end_ts
                if end_ts is None or end_ts <= start_times[leader]:
                    continue

                # 整体：leader + 它后面的无 ts 字符，按像素宽度加权从左到右分配时间
                seg_total_w = sum(char_widths[ci] for ci in range(leader, seg_end + 1))
                cum_w = 0
                for ci in range(leader, seg_end + 1):
                    w = char_widths[ci]
                    ratio = cum_w / seg_total_w if seg_total_w > 0 else 0.0
                    next_ratio = (cum_w + w) / seg_total_w if seg_total_w > 0 else 1.0
                    char_start_ts = int(start_times[leader] + (end_ts - start_times[leader]) * ratio)
                    char_end_ts = int(start_times[leader] + (end_ts - start_times[leader]) * next_ratio)
                    char_wipe_times[ci] = (char_start_ts, char_end_ts)
                    cum_w += w

            # 句子内第一个 leader 之前的无 ts 字符
            first_leader = leaders[0]
            if first_leader > sent_start:
                # 收集句首连续 cc=0 字符；遇第一个 cc>0 立即停止
                no_cc_end = sent_start
                for ci in range(sent_start, first_leader):
                    if characters[ci].check_count == 0:
                        no_cc_end = ci + 1
                    else:
                        break

                # 确定起始锚点：
                #   sent_start == 0（行首句）→ 借上一行末尾时间戳
                #   sent_start  > 0（行中句）→ 借上一句 global_sentence_end_ts
                if no_cc_end > sent_start:
                    _start_anchor: Optional[int] = None
                    if sent_start == 0:
                        if _prev_line_last_ts is None:
                            _prev_line_last_ts = self._find_prev_line_last_timestamp(idx)
                        _start_anchor = _prev_line_last_ts
                    else:
                        prev_end_ts = characters[sent_start - 1].global_sentence_end_ts
                        if prev_end_ts is not None:
                            _start_anchor = int(prev_end_ts)
                    if _start_anchor is None:
                        no_cc_end = sent_start  # 无有效锚点，不 wipe

                # 将 cc=0 字符分配到 [_start_anchor, first_leader_ts]
                old_algo_start = sent_start
                if no_cc_end > sent_start:
                    _end_nl = start_times[first_leader]
                    if _end_nl > _start_anchor:  # type: ignore[operator]
                        seg_w = sum(char_widths[ci] for ci in range(sent_start, no_cc_end))
                        cum_w = 0
                        for ci in range(sent_start, no_cc_end):
                            w = char_widths[ci]
                            ratio = cum_w / seg_w if seg_w > 0 else 0.0
                            next_ratio = (cum_w + w) / seg_w if seg_w > 0 else 1.0
                            char_wipe_times[ci] = (
                                int(_start_anchor + (_end_nl - _start_anchor) * ratio),  # type: ignore[operator]
                                int(_start_anchor + (_end_nl - _start_anchor) * next_ratio),  # type: ignore[operator]
                            )
                            cum_w += w
                    old_algo_start = no_cc_end

                # 剩余 pre-leader 字符（cc>0 未打轴）：
                # 老算法——按像素宽度加权分配到 first_leader 的 wipe 窗口内
                if old_algo_start < first_leader and first_leader in char_wipe_times:
                    leader_start_ts, leader_end_ts = char_wipe_times[first_leader]
                    pre_total_w = sum(char_widths[ci] for ci in range(old_algo_start, first_leader + 1))
                    cum_w = 0
                    for ci in range(old_algo_start, first_leader):
                        w = char_widths[ci]
                        ratio = cum_w / pre_total_w if pre_total_w > 0 else 0.0
                        next_ratio = (cum_w + w) / pre_total_w if pre_total_w > 0 else 1.0
                        char_wipe_times[ci] = (
                            int(leader_start_ts + (leader_end_ts - leader_start_ts) * ratio),
                            int(leader_start_ts + (leader_end_ts - leader_start_ts) * next_ratio),
                        )
                        cum_w += w

        # ---------- 每字符的 part 锚点序列（用于 check_count>=2 的多 checkpoint 字符） ----------
        # char_part_anchors[ci] = [ts_0, ts_1, ..., ts_N]，N = part 数
        #   - 仅在 ch.global_timestamps 数量 >= 2 时构造（即 check_count>=2 且至少打了 2 个轴）
        #   - 末尾的 ts_N 取 char_wipe_times[ci][1]（沿用现状的回退链：下一字符 ts[0] /
        #     global_sentence_end_ts / 下一行首 ts / _duration_ms）
        #   - ruby.parts 数与 check_count 不匹配也启用：渲染层会按 ruby 文本像素 N 等分回退
        char_part_anchors: dict[int, list[int]] = {}
        for ci, ch_obj in enumerate(characters):
            gts = list(ch_obj.global_timestamps)
            if len(gts) < 2:
                continue
            wt = char_wipe_times.get(ci)
            if not wt:
                continue
            seg_end_ts = wt[1]
            # 末尾锚点必须严格大于倒数第二个，否则视为脏数据，跳过
            if seg_end_ts <= gts[-1]:
                continue
            char_part_anchors[ci] = gts + [int(seg_end_ts)]

        # ---------- Ruby 整串墨水边界缓存 ----------
        # 单字符 ruby：以 ruby.text 整串的 tightBoundingRect 为准
        # 连词组 ruby：以组内所有 ruby.text 拼接后整串的 tightBoundingRect 为准
        # 返回 (ink_left, ink_width)，wipe 时把 clip 的左边界从 ruby_x 收缩到
        # ruby_x + ink_left，宽度由 ink_width × ratio 决定。
        char_ruby_ink: dict[int, tuple[int, int]] = {}
        group_ruby_ink: dict[int, tuple[int, int]] = {}
        for ci, ch_obj in enumerate(characters):
            if ci in linked_leader_groups or ci in linked_non_leader:
                continue
            ruby = ch_obj.ruby
            if ruby and ruby.text:
                char_ruby_ink[ci] = _ink_bounds(fm_ruby, ruby.text)
        for leader_ci, group in linked_leader_groups.items():
            merged_text = ""
            for _gci in group:
                _r = characters[_gci].ruby
                if _r:
                    merged_text += _r.text
            if merged_text:
                group_ruby_ink[leader_ci] = _ink_bounds(fm_ruby, merged_text)

        # ---------- 连词组 ruby 的分段 wipe 时间轴 ----------
        # 连词组的 ruby 横跨整个组（如「明日」中「日」无节奏点，与「明」合并重分配
        # 区间）。ruby 的假名边界必须来自各成员**实际打轴的** global_timestamps，
        # 而非成员主文字 wipe 窗口的末端——后者可能是重分配出来的临时分割点
        # （明的主文字在组中段就走完，若用它当 ruby 末锚点，あした 会在「明」走完时
        # 就全部走完，丢失「日」对应的时长）。
        #
        # 做法：把组内所有成员的 (mora 时间戳, mora 墨水 advance) 按序摊平，相邻时间戳
        # 即段边界；**最后一段延伸到整组真正的结束时间**（组尾成员 wipe 终点）。
        # 空 part（advance=0）消耗时间但不推进墨水；段间空隙保持不推进。
        group_ruby_wipe: dict[int, list[tuple[int, int, float, float]]] = {}
        for leader_ci, group in linked_leader_groups.items():
            # 组真正的结束时间：组尾成员主文字 wipe 终点（无则回退到组内最大终点）
            group_end: int | None = None
            _gw_last = char_wipe_times.get(group[-1])
            if _gw_last:
                group_end = _gw_last[1]
            else:
                for gci in group:
                    _wt = char_wipe_times.get(gci)
                    if _wt and (group_end is None or _wt[1] > group_end):
                        group_end = _wt[1]
            # 摊平各成员的 mora：(时间戳, 墨水 advance)
            flat: list[tuple[int, float]] = []
            for gci in group:
                ruby = characters[gci].ruby
                if not ruby or not ruby.text:
                    continue
                gts = list(characters[gci].global_timestamps)
                parts = ruby.parts if ruby.parts else []
                if gts and len(parts) == len(gts):
                    for _ts, _p in zip(gts, parts):
                        flat.append((int(_ts), float(fm_ruby.horizontalAdvance(_p.text))))
                elif gts:
                    # parts 数与时间戳数不一致：整串 advance 等分到各时间戳
                    _per = float(fm_ruby.horizontalAdvance(ruby.text)) / len(gts)
                    for _ts in gts:
                        flat.append((int(_ts), _per))
                else:
                    # 成员无自身节奏点但带 ruby：以其主文字 wipe 起点为锚整段推进
                    _wt = char_wipe_times.get(gci)
                    if _wt:
                        flat.append(
                            (_wt[0], float(fm_ruby.horizontalAdvance(ruby.text)))
                        )
            if not flat or group_end is None:
                continue
            # 时间戳须单调（防脏数据）；末锚点延伸到 group_end
            flat.sort(key=lambda it: it[0])
            total_w = sum(w for _, w in flat)
            if total_w <= 0:
                continue
            segs: list[tuple[int, int, float, float]] = []
            cum = 0.0
            for i, (_ts, _w) in enumerate(flat):
                t0 = _ts
                t1 = flat[i + 1][0] if i + 1 < len(flat) else group_end
                if t1 < t0:
                    t1 = t0
                w0 = cum / total_w
                cum += _w
                segs.append((t0, t1, w0, cum / total_w))
            group_ruby_wipe[leader_ci] = segs

        entry = {
            "v": line_version,
            "gv": self._global_version,
            "fk": font_key,
            "char_widths": char_widths,
            "char_ink_offsets": char_ink_offsets,
            "char_ink_widths": char_ink_widths,
            "end_sentence_w": end_sentence_w,
            "total_text_width": sum(char_widths) + sum(end_sentence_w.values()),
            "char_wipe_times": char_wipe_times,
            "linked_leader_groups": linked_leader_groups,
            "linked_non_leader": linked_non_leader,
            "char_part_anchors": char_part_anchors,
            "char_ruby_ink": char_ruby_ink,
            "group_ruby_ink": group_ruby_ink,
            "group_ruby_wipe": group_ruby_wipe,
        }
        self._sentence_cache[idx] = entry
        return entry

    def _find_nearest_hitbox(self, click_x: int, click_y: int) -> tuple[str, int, int, int | None] | None:
        """在行内空白区域找水平距离最近的 hitbox

        Returns:
            (hit_type, line_idx, char_idx, cp_idx) 或 None
        """
        # 收集所有 hitbox（字符和 checkpoint），按行分组
        line_hitboxes: dict[int, list[tuple[QRect, str, int, int, int | None]]] = {}
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if line_idx not in line_hitboxes:
                line_hitboxes[line_idx] = []
            line_hitboxes[line_idx].append((char_rect, "char", line_idx, char_idx, None))
        for marker_rect, line_idx, char_idx, cp_idx in self._checkpoint_hitboxes:
            if line_idx not in line_hitboxes:
                line_hitboxes[line_idx] = []
            line_hitboxes[line_idx].append((marker_rect, "cp", line_idx, char_idx, cp_idx))

        # 找到点击所在的行（垂直范围内）
        for line_idx, hitboxes in line_hitboxes.items():
            if not hitboxes:
                continue
            # 检查是否在行内垂直范围内
            first_rect = hitboxes[0][0]
            rect_top = first_rect.top()
            rect_bottom = first_rect.bottom()
            if not (rect_top <= click_y <= rect_bottom):
                continue
            # 在该行内找水平距离最近的 hitbox
            min_dist = float('inf')
            nearest = None
            for rect, hit_type, li, ci, cpi in hitboxes:
                # 计算水平距离
                if click_x < rect.left():
                    dist = rect.left() - click_x
                elif click_x > rect.right():
                    dist = click_x - rect.right()
                else:
                    dist = 0
                if dist < min_dist:
                    min_dist = dist
                    nearest = (hit_type, li, ci, cpi)
            return nearest
        return None

    def mouseDoubleClickEvent(self, a0: Optional[QMouseEvent]):
        """双击 → 跳转到时间戳"""
        if not a0 or not self._project or not self._project.sentences:
            return

        click_x = int(a0.position().x())
        click_y = int(a0.position().y())

        # 双击时停止单击定时器
        self._click_timer.stop()
        # 标记双击已处理，防止随后的 Release #2 触发单击逻辑（Qt 双击序列：
        # Press→Release→DblClick→Release，Release #2 需要跳过）
        self._double_click_handled = True

        # 优先使用快照判断双击目标（快照在单击时锁定，避免居中导致 hitbox 变化）
        # 禁用单击跳转时不使用快照，直接走当前 hitbox 判断
        if not self._disable_click_jump and self._click_snapshot is not None:
            snapshot = self._click_snapshot
            self._click_snapshot = None
            if snapshot["type"] == "cp":
                self.seek_to_checkpoint_requested.emit(
                    snapshot["line_idx"], snapshot["char_idx"], snapshot["cp_idx"]
                )
                return
            elif snapshot["type"] == "char":
                self.seek_to_char_requested.emit(
                    snapshot["line_idx"], snapshot["char_idx"]
                )
                return

        # 快照不存在时（如禁用单击跳转），回退到当前 hitbox 判断
        for marker_rect, line_idx, char_idx, cp_idx in self._checkpoint_hitboxes:
            if marker_rect.contains(click_x, click_y):
                self.seek_to_checkpoint_requested.emit(line_idx, char_idx, cp_idx)
                return

        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                self.seek_to_char_requested.emit(line_idx, char_idx)
                return

        # 双击在行内空白区域：按水平距离找最近的 hitbox
        nearest = self._find_nearest_hitbox(click_x, click_y)
        if nearest:
            hit_type, line_idx, char_idx, cp_idx = nearest
            if hit_type == "cp":
                self.seek_to_checkpoint_requested.emit(line_idx, char_idx, cp_idx)
            else:
                self.seek_to_char_requested.emit(line_idx, char_idx)
            return

    def _compute_guide_alpha(self, characters, cursor_idx: int) -> dict:
        """走字预览指引：返回 ``char_pos -> 透明度(alphaF)`` 映射。

        以打轴光标 ``cursor_idx``（``_current_char_idx``）为锚，而非时间戳分布——
        "用户正在打哪个字"的唯一真相来源是光标，回跳/seek/跳着打都能正确跟随。

        分群：每个 ``check_count > 0`` 的字符开启一个新字群，其后紧邻的
        ``check_count == 0``（无 cc，不单独打轴）字符并入该字群——即"上一个打的字
        与当前打的字之间不需要打的字"会被组合进同一个字群。

        再按光标所在字群相对位置上色（分色由 ``_draw_split_text`` 负责）：

          - 光标所在字群        = 正在打的字 → 0.5（无时间戳、走不了字，主动上色）
          - 紧邻的上一个字群    = 上一个打的字 → 0.8（避免"已完成"误解）
          - 紧邻的下一个字群    = 下一个要打的字 → 0.2

        其余字符不返回（保持正常着色/走字）。
        """
        groups: list[list[int]] = []
        char_to_group: dict[int, int] = {}
        cur: Optional[list[int]] = None
        for ci, ch in enumerate(characters):
            if ch.check_count > 0:
                cur = [ci]
                groups.append(cur)
            elif cur is not None:
                cur.append(ci)
            else:
                cur = [ci]
                groups.append(cur)
            char_to_group[ci] = len(groups) - 1
        if not groups:
            return {}

        # 光标定位到字群：越界时 clamp 到行内有效范围
        ci_clamped = max(0, min(cursor_idx, len(characters) - 1))
        cur_gi = char_to_group.get(ci_clamped, 0)

        alpha: dict[int, float] = {}
        for ci in groups[cur_gi]:
            alpha[ci] = 0.5
        if cur_gi - 1 >= 0:
            for ci in groups[cur_gi - 1]:
                alpha[ci] = 0.8
        if cur_gi + 1 < len(groups):
            for ci in groups[cur_gi + 1]:
                alpha[ci] = 0.2
        return alpha

    # ---- 绘制 ----

    def paintEvent(self, a0: Optional[QPaintEvent]):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 填充背景
        painter.fillRect(self.rect(), theme.karaoke_bg)

        # 清空 hitbox 缓存
        self._checkpoint_hitboxes = []
        self._char_hitboxes = []

        # 渲染时间：播放中主动拉取基于 perf_counter 外推的高精度时间，
        # 消除 QTimer 间隔 + Qt paint 调度带来的 ~16ms 抖动。
        if self._is_playing and self._audio_engine is not None:
            display_getter = getattr(self._audio_engine, "get_display_position_ms", None)
            current_time = (
                int(display_getter())
                if callable(display_getter)
                else self._audio_engine.get_position_ms()
            )
            self._current_time_ms = current_time
        else:
            current_time = self._current_time_ms

        if not self._project or not self._project.sentences:
            painter.setPen(theme.text_hint)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请拖入sug项目或者歌词文件或ctrl+v粘贴剪贴板上的歌词文件"
            )
            painter.end()
            return

        w, h = self.width(), self.height()
        total = len(self._project.sentences)
        line_height = h / self._visible_lines
        center_y = h / 2.0

        font_current = self._font_current
        font_context = self._font_context
        font_ruby = self._font_ruby
        font_checkpoint = self._font_checkpoint

        fm_current = self._fm_current
        fm_context = self._fm_context
        fm_ruby = self._fm_ruby
        fm_checkpoint = self._fm_checkpoint

        default_highlight = theme.default_highlight

        # 计算可见行范围（留 1 行余量避免边缘裁切）
        half_visible = self._visible_lines / 2.0 + 1
        first_visible = max(0, int(self._scroll_center_line - half_visible))
        last_visible = min(total - 1, int(self._scroll_center_line + half_visible))

        # 播放+自动滚动时，用播放行做视觉高亮，不污染编辑光标
        effective_current = (
            self._last_auto_scroll_line_idx
            if self._is_playing
            and self._auto_scroll_enabled
            and not self._auto_scroll_suspended
            and self._last_auto_scroll_line_idx >= 0
            else self._current_line_idx
        )

        for idx in range(first_visible, last_visible + 1):
            # 行中心 y 坐标
            y_center_f = center_y + (idx - self._scroll_center_line) * line_height
            y_center = int(round(y_center_f))

            # 跳过完全不可见的行
            if y_center_f < -line_height or y_center_f > h + line_height:
                continue

            line = self._project.sentences[idx]
            is_current = idx == effective_current

            # 绘制行号（左侧固定区域）
            painter.setFont(self._font_line_number)
            line_num_color = theme.line_number_current if is_current else theme.line_number_normal
            painter.setPen(line_num_color)
            line_num_text = str(idx + 1)
            line_num_w = self._fm_line_number.horizontalAdvance(line_num_text)
            painter.drawText(
                int(self._line_number_margin - line_num_w - 5),
                int(y_center),
                line_num_text,
            )

            # 根据演唱者获取行级别默认高亮颜色
            singer = (
                self._project.get_singer(line.singer_id) if line.singer_id else None
            )
            highlight_color = (
                QColor(singer.color) if singer and singer.color else default_highlight
            )

            # 预计算每个字符的 per-char singer 颜色（从 Character.singer_id 读取）
            # 值为 List[QColor]：solid 模式长度为 1，split 模式长度 2-5
            _char_singer_colors: dict = {}  # char_idx -> List[QColor] (基色列表)
            _char_complement_colors: dict = {}  # char_idx -> QColor (选中高亮色 = 演唱者补色)
            default_singer = self._project.get_default_singer()
            for ci, char in enumerate(line.characters):
                singer_obj = self._project.get_singer(char.singer_id)
                eff = singer_obj if (singer_obj and singer_obj.color) else default_singer
                if eff:
                    all_colors = [QColor(c) for c in eff.get_all_colors()]
                    comp_color = eff.complement_color or eff.color
                else:
                    all_colors = [QColor("#FFFFFF")]
                    comp_color = "#FFFFFF"
                _char_singer_colors[ci] = all_colors
                _char_complement_colors[ci] = QColor(comp_color)

            if is_current:
                main_font = font_current
                main_fm = fm_current
                base_color = theme.karaoke_text_current
            elif idx < effective_current:
                main_font = font_context
                main_fm = fm_context
                base_color = theme.karaoke_text_past
            else:
                main_font = font_context
                main_fm = fm_context
                base_color = theme.karaoke_text_future

            # 走字预览指引：仅在「设置开启 + 播放中 + 打轴光标所在行」时，对本行
            # 以光标 _current_char_idx 为锚，计算"上一个/正在/下一个"字群的过渡透明度。
            # 锚定 _current_line_idx（打轴光标行）而非 effective_current（视觉高亮行），
            # 两者在播放/自动滚动挂起等状态下可能不一致。
            if (
                self._preview_guide_enabled
                and self._is_playing
                and idx == self._current_line_idx
            ):
                guide_alpha = self._compute_guide_alpha(
                    line.characters, self._current_char_idx
                )
            else:
                guide_alpha = {}

            # 使用缓存的渲染数据（字符宽度/分组/wipe时间/连词信息）
            _rd = self._get_sentence_render_data(
                idx, line, main_fm, "cur" if is_current else "ctx"
            )
            char_widths = _rd["char_widths"]
            _char_ink_offsets = _rd["char_ink_offsets"]
            _char_ink_widths = _rd["char_ink_widths"]
            _end_sentence_w = _rd["end_sentence_w"]
            total_text_width = _rd["total_text_width"]
            char_wipe_times = _rd["char_wipe_times"]
            _linked_leader_groups = _rd["linked_leader_groups"]
            _linked_non_leader = _rd["linked_non_leader"]
            _char_part_anchors = _rd["char_part_anchors"]
            _char_ruby_ink = _rd["char_ruby_ink"]
            _group_ruby_ink = _rd["group_ruby_ink"]
            _group_ruby_wipe = _rd["group_ruby_wipe"]

            # 根据对齐方式计算起始 x 坐标
            text_area_left = self._line_number_margin + 5  # 行号区域右侧留 5px 间距
            sb_w = self._SCROLLBAR_WIDTH if self._scrollbar.isVisible() else 0
            text_area_right = w - sb_w  # 文本区域右边界，为滚动条留出空间
            available_width = text_area_right - text_area_left

            if self._alignment == "left":
                start_x = text_area_left + self._alignment_margin
            elif self._alignment == "right":
                start_x = text_area_right - total_text_width - self._alignment_margin
                # 确保不覆盖行号区域
                start_x = max(start_x, text_area_left)
            else:  # center
                start_x = text_area_left + (available_width - total_text_width) // 2

            curr_x = start_x

            # 预计算本行所有非空白字符的实际墨水上下边界（屏幕坐标）。
            # 用于分色渲染时统一色带分界线，使同行各字符的分色视觉位置一致。
            _line_ink_top = y_center - main_fm.ascent()   # 默认 fallback
            _line_ink_bottom = y_center + main_fm.descent()
            _ink_top_min = float("inf")
            _ink_bottom_max = float("-inf")
            for _ch in line.chars:
                if not _ch or not _ch.strip():
                    continue
                _br = main_fm.tightBoundingRect(_ch)
                _ink_top_min = min(_ink_top_min, y_center + _br.top())
                _ink_bottom_max = max(_ink_bottom_max, y_center + _br.bottom())
            if _ink_top_min < float("inf"):
                _line_ink_top = int(_ink_top_min)
                _line_ink_bottom = int(_ink_bottom_max) + 1

            for char_pos, ch in enumerate(line.chars):
                char_w = char_widths[char_pos]

                # 统一高亮/hitbox 矩形：以行逻辑中心 y_center_f 为唯一锚点、
                # 高度 clamp 到 int(line_height)。此前 _rect_top 在「行框顶」
                # 与「字体 ascent 顶」之间取 max()，在当前行字体放大（22pt）
                # 相邻行字体缩小（18pt）时两套锚点不一致，会让选中行下方出现
                # 大块空白（issue #9）。现统一以行中心垂直居中矩形，消除跳变。
                _rect_height = min(main_fm.height() + 4, int(line_height))
                _rect_top = int(round(y_center_f - _rect_height / 2))

                # 当前打轴位置高亮背景
                if is_current and char_pos == self._current_char_idx:
                    highlight_bg = theme.karaoke_highlight_bg
                    bg_rect = QRect(
                        int(curr_x) - 1,
                        _rect_top,
                        int(char_w) + 2,
                        _rect_height,
                    )
                    painter.fillRect(bg_rect, highlight_bg)

                # 划词选中高亮背景
                if idx == self._focus_line_idx and self._focus_char_idx >= 0:
                    sel_lo = min(self._focus_char_idx, self._focus_char_range_end)
                    sel_hi = max(self._focus_char_idx, self._focus_char_range_end)
                    if sel_lo <= char_pos <= sel_hi:
                        sel_bg = theme.karaoke_selection_bg
                        sel_rect = QRect(
                            int(curr_x) - 1,
                            _rect_top,
                            int(char_w) + 2,
                            _rect_height,
                        )
                        painter.fillRect(sel_rect, sel_bg)

                # 存储字符 hitbox 用于点击检测（与高亮矩形对齐）
                char_rect = QRect(
                    int(curr_x),
                    _rect_top,
                    int(char_w),
                    _rect_height,
                )
                self._char_hitboxes.append((char_rect, idx, char_pos))

                # Ruby — 连词组合并绘制 / 单字独立绘制
                if char_pos in _linked_non_leader:
                    pass  # Ruby 由组 leader 统一绘制
                elif char_pos in _linked_leader_groups:
                    # 连词组 leader：收集组内所有 ruby 合并绘制
                    _grp = _linked_leader_groups[char_pos]
                    _grp_rubies: list = []
                    for _gci in _grp:
                        _r = line.characters[_gci].ruby
                        if _r:
                            _grp_rubies.append(_r)
                    if _grp_rubies:
                        _merged = "".join(r.text for r in _grp_rubies)
                        _grp_w = sum(char_widths[g] for g in _grp)
                        ruby_text_w = fm_ruby.horizontalAdvance(_merged)
                        ruby_x = curr_x + (_grp_w - ruby_text_w) // 2
                        ruby_y = int(y_center - main_fm.ascent() - self._ruby_spacing)
                        # Ruby 整串墨水边界（合并后整体 tightBoundingRect）：
                        # wipe 按墨水起止点裁剪，不再扫过 ruby 首尾的透明侧 bearings。
                        _r_ink_off, _r_ink_w = _group_ruby_ink.get(char_pos, (0, ruby_text_w))
                        _r_ink_x = ruby_x + _r_ink_off
                        painter.setFont(font_ruby)
                        _rh_colors = _char_singer_colors.get(_grp[0], [highlight_color])
                        # Ruby 分色边界：基于该 ruby 串的实际墨水范围
                        _rh_br = fm_ruby.tightBoundingRect(_merged)
                        _rh_ink_top = ruby_y + _rh_br.top()
                        _rh_ink_bottom = ruby_y + _rh_br.bottom() + 1
                        # 底色：命中走字预览指引时用走字后分色 × 透明度，否则用 base_color。
                        # wipe 照常在底色之上叠加——能 wipe 的自然走字，无需任何拦截。
                        _g_alpha_ruby = guide_alpha.get(char_pos)
                        if _g_alpha_ruby is not None:
                            _draw_split_text(
                                painter, int(ruby_x), ruby_y, _merged,
                                _guide_colors(_rh_colors, _g_alpha_ruby),
                                _rh_ink_top, _rh_ink_bottom,
                            )
                        else:
                            painter.setPen(base_color)
                            painter.drawText(int(ruby_x), ruby_y, _merged)
                        # Wipe — 连词组 ruby 与原字符逻辑一致：按各成员/各 part 的
                        # 时间轴分段，空 part 不推进、段间空隙保持，墨水边缘走字（非匀速）。
                        # 缺分段数据时回退为整段线性（组首 wipe 始 → 组尾 wipe 终）。
                        _segs = _group_ruby_wipe.get(char_pos)
                        if _segs:
                            _rr = _piecewise_wipe_ratio(_segs, current_time)
                            if _rr >= 1.0:
                                _draw_split_text(
                                    painter, int(ruby_x), ruby_y, _merged,
                                    _rh_colors, _rh_ink_top, _rh_ink_bottom,
                                )
                            elif _rr > 0 and _r_ink_w > 0:
                                painter.save()
                                _rww = int(_r_ink_w * _rr)
                                painter.setClipRect(
                                    QRect(
                                        int(_r_ink_x),
                                        ruby_y - fm_ruby.ascent() - 2,
                                        _rww,
                                        fm_ruby.height() + 4,
                                    )
                                )
                                _draw_split_text(
                                    painter, int(ruby_x), ruby_y, _merged,
                                    _rh_colors, _rh_ink_top, _rh_ink_bottom,
                                )
                                painter.restore()
                        else:
                            _fw = char_wipe_times.get(_grp[0])
                            _lw = char_wipe_times.get(_grp[-1])
                            _rs = _fw[0] if _fw else None
                            _re = _lw[1] if _lw else None
                            if _rs is not None and _re is not None:
                                if current_time >= _re:
                                    _draw_split_text(
                                        painter, int(ruby_x), ruby_y, _merged,
                                        _rh_colors, _rh_ink_top, _rh_ink_bottom,
                                    )
                                elif current_time >= _rs:
                                    _rd = _re - _rs
                                    _rr = (
                                        min(1.0, (current_time - _rs) / _rd)
                                        if _rd > 0
                                        else 1.0
                                    )
                                    if _rr > 0 and _r_ink_w > 0:
                                        painter.save()
                                        _rww = int(_r_ink_w * _rr)
                                        painter.setClipRect(
                                            QRect(
                                                int(_r_ink_x),
                                                ruby_y - fm_ruby.ascent() - 2,
                                                _rww,
                                                fm_ruby.height() + 4,
                                            )
                                        )
                                        _draw_split_text(
                                            painter, int(ruby_x), ruby_y, _merged,
                                            _rh_colors, _rh_ink_top, _rh_ink_bottom,
                                        )
                                        painter.restore()
                        # 连词框：Ruby 拼接串实际总宽 vs 字符组墨水宽度取更宽者。
                        # 若 ruby 实际总宽 < 字符组墨水宽度 → 用字符墨水边界（方法2），
                        # 否则 ruby 串已经超出字符墨水范围 → 用 ruby 墨水边界（方法1）。
                        # --- 方法1：Ruby 拼接串墨水边界 ---
                        _ruby_box_left = _r_ink_x
                        _ruby_box_right = _r_ink_x + _r_ink_w
                        _ruby_box_w = ruby_text_w
                        # --- 方法2：字符组墨水边界并集 ---
                        _char_ink_left = float('inf')
                        _char_ink_right = float('-inf')
                        _cum = 0
                        for _gci in _grp:
                            _char_x = curr_x + _cum
                            _char_ink_x = _char_x + _char_ink_offsets[_gci]
                            _char_ink_left = min(_char_ink_left, _char_ink_x)
                            _char_ink_right = max(_char_ink_right, _char_ink_x + _char_ink_widths[_gci])
                            _cum += char_widths[_gci]
                        _char_box_w = _char_ink_right - _char_ink_left
                        if _ruby_box_w < _char_box_w:
                            _box_left = int(_char_ink_left)
                            _box_right = int(_char_ink_right)
                        else:
                            _box_left = int(_ruby_box_left)
                            _box_right = int(_ruby_box_right)
                        painter.save()
                        _fc = QColor(base_color)
                        _fc.setAlpha(120)
                        _fp = QPen(_fc, 1.0)
                        _fp.setStyle(Qt.PenStyle.SolidLine)
                        painter.setPen(_fp)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRoundedRect(
                            _box_left - 2,
                            ruby_y - fm_ruby.ascent() - 1,
                            (_box_right - _box_left) + 4,
                            fm_ruby.height() + 2,
                            2,
                            2,
                        )
                        painter.restore()
                else:
                    ruby = line.characters[char_pos].ruby
                    if ruby:
                        # 单字符 ruby（per-char 模型）- 渲染剥离 '#' 分组标记
                        _ruby_disp = ruby.text
                        ruby_text_w = fm_ruby.horizontalAdvance(_ruby_disp)
                        ruby_x = curr_x + (char_w - ruby_text_w) // 2
                        ruby_y = int(y_center - main_fm.ascent() - self._ruby_spacing)
                        # Ruby 文本整串墨水边界（含所有假名）
                        _r_ink_off, _r_ink_w = _char_ruby_ink.get(char_pos, (0, ruby_text_w))
                        _r_ink_x = ruby_x + _r_ink_off
                        painter.setFont(font_ruby)
                        ruby_highlight_colors = _char_singer_colors.get(
                            char_pos, [highlight_color]
                        )
                        # Ruby 分色边界：基于该 ruby 串的实际墨水范围
                        _ruby_br = fm_ruby.tightBoundingRect(_ruby_disp)
                        _ruby_ink_top = ruby_y + _ruby_br.top()
                        _ruby_ink_bottom = ruby_y + _ruby_br.bottom() + 1
                        # 底色：命中走字预览指引时用走字后分色 × 透明度，否则用 base_color。
                        # wipe 照常在底色之上叠加——能 wipe 的自然走字，无需任何拦截。
                        _g_alpha_ruby = guide_alpha.get(char_pos)
                        if _g_alpha_ruby is not None:
                            _draw_split_text(
                                painter, int(ruby_x), ruby_y, _ruby_disp,
                                _guide_colors(ruby_highlight_colors, _g_alpha_ruby),
                                _ruby_ink_top, _ruby_ink_bottom,
                            )
                        else:
                            painter.setPen(base_color)
                            painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
                        # Wipe — 优先用 part 锚点轴分段；缺锚点回退旧整段线性
                        _r_anchors = _char_part_anchors.get(char_pos)
                        if _r_anchors is not None and len(_r_anchors) >= 2:
                            _i, _sr, _n = _anchor_segment(_r_anchors, current_time)
                            if _n > 0:
                                # 计算整体进度比例 _ratio ∈ [0,1]，再乘 ink 宽度。
                                # parts 数与段数 N 匹配则按 part 实际 advance 占比，否则等分。
                                _parts = ruby.parts if ruby.parts else []
                                if len(_parts) == _n and _n > 0:
                                    _part_ws = [
                                        fm_ruby.horizontalAdvance(p.text)
                                        for p in _parts
                                    ]
                                    _total_pw = sum(_part_ws)
                                    if _total_pw > 0:
                                        _cum = sum(_part_ws[:_i])
                                        _local = _part_ws[_i] * _sr
                                        _ratio = (_cum + _local) / _total_pw
                                    else:
                                        _ratio = (_i + _sr) / _n
                                else:
                                    _ratio = (_i + _sr) / _n
                                r_wipe_w = int(_r_ink_w * _ratio) if _r_ink_w > 0 else 0
                                if _ratio >= 1.0:
                                    _draw_split_text(
                                        painter, int(ruby_x), ruby_y, _ruby_disp,
                                        ruby_highlight_colors, _ruby_ink_top, _ruby_ink_bottom,
                                    )
                                elif r_wipe_w > 0:
                                    painter.save()
                                    painter.setClipRect(
                                        QRect(
                                            int(_r_ink_x),
                                            ruby_y - fm_ruby.ascent() - 2,
                                            r_wipe_w,
                                            fm_ruby.height() + 4,
                                        )
                                    )
                                    _draw_split_text(
                                        painter, int(ruby_x), ruby_y, _ruby_disp,
                                        ruby_highlight_colors, _ruby_ink_top, _ruby_ink_bottom,
                                    )
                                    painter.restore()
                        else:
                            ruby_wipe_st = char_wipe_times.get(char_pos)
                            ruby_st = ruby_wipe_st[0] if ruby_wipe_st else None
                            if ruby_st is not None:
                                ruby_wipe_et = char_wipe_times.get(char_pos)
                                ruby_et = ruby_wipe_et[1] if ruby_wipe_et else ruby_st + 300
                                if current_time >= ruby_et:
                                    _draw_split_text(
                                        painter, int(ruby_x), ruby_y, _ruby_disp,
                                        ruby_highlight_colors, _ruby_ink_top, _ruby_ink_bottom,
                                    )
                                elif current_time >= ruby_st:
                                    r_dur = ruby_et - ruby_st
                                    r_ratio = (
                                        min(1.0, (current_time - ruby_st) / r_dur)
                                        if r_dur > 0
                                        else 1.0
                                    )
                                    if r_ratio > 0 and _r_ink_w > 0:
                                        painter.save()
                                        r_wipe_w = int(_r_ink_w * r_ratio)
                                        painter.setClipRect(
                                            QRect(
                                                int(_r_ink_x),
                                                ruby_y - fm_ruby.ascent() - 2,
                                                r_wipe_w,
                                                fm_ruby.height() + 4,
                                            )
                                        )
                                        _draw_split_text(
                                            painter, int(ruby_x), ruby_y, _ruby_disp,
                                            ruby_highlight_colors, _ruby_ink_top, _ruby_ink_bottom,
                                        )
                                        painter.restore()

                # 主文字 — 基于 checkpoint 的逐字 wipe
                painter.setFont(main_font)
                # 使用 per-char singer 颜色（List[QColor]，支持分色）
                char_colors = _char_singer_colors.get(char_pos, [highlight_color])

                # 字符在 char_w 宽度内居中（与 ruby 对齐）
                char_text_w = main_fm.horizontalAdvance(ch)
                char_draw_x = curr_x + (char_w - char_text_w) // 2

                # 走字预览指引：命中时把"未走字部分的底色"换成走字后分色 × 透明度
                # （上一个=0.8 / 正在=0.5 / 下一个=0.2）。底色之上 wipe 照常叠加——
                # 能 wipe 的字走字后色会盖住底色自然走字，打完检查时一切正常，无需拦截。
                _g_alpha = guide_alpha.get(char_pos)

                if char_pos in char_wipe_times:
                    char_time, next_time = char_wipe_times[char_pos]

                    # 决定 wipe ratio 来源：
                    # 1) 字符 part 锚点（check_count>=2 且打过轴）→ 该字符 ratio
                    # 2) 否则 → 整字线性（char_wipe_times 已按像素宽度加权分配）
                    # 注：连词组（linked_to_next）仅影响视觉层（ruby 合并绘制），
                    #     不改变 wipe 时间分配，每字独立走 char_wipe_times。
                    if char_pos in _char_part_anchors:
                        wipe_ratio = _anchor_ratio(
                            _char_part_anchors[char_pos], current_time
                        )
                    else:
                        if current_time >= next_time and next_time > char_time:
                            wipe_ratio = 1.0
                        elif current_time >= char_time:
                            duration = next_time - char_time
                            wipe_ratio = (
                                min(1.0, (current_time - char_time) / duration)
                                if duration > 0
                                else 0.0
                            )
                        else:
                            wipe_ratio = 0.0

                    if wipe_ratio >= 1.0:
                        # 已唱完 → 全高亮（支持分色）
                        _draw_split_text(
                            painter, int(char_draw_x), int(y_center),
                            ch, char_colors, _line_ink_top, _line_ink_bottom,
                        )
                    elif wipe_ratio > 0.0:
                        # 正在唱 → wipe 渐变。底色优先用指引过渡色（命中时）。
                        if _g_alpha is not None:
                            _draw_split_text(
                                painter, int(char_draw_x), int(y_center),
                                ch, _guide_colors(char_colors, _g_alpha),
                                _line_ink_top, _line_ink_bottom,
                            )
                        else:
                            painter.setPen(base_color)
                            painter.drawText(int(char_draw_x), int(y_center), ch)

                        # 按字形墨水（ink）边界裁剪，而非 advance box：
                        # - 起点 = char_draw_x + ink_left（字形真正起墨像素列）
                        # - 终点 = 起点 + ink_width × ratio（字形墨水终止像素列）
                        # 这样 wipe 不会扫过字符左右两侧的透明侧 bearings；
                        # 句尾扩展区（_esw）只用于放置 marker，不再参与 wipe。
                        ink_w = _char_ink_widths[char_pos]
                        if ink_w > 0:
                            ink_off = _char_ink_offsets[char_pos]
                            painter.save()
                            wipe_w = int(ink_w * wipe_ratio)
                            clip_rect = QRect(
                                int(char_draw_x + ink_off),
                                int(y_center - main_fm.ascent() - 5),
                                wipe_w,
                                main_fm.height() + 10,
                            )
                            painter.setClipRect(clip_rect)
                            # _draw_split_text 内部用 IntersectClip，与 wipe clip 正确叠加
                            _draw_split_text(
                                painter, int(char_draw_x), int(y_center),
                                ch, char_colors, _line_ink_top, _line_ink_bottom,
                            )
                            painter.restore()
                        # ink_w == 0（空格/全角空格/NBSP/Tab 等空白字符）：
                        # 没有可见墨水，跳过 clip 绘制，wipe 期间保持 base_color 即可。
                    else:
                        # 未唱 → 基色（命中指引时改用过渡色）
                        if _g_alpha is not None:
                            _draw_split_text(
                                painter, int(char_draw_x), int(y_center),
                                ch, _guide_colors(char_colors, _g_alpha),
                                _line_ink_top, _line_ink_bottom,
                            )
                        else:
                            painter.setPen(base_color)
                            painter.drawText(int(char_draw_x), int(y_center), ch)
                else:
                    # 不在任何字符组内 → 基色（命中指引时改用过渡色）
                    if _g_alpha is not None:
                        _draw_split_text(
                            painter, int(char_draw_x), int(y_center),
                            ch, _guide_colors(char_colors, _g_alpha),
                            _line_ink_top, _line_ink_bottom,
                        )
                    else:
                        painter.setPen(base_color)
                        painter.drawText(int(char_draw_x), int(y_center), ch)

                # 当前打轴位置指示线
                if is_current and char_pos == self._current_char_idx:
                    _esw = _end_sentence_w.get(char_pos, 0)
                    painter.setPen(highlight_color)
                    painter.drawLine(
                        int(curr_x),
                        int(y_center + main_fm.descent() + 2),
                        int(curr_x + char_w + _esw),
                        int(y_center + main_fm.descent() + 2),
                    )

                # Checkpoint 标记（逐 checkpoint 绘制）
                # 句尾marker独立于普通marker，绘制在字符右侧扩展区域
                ch_obj = line.characters[char_pos]
                if ch_obj.total_timing_points > 0:
                    painter.setFont(font_checkpoint)

                    # 普通markers（不含句尾marker）
                    regular_markers = []
                    for cp_idx in range(ch_obj.check_count):
                        has_timed = cp_idx < len(ch_obj.global_timestamps)
                        if cp_idx == 0:
                            marker_char = self._checkpoint_markers["cp_first_timed"] if has_timed else self._checkpoint_markers["cp_first_empty"]
                        else:
                            marker_char = self._checkpoint_markers["cp_multi_timed"] if has_timed else self._checkpoint_markers["cp_multi_empty"]
                        regular_markers.append((cp_idx, marker_char, has_timed))

                    # 左对齐排列普通marker（在原始字符宽度内）
                    mx = curr_x
                    marker_y = int(y_center + main_fm.descent() + 14)

                    for cp_idx, marker_char, has_timed in regular_markers:
                        is_selected = (
                            ch_obj.selected_checkpoint_idx == cp_idx
                        )
                        if is_selected:
                            color = _char_singer_colors.get(char_pos, [highlight_color])[0]
                        elif not has_timed:
                            color = theme.karaoke_text_current
                        else:
                            color = base_color

                        mw = fm_checkpoint.horizontalAdvance(marker_char)

                        painter.setPen(color)
                        painter.drawText(int(mx), marker_y, marker_char)

                        marker_rect = QRect(
                            int(mx),
                            marker_y - fm_checkpoint.ascent(),
                            int(mw),
                            fm_checkpoint.height(),
                        )
                        self._checkpoint_hitboxes.append(
                            (marker_rect, idx, char_pos, cp_idx)
                        )

                        mx += mw

                    # 句尾marker：独立绘制在字符右侧扩展区域
                    if ch_obj.is_sentence_end:
                        se_cp_idx = ch_obj.check_count
                        has_timed = ch_obj.global_sentence_end_ts is not None
                        marker_char = self._checkpoint_markers["cp_sentence_end_timed"] if has_timed else self._checkpoint_markers["cp_sentence_end_empty"]

                        is_selected = (
                            ch_obj.selected_checkpoint_idx == se_cp_idx
                        )
                        if is_selected:
                            color = _char_singer_colors.get(char_pos, [highlight_color])[0]
                        elif not has_timed:
                            color = theme.karaoke_text_current
                        else:
                            color = base_color

                        # 扩展区域：字符右侧，宽度为半字符宽
                        se_area_x = curr_x + char_w
                        se_area_w = _end_sentence_w.get(char_pos, 0)

                        painter.setPen(color)
                        painter.drawText(int(se_area_x), marker_y, marker_char)

                        # hitbox覆盖整个扩展区域（高度与字符区域一致）
                        se_rect = QRect(
                            int(se_area_x),
                            _rect_top,
                            int(se_area_w),
                            _rect_height,
                        )
                        self._checkpoint_hitboxes.append(
                            (se_rect, idx, char_pos, se_cp_idx)
                        )

                curr_x += char_w + _end_sentence_w.get(char_pos, 0)
