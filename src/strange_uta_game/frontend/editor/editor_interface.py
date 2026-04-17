"""编辑器界面。

打轴主界面，包含：
- 播放控制栏（播放/暂停/停止/进度/速度/音量）
- 工具栏（保存/加载音频/撤销/重做）
- 卡拉OK 歌词预览区
- 时间轴
- 状态栏
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QFrame,
    QDialog,
    QFormLayout,
    QLineEdit,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QRect
from PyQt6.QtGui import (
    QFont,
    QColor,
    QPainter,
    QPen,
    QBrush,
    QPaintEvent,
    QMouseEvent,
    QKeyEvent,
    QFontMetrics,
)

from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    Slider,
    DoubleSpinBox,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    SimpleCardWidget,
    ToolButton,
    PrimaryToolButton,
)

from typing import Optional, List
from pathlib import Path
import time

from strange_uta_game.backend.domain import (
    Project,
    LyricLine,
    TimeTag,
    Ruby,
    CheckpointConfig,
)
from strange_uta_game.backend.application import (
    CheckpointPosition,
    TimingService,
)
from strange_uta_game.backend.infrastructure.audio import AudioLoadError


# ──────────────────────────────────────────────
# 播放控制栏
# ──────────────────────────────────────────────
class TransportBar(QFrame):
    """播放控制栏 - 紧凑水平布局"""

    play_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    seek_requested = pyqtSignal(int)
    speed_changed = pyqtSignal(float)
    volume_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._current_ms = 0
        self._is_playing = False
        self.setFixedHeight(56)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        # 停止
        self.btn_stop = ToolButton(FIF.CANCEL, self)
        self.btn_stop.setFixedSize(40, 40)
        self.btn_stop.setIconSize(QSize(18, 18))
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.btn_stop)

        # 播放/暂停
        self.btn_play = PrimaryToolButton(FIF.PLAY, self)
        self.btn_play.setFixedSize(40, 40)
        self.btn_play.setIconSize(QSize(18, 18))
        self.btn_play.clicked.connect(self._on_play_clicked)
        layout.addWidget(self.btn_play)

        # 时间
        self.lbl_time = QLabel("00:00.00 / 00:00.00")
        self.lbl_time.setStyleSheet("font-family: monospace; font-size: 12px;")
        self.lbl_time.setMinimumWidth(140)
        layout.addWidget(self.lbl_time)

        # 进度条
        self.slider_progress = Slider(Qt.Orientation.Horizontal, self)
        self.slider_progress.setRange(0, 10000)
        self.slider_progress.setValue(0)
        self.slider_progress.sliderReleased.connect(self._on_seek)
        layout.addWidget(self.slider_progress, stretch=1)

        # 速度
        lbl_speed = QLabel("速度")
        lbl_speed.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(lbl_speed)
        self.spin_speed = DoubleSpinBox(self)
        self.spin_speed.setRange(0.5, 2.0)
        self.spin_speed.setSingleStep(0.1)
        self.spin_speed.setValue(1.0)
        self.spin_speed.setDecimals(1)
        self.spin_speed.setSuffix("x")
        self.spin_speed.setFixedWidth(80)
        self.spin_speed.valueChanged.connect(self.speed_changed.emit)
        layout.addWidget(self.spin_speed)

        # 音量
        lbl_vol = QLabel("音量")
        lbl_vol.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(lbl_vol)
        self.slider_volume = Slider(Qt.Orientation.Horizontal, self)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(100)
        self.slider_volume.setFixedWidth(100)
        self.slider_volume.valueChanged.connect(self.volume_changed.emit)
        layout.addWidget(self.slider_volume)

    def _on_play_clicked(self):
        if self._is_playing:
            self.pause_clicked.emit()
        else:
            self.play_clicked.emit()

    def _on_seek(self):
        if self._duration_ms > 0:
            ratio = self.slider_progress.value() / 10000
            self.seek_requested.emit(int(ratio * self._duration_ms))

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self._update_label()

    def set_position(self, ms: int):
        self._current_ms = ms
        if self._duration_ms > 0:
            self.slider_progress.setValue(int((ms / self._duration_ms) * 10000))
        self._update_label()

    def set_playing(self, playing: bool):
        self._is_playing = playing
        self.btn_play.setIcon(FIF.PAUSE if playing else FIF.PLAY)

    def _update_label(self):
        def fmt(ms):
            s = ms // 1000
            c = (ms % 1000) // 10
            return f"{s // 60:02d}:{s % 60:02d}.{c:02d}"

        self.lbl_time.setText(f"{fmt(self._current_ms)} / {fmt(self._duration_ms)}")


# ──────────────────────────────────────────────
# 工具栏
# ──────────────────────────────────────────────
class EditorToolBar(QFrame):
    """编辑器工具栏 - 保存/加载音频/撤销/重做"""

    save_clicked = pyqtSignal()
    load_audio_clicked = pyqtSignal()
    undo_clicked = pyqtSignal()
    redo_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self.btn_save = PushButton("保存项目", self)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setFixedHeight(32)
        self.btn_save.clicked.connect(self.save_clicked.emit)
        layout.addWidget(self.btn_save)

        self.btn_load_audio = PushButton("加载音频", self)
        self.btn_load_audio.setIcon(FIF.MUSIC)
        self.btn_load_audio.setFixedHeight(32)
        self.btn_load_audio.clicked.connect(self.load_audio_clicked.emit)
        layout.addWidget(self.btn_load_audio)

        layout.addSpacing(10)

        self.btn_undo = PushButton("撤销", self)
        self.btn_undo.setIcon(FIF.LEFT_ARROW)
        self.btn_undo.setFixedHeight(32)
        self.btn_undo.clicked.connect(self.undo_clicked.emit)
        layout.addWidget(self.btn_undo)

        self.btn_redo = PushButton("重做", self)
        self.btn_redo.setIcon(FIF.RIGHT_ARROW)
        self.btn_redo.setFixedHeight(32)
        self.btn_redo.clicked.connect(self.redo_clicked.emit)
        layout.addWidget(self.btn_redo)

        layout.addStretch()

        # 状态标签
        self.lbl_audio = QLabel("未加载音频")
        self.lbl_audio.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(self.lbl_audio)


# ──────────────────────────────────────────────
# 卡拉OK 歌词预览
# ──────────────────────────────────────────────
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
    seek_to_char_requested = pyqtSignal(int, int)  # line_idx, char_idx (double-click)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._current_line_idx = 0
        self._current_char_idx = 0
        self._current_time_ms = 0
        self._visible_lines = 7  # 视口内可见行数（决定行高）
        self._scroll_center_line: float = 0.0  # 视口中央对应的行索引
        self._checkpoint_hitboxes: list = []  # [(QRect, line_idx, char_idx, cp_idx)]
        self._char_hitboxes: list = []  # [(QRect, line_idx, char_idx)]
        self.setMinimumHeight(400)
        self.setMouseTracking(True)

        # 缓存字体和 QFontMetrics，避免每帧重建
        self._font_current = QFont("Microsoft YaHei", 22, QFont.Weight.Bold)
        self._font_context = QFont("Microsoft YaHei", 18)
        self._font_ruby = QFont("Microsoft YaHei", 10)
        self._font_checkpoint = QFont("Microsoft YaHei", 10)
        self._fm_current = QFontMetrics(self._font_current)
        self._fm_context = QFontMetrics(self._font_context)
        self._fm_ruby = QFontMetrics(self._font_ruby)
        self._fm_checkpoint = QFontMetrics(self._font_checkpoint)

    def set_project(self, project: Project):
        self._project = project
        self._scroll_center_line = 0.0
        self._update_display()

    def set_current_position(self, line_idx: int, char_idx: int = 0):
        self._current_line_idx = line_idx
        self._current_char_idx = char_idx
        # 自动跟随：当前行始终居中
        self._scroll_center_line = float(line_idx)
        self._update_display()

    def set_current_time_ms(self, time_ms: int):
        self._current_time_ms = time_ms
        self.update()

    def _update_display(self):
        self.update()

    # ---- 滚动 ----

    def wheelEvent(self, a0):
        """鼠标滚轮滚动浏览歌词"""
        if not a0 or not self._project or not self._project.lines:
            return
        delta = a0.angleDelta().y()
        # 每个滚轮 notch（120 单位）滚动 1 行
        self._scroll_center_line -= delta / 120.0
        total = len(self._project.lines)
        self._scroll_center_line = max(
            0.0, min(float(total - 1), self._scroll_center_line)
        )
        self.update()

    # ---- 点击 ----

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if not a0 or not self._project or not self._project.lines:
            return

        click_x = int(a0.position().x())
        click_y = int(a0.position().y())

        # 优先检查 checkpoint 标记的点击
        for marker_rect, line_idx, char_idx, cp_idx in self._checkpoint_hitboxes:
            if marker_rect.contains(click_x, click_y):
                self.checkpoint_clicked.emit(line_idx, char_idx, cp_idx)
                return

        # 检查字符文本点击 → 跳转到该字符的第一个 checkpoint
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                self.checkpoint_clicked.emit(line_idx, char_idx, 0)
                return

        # 回退到行级别点击：根据 y 坐标反算行索引
        h = self.height()
        line_height = h / self._visible_lines
        center_y = h / 2.0
        # 点击位置对应的行索引（浮点）
        clicked_line = self._scroll_center_line + (click_y - center_y) / line_height
        target_idx = int(round(clicked_line))
        total = len(self._project.lines)
        if 0 <= target_idx < total:
            self.line_clicked.emit(target_idx)

    def mouseDoubleClickEvent(self, a0: Optional[QMouseEvent]):
        """双击字符 → 跳转到该字符 checkpoint 前 3 秒"""
        if not a0 or not self._project or not self._project.lines:
            return

        click_x = int(a0.position().x())
        click_y = int(a0.position().y())

        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                self.seek_to_char_requested.emit(line_idx, char_idx)
                return

    # ---- 绘制 ----

    def paintEvent(self, a0: Optional[QPaintEvent]):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 填充背景
        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        # 清空 hitbox 缓存
        self._checkpoint_hitboxes = []
        self._char_hitboxes = []

        if not self._project or not self._project.lines:
            painter.setPen(QColor("#999"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请创建或打开项目"
            )
            painter.end()
            return

        w, h = self.width(), self.height()
        total = len(self._project.lines)
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

        highlight_color = QColor("#FF6B6B")

        # 计算可见行范围（留 1 行余量避免边缘裁切）
        half_visible = self._visible_lines / 2.0 + 1
        first_visible = max(0, int(self._scroll_center_line - half_visible))
        last_visible = min(total - 1, int(self._scroll_center_line + half_visible))

        for idx in range(first_visible, last_visible + 1):
            # 行中心 y 坐标
            y_center_f = center_y + (idx - self._scroll_center_line) * line_height
            y_center = int(y_center_f)

            # 跳过完全不可见的行
            if y_center_f < -line_height or y_center_f > h + line_height:
                continue

            line = self._project.lines[idx]
            is_current = idx == self._current_line_idx

            if is_current:
                main_font = font_current
                main_fm = fm_current
                base_color = QColor("black")
            elif idx < self._current_line_idx:
                main_font = font_context
                main_fm = fm_context
                base_color = QColor("#aaa")
            else:
                main_font = font_context
                main_fm = fm_context
                base_color = QColor("#666")

            char_widths = []
            for ch in line.chars:
                char_widths.append(main_fm.horizontalAdvance(ch))

            total_text_width = sum(char_widths)
            start_x = (w - total_text_width) // 2
            curr_x = start_x

            cps_in_line = {cp.char_idx: cp for cp in line.checkpoints}
            tags_in_line: dict = {}
            for t in line.timetags:
                if t.char_idx not in tags_in_line:
                    tags_in_line[t.char_idx] = []
                tags_in_line[t.char_idx].append(t)

            # 预计算每个字符的起始时间（第一个 checkpoint 的时间戳）
            char_start_times: dict = {}
            for ci in range(len(line.chars)):
                ci_tags = tags_in_line.get(ci, [])
                if ci_tags:
                    first_cp_tags = [t for t in ci_tags if t.checkpoint_idx == 0]
                    if first_cp_tags:
                        char_start_times[ci] = first_cp_tags[0].timestamp_ms
                    else:
                        char_start_times[ci] = min(t.timestamp_ms for t in ci_tags)

            for char_pos, ch in enumerate(line.chars):
                char_w = char_widths[char_pos]

                # 当前打轴位置高亮背景
                if is_current and char_pos == self._current_char_idx:
                    highlight_bg = QColor("#FFE0E0")
                    bg_rect = QRect(
                        int(curr_x) - 1,
                        int(y_center - main_fm.ascent()) - 2,
                        int(char_w) + 2,
                        main_fm.height() + 4,
                    )
                    painter.fillRect(bg_rect, highlight_bg)

                # 存储字符 hitbox 用于点击检测
                char_rect = QRect(
                    int(curr_x),
                    int(y_center - main_fm.ascent()),
                    int(char_w),
                    main_fm.height(),
                )
                self._char_hitboxes.append((char_rect, idx, char_pos))

                # Ruby
                ruby = line.get_ruby_for_char(char_pos)
                if ruby and ruby.start_idx == char_pos:
                    ruby_chars_w = sum(char_widths[char_pos : ruby.end_idx])
                    ruby_text_w = fm_ruby.horizontalAdvance(ruby.text)
                    ruby_x = curr_x + (ruby_chars_w - ruby_text_w) // 2
                    painter.setFont(font_ruby)
                    painter.setPen(base_color)
                    painter.drawText(
                        int(ruby_x),
                        int(y_center - main_fm.ascent() - 4),
                        ruby.text,
                    )

                    # 连词Ruby标注 — 多字符合并时画边框
                    if ruby.end_idx - ruby.start_idx > 1:
                        painter.save()
                        frame_color = QColor(base_color)
                        frame_color.setAlpha(120)
                        pen = QPen(frame_color, 1.0)
                        pen.setStyle(Qt.PenStyle.SolidLine)
                        painter.setPen(pen)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        # Ruby text bounding rect with small padding
                        rx = int(ruby_x) - 2
                        ry = int(y_center - main_fm.ascent() - 4 - fm_ruby.ascent()) - 1
                        rw = int(ruby_text_w) + 4
                        rh = fm_ruby.height() + 2
                        painter.drawRoundedRect(rx, ry, rw, rh, 2, 2)
                        painter.restore()

                # 主文字 — 基于 checkpoint 的逐字 wipe
                painter.setFont(main_font)

                if char_pos in char_start_times:
                    char_time = char_start_times[char_pos]
                    cp = cps_in_line.get(char_pos)

                    if cp and cp.is_line_end:
                        # 句尾字符：start = cp_idx=0 的 tag 时间
                        #           end = cp_idx=(check_count-1) 的 tag 时间（release）
                        char_tags = tags_in_line.get(char_pos, [])
                        release_tags = [
                            t
                            for t in char_tags
                            if t.checkpoint_idx == cp.check_count - 1
                        ]
                        next_time = (
                            release_tags[0].timestamp_ms
                            if release_tags
                            else char_time + 300
                        )
                    else:
                        # 非句尾字符：start = 自己的 cp_idx=0 tag
                        #             end = 下一个有 timetag 字符的 cp_idx=0 tag
                        next_time = None
                        for next_ci in range(char_pos + 1, len(line.chars)):
                            if next_ci in char_start_times:
                                next_time = char_start_times[next_ci]
                                break

                        if next_time is None:
                            next_time = char_time + 300

                    if self._current_time_ms >= next_time:
                        # 已唱完 → 全高亮
                        painter.setPen(highlight_color)
                        painter.drawText(int(curr_x), int(y_center), ch)
                    elif self._current_time_ms >= char_time:
                        # 正在唱 → wipe 渐变
                        painter.setPen(base_color)
                        painter.drawText(int(curr_x), int(y_center), ch)

                        duration = next_time - char_time
                        if duration > 0:
                            wipe_ratio = min(
                                1.0,
                                (self._current_time_ms - char_time) / duration,
                            )
                        else:
                            wipe_ratio = 1.0

                        if wipe_ratio > 0:
                            painter.save()
                            wipe_w = int(char_w * wipe_ratio)
                            clip_rect = QRect(
                                int(curr_x),
                                int(y_center - main_fm.ascent() - 5),
                                wipe_w,
                                main_fm.height() + 10,
                            )
                            painter.setClipRect(clip_rect)
                            painter.setPen(highlight_color)
                            painter.drawText(int(curr_x), int(y_center), ch)
                            painter.restore()
                    else:
                        # 未唱 → 基色
                        painter.setPen(base_color)
                        painter.drawText(int(curr_x), int(y_center), ch)
                else:
                    # 无 timetag → 基色
                    painter.setPen(base_color)
                    painter.drawText(int(curr_x), int(y_center), ch)

                # 当前打轴位置指示线
                if is_current and char_pos == self._current_char_idx:
                    painter.setPen(highlight_color)
                    painter.drawLine(
                        int(curr_x),
                        int(y_center + main_fm.descent() + 2),
                        int(curr_x + char_w),
                        int(y_center + main_fm.descent() + 2),
                    )

                # Checkpoint 标记（逐 checkpoint 绘制）
                if char_pos in cps_in_line:
                    cp = cps_in_line[char_pos]
                    painter.setFont(font_checkpoint)

                    markers = []
                    for cp_idx in range(cp.check_count):
                        has_timed = any(
                            t.checkpoint_idx == cp_idx
                            for t in tags_in_line.get(char_pos, [])
                        )

                        if cp.is_line_end:
                            marker_char = "。"
                        elif cp_idx == 0:
                            marker_char = "▶" if has_timed else "▷"
                        else:
                            marker_char = "▮" if has_timed else "▯"

                        markers.append((marker_char, has_timed))

                    # 居中排列所有 marker
                    total_markers_w = sum(
                        fm_checkpoint.horizontalAdvance(m[0]) for m in markers
                    )
                    mx = curr_x + (char_w - total_markers_w) // 2
                    marker_y = int(y_center + main_fm.descent() + 14)

                    for cp_idx, (marker_char, has_timed) in enumerate(markers):
                        color = QColor("#FF6B6B") if has_timed else QColor("#ccc")
                        painter.setPen(color)

                        mw = fm_checkpoint.horizontalAdvance(marker_char)
                        painter.drawText(int(mx), marker_y, marker_char)

                        # 存储 hitbox 用于点击检测
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

                curr_x += char_w


class CharEditDialog(QDialog):
    """注音编辑对话框 — 支持连词（Ruby 合并/拆分）

    连词功能：用 + 号将相邻字符合并为一个 Ruby 对象。
    例如 "昨日" → Ruby(text="きのう", start_idx=0, end_idx=2)

    UI 布局：
    - 当前字符显示（只读）
    - 注音文本编辑
    - 连词：+ 号合并下一个字符（checkbox）
    - 拆分连词按钮（仅当前是连词时显示）
    """

    def __init__(self, line: "LyricLine", char_idx: int, parent=None):
        super().__init__(parent)
        self._line = line
        self._char_idx = char_idx
        self._modified = False

        self.setWindowTitle("编辑注音")
        self.resize(360, 220)
        self.setFont(QFont("Microsoft YaHei", 10))

        form = QFormLayout(self)

        # 当前字符（只读）
        self._ruby = line.get_ruby_for_char(char_idx)
        if self._ruby and self._ruby.start_idx == char_idx:
            # 显示连词覆盖的所有字符
            covered = "".join(line.chars[self._ruby.start_idx : self._ruby.end_idx])
            display = " + ".join(line.chars[self._ruby.start_idx : self._ruby.end_idx])
        else:
            covered = line.chars[char_idx]
            display = covered

        lbl_char = QLabel(display)
        lbl_char.setStyleSheet("font-size: 16px; font-weight: bold;")
        form.addRow("字符:", lbl_char)

        # 注音编辑
        self.edit_ruby = QLineEdit(self._ruby.text if self._ruby else "")
        self.edit_ruby.setPlaceholderText("输入注音（留空则删除注音）")
        form.addRow("注音:", self.edit_ruby)

        # 连词选项 — 仅当下一个字符存在且当前没有跨字符 Ruby 时显示
        from PyQt6.QtWidgets import QCheckBox

        self._is_merged = self._ruby and (self._ruby.end_idx - self._ruby.start_idx) > 1
        self._merge_start = self._ruby.start_idx if self._ruby else char_idx

        self.chk_merge = QCheckBox("连词（与下一个字符合并注音）", self)
        can_merge_next = char_idx + 1 < len(line.chars)
        if self._is_merged:
            self.chk_merge.setChecked(True)
            self.chk_merge.setEnabled(True)
        elif can_merge_next:
            self.chk_merge.setChecked(False)
            self.chk_merge.setEnabled(True)
        else:
            self.chk_merge.setChecked(False)
            self.chk_merge.setEnabled(False)
        self.chk_merge.stateChanged.connect(self._on_merge_toggled)
        form.addRow("", self.chk_merge)

        # 连词范围提示
        if self._is_merged and self._ruby:
            range_text = " + ".join(
                line.chars[self._ruby.start_idx : self._ruby.end_idx]
            )
            lbl_range = QLabel(f"当前连词范围: {range_text}")
            lbl_range.setStyleSheet("font-size: 11px; color: gray;")
            form.addRow("", lbl_range)

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

    def _on_merge_toggled(self, state):
        """连词复选框切换时，自动拼接覆盖范围内所有字符的读音。"""
        if state == Qt.CheckState.Checked.value:
            # 合并：收集当前字符和下一个字符的读音并拼接
            parts = []
            if self._ruby and (self._ruby.end_idx - self._ruby.start_idx) > 1:
                # 已有连词时，范围是 old ruby 的范围
                merge_start = self._ruby.start_idx
                merge_end = self._ruby.end_idx
            else:
                merge_start = self._char_idx
                merge_end = self._char_idx + 2
            i = merge_start
            while i < min(merge_end, len(self._line.chars)):
                r = self._line.get_ruby_for_char(i)
                if r and r.text:
                    parts.append(r.text)
                    # 跳到该 ruby 末尾之后，避免多字符 ruby 被重复收集
                    i = r.end_idx
                else:
                    i += 1
            if parts:
                self.edit_ruby.setText("".join(parts))
        else:
            # 取消合并：恢复为单个字符的读音
            if self._ruby:
                self.edit_ruby.setText(self._ruby.text)
            else:
                self.edit_ruby.setText("")

    def _on_accept(self):
        new_ruby_text = self.edit_ruby.text().strip()
        want_merge = self.chk_merge.isChecked()
        old_ruby = self._ruby

        # 确定新的 Ruby 范围
        if want_merge:
            if old_ruby and (old_ruby.end_idx - old_ruby.start_idx) > 1:
                # 保持现有连词范围
                new_start = old_ruby.start_idx
                new_end = old_ruby.end_idx
            else:
                # 新建连词：当前字符 + 下一个字符
                new_start = self._char_idx
                new_end = self._char_idx + 2
                if new_end > len(self._line.chars):
                    new_end = len(self._line.chars)
        else:
            # 单字符 Ruby
            new_start = self._char_idx
            new_end = self._char_idx + 1

        # 移除新范围内所有重叠的 Ruby（而非仅移除当前字符的 Ruby）
        overlapping = [
            r
            for r in self._line.rubies
            if r.start_idx < new_end and r.end_idx > new_start
        ]
        for r in overlapping:
            self._line.rubies.remove(r)
            self._modified = True

        if not want_merge and self._is_merged and old_ruby:
            # 取消连词 — 将多字符 Ruby 拆分，按 mora 均分注音到各个字符
            from strange_uta_game.backend.infrastructure.parsers.inline_format import (
                split_ruby_for_checkpoints,
            )

            text = old_ruby.text
            count = old_ruby.end_idx - old_ruby.start_idx
            if count > 0:
                split_parts = split_ruby_for_checkpoints(text, count)
                for i, part in enumerate(split_parts):
                    if part:
                        try:
                            self._line.add_ruby(
                                Ruby(
                                    text=part,
                                    start_idx=old_ruby.start_idx + i,
                                    end_idx=old_ruby.start_idx + i + 1,
                                )
                            )
                        except Exception:
                            pass
            self._modified = True
            self.accept()
            return

        # 设置新的注音
        if new_ruby_text:
            try:
                self._line.add_ruby(
                    Ruby(
                        text=new_ruby_text,
                        start_idx=new_start,
                        end_idx=new_end,
                    )
                )
                self._modified = True
            except Exception:
                # 如果有重叠等验证错误，忽略
                pass
        else:
            # 注音文本为空 → 已删除旧 Ruby（如果有的话）
            if overlapping:
                self._modified = True

        self.accept()

    def was_modified(self) -> bool:
        return self._modified


# ──────────────────────────────────────────────
# 时间轴
# ──────────────────────────────────────────────
class TimelineWidget(QWidget):
    """时间轴 - 显示时间网格 + 时间标签 + 播放位置"""

    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._current_ms = 0
        self._time_tags: List[int] = []
        self.setMinimumHeight(80)
        self.setMaximumHeight(120)

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self.update()

    def set_position(self, ms: int):
        self._current_ms = ms
        self.update()

    def set_time_tags(self, tags_ms: List[int]):
        self._time_tags = sorted(tags_ms)
        self.update()

    def paintEvent(self, a0: Optional[QPaintEvent]):
        _ = a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QColor("#F0F0F0"))

        if self._duration_ms <= 0:
            painter.setPen(QColor("#999"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请加载音频文件"
            )
            return

        # 时间网格（10 秒间隔）
        painter.setPen(QPen(QColor("#DDD"), 1))
        for t in range(0, self._duration_ms + 1, 10000):
            x = int((t / self._duration_ms) * w)
            painter.drawLine(x, 0, x, h)

        # 时间标签标记
        painter.setPen(QPen(QColor("#FF6B6B"), 2))
        for tag in self._time_tags:
            x = int((tag / self._duration_ms) * w)
            painter.drawLine(x, int(h * 0.3), x, int(h * 0.7))

        # 播放位置
        if 0 <= self._current_ms <= self._duration_ms:
            x = int((self._current_ms / self._duration_ms) * w)
            painter.setPen(QPen(QColor("#4ECDC4"), 2))
            painter.drawLine(x, 0, x, h)
            painter.setBrush(QBrush(QColor("#4ECDC4")))
            painter.drawEllipse(x - 4, h // 2 - 4, 8, 8)

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None:
            return
        if self._duration_ms <= 0:
            return
        ratio = max(0.0, min(1.0, a0.position().x() / self.width()))
        self.seek_requested.emit(int(ratio * self._duration_ms))


# ──────────────────────────────────────────────
# 编辑器主界面
# ──────────────────────────────────────────────
class EditorInterface(QWidget):
    """编辑器界面主容器"""

    project_saved = pyqtSignal()
    _position_changed_signal = pyqtSignal(int, int, object)
    _checkpoint_moved_signal = pyqtSignal(object)
    _timetag_added_signal = pyqtSignal()
    _timing_error_signal = pyqtSignal(str, str)
    _line_end_started_signal = pyqtSignal(int, int, int)
    _line_end_finished_signal = pyqtSignal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._timing_service: Optional[TimingService] = None
        self._audio_file_path: Optional[str] = None
        self._current_line_idx = 0
        self._space_pressed = False
        self._last_position_update_time = 0.0  # 60fps UI 节流
        self._fast_forward_ms = 5000
        self._rewind_ms = 5000
        self._key_map = {}  # key_string -> action_name, populated by _apply_settings
        self._init_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._bind_callback_signals()

    def _bind_callback_signals(self):
        self._position_changed_signal.connect(self._handle_position_changed)
        self._checkpoint_moved_signal.connect(self._handle_checkpoint_moved)
        self._timetag_added_signal.connect(self._handle_timetag_added)
        self._timing_error_signal.connect(self._handle_timing_error)
        self._line_end_started_signal.connect(self._handle_line_end_recording_started)
        self._line_end_finished_signal.connect(self._handle_line_end_recording_finished)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 5)
        layout.setSpacing(8)

        # 1) 工具栏
        self.toolbar = EditorToolBar(self)
        self.toolbar.save_clicked.connect(self._on_save)
        self.toolbar.load_audio_clicked.connect(self._on_load_audio)
        self.toolbar.undo_clicked.connect(self._on_undo)
        self.toolbar.redo_clicked.connect(self._on_redo)
        layout.addWidget(self.toolbar)

        # 2) 播放控制栏
        self.transport = TransportBar(self)
        self.transport.play_clicked.connect(self._on_play)
        self.transport.pause_clicked.connect(self._on_pause)
        self.transport.stop_clicked.connect(self._on_stop)
        self.transport.seek_requested.connect(self._on_seek)
        self.transport.speed_changed.connect(self._on_speed_changed)
        self.transport.volume_changed.connect(self._on_volume_changed)
        layout.addWidget(self.transport)

        # 3) 时间轴
        self.timeline = TimelineWidget(self)
        self.timeline.seek_requested.connect(self._on_seek)
        layout.addWidget(self.timeline)

        # 4) 歌词预览（占主要空间）
        self.preview = KaraokePreview(self)
        self.preview.line_clicked.connect(self._on_line_clicked)
        self.preview.checkpoint_clicked.connect(self._on_checkpoint_clicked)
        self.preview.char_edit_requested.connect(self._on_char_edit_requested)
        self.preview.seek_to_char_requested.connect(self._on_seek_to_char)
        layout.addWidget(self.preview, stretch=1)

        # 5) 底部打轴操作栏
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        self.lbl_line_info = QLabel("当前行: -")
        self.lbl_line_info.setStyleSheet("font-size: 12px;")
        bottom.addWidget(self.lbl_line_info)

        self.btn_tag = PrimaryPushButton("打轴 (Space)", self)
        self.btn_tag.setIcon(FIF.PIN)
        self.btn_tag.setMinimumHeight(36)
        self.btn_tag.setMinimumWidth(160)
        self.btn_tag.clicked.connect(self._on_tag_now)
        bottom.addWidget(self.btn_tag)

        self.btn_clear_tags = PushButton("清除当前行标签 (Esc)", self)
        self.btn_clear_tags.setIcon(FIF.DELETE)
        self.btn_clear_tags.clicked.connect(self._on_clear_current_line_tags)
        bottom.addWidget(self.btn_clear_tags)

        bottom.addStretch()

        # 快捷键提示
        shortcut_hint = QLabel("A播放 S停止 Z/X跳转 Q/W变速 F2注音")
        shortcut_hint.setStyleSheet("font-size: 11px; color: gray;")
        bottom.addWidget(shortcut_hint)

        layout.addLayout(bottom)

        # 6) 状态栏
        status = QHBoxLayout()
        status.setContentsMargins(5, 2, 5, 2)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("font-size: 11px; color: gray;")
        status.addWidget(self.lbl_status)
        status.addStretch()
        self.lbl_progress = QLabel("行: 0/0 | 进度: 0%")
        self.lbl_progress.setStyleSheet("font-size: 11px; color: gray;")
        status.addWidget(self.lbl_progress)
        layout.addLayout(status)

    def set_timing_service(self, timing_service: TimingService):
        self._timing_service = timing_service
        self._timing_service.set_callbacks(self)

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self.set_project(self._store.project)
        elif change_type in ("rubies", "lyrics", "checkpoints"):
            self.refresh_lyric_display()
        elif change_type == "timetags":
            self._update_time_tags_display()
            self._update_status()
        elif change_type == "settings":
            self._apply_settings()

    def _apply_settings(self):
        """从 AppSettings 读取设定并应用到编辑器。"""
        if not self._store:
            return
        # 通过 MainWindow 的 settingInterface 获取 AppSettings
        main_window = self.window()
        setting_iface = getattr(main_window, "settingInterface", None)
        if setting_iface is None:
            return
        settings = setting_iface.get_settings()
        self._fast_forward_ms = settings.get("timing.fast_forward_ms", 5000)
        self._rewind_ms = settings.get("timing.rewind_ms", 5000)
        # 读取快捷键映射
        self._key_map = {}
        shortcut_actions = {
            "tag_now": settings.get("shortcuts.tag_now", "Space"),
            "play_pause": settings.get("shortcuts.play_pause", "A"),
            "stop": settings.get("shortcuts.stop", "S"),
            "seek_back": settings.get("shortcuts.seek_back", "Z"),
            "seek_forward": settings.get("shortcuts.seek_forward", "X"),
            "speed_down": settings.get("shortcuts.speed_down", "Q"),
            "speed_up": settings.get("shortcuts.speed_up", "W"),
            "edit_ruby": settings.get("shortcuts.edit_ruby", "F2"),
            "toggle_checkpoint": settings.get("shortcuts.toggle_checkpoint", "F4"),
            "volume_up": settings.get("shortcuts.volume_up", "UP"),
            "volume_down": settings.get("shortcuts.volume_down", "DOWN"),
            "nav_prev_line": settings.get("shortcuts.nav_prev_line", "LEFT"),
            "nav_next_line": settings.get("shortcuts.nav_next_line", "RIGHT"),
            "clear_tags": settings.get("shortcuts.clear_tags", "ESCAPE"),
        }
        for action, key_str in shortcut_actions.items():
            self._key_map[key_str.upper()] = action

    # ==================== 项目 ====================

    def set_project(self, project: Project):
        self._project = project
        self.preview.set_project(project)
        self._apply_checkpoint_position(
            self._timing_service.get_current_position()
            if self._timing_service
            else CheckpointPosition()
        )
        self._update_time_tags_display()
        self._update_status()

    def release_resources(self):
        """释放音频资源"""
        if self._timing_service:
            self._timing_service.release()

    # ==================== 工具栏操作 ====================

    def _on_save(self):
        if not self._project:
            InfoBar.warning(
                title="无项目",
                content="请先创建或打开项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "保存项目", "", "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)"
        )
        if not path:
            return
        if not path.endswith(".sug"):
            path += ".sug"

        try:
            from strange_uta_game.backend.infrastructure.persistence.sug_parser import (
                SugProjectParser,
            )

            SugProjectParser.save(self._project, path)
            InfoBar.success(
                title="保存成功",
                content=path,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            self.project_saved.emit()
        except Exception as e:
            InfoBar.error(
                title="保存失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    def _on_load_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            "",
            "音频文件 (*.mp3 *.wav *.flac *.aac *.ogg *.m4a);;所有文件 (*.*)",
        )
        if path:
            self.load_audio(path)

    def _on_undo(self):
        if self._timing_service and self._timing_service.can_undo():
            self._timing_service.undo()
            self._update_time_tags_display()
            self._apply_checkpoint_position(self._timing_service.get_current_position())
            self._update_status()

    def _on_redo(self):
        if self._timing_service and self._timing_service.can_redo():
            self._timing_service.redo()
            self._update_time_tags_display()
            self._apply_checkpoint_position(self._timing_service.get_current_position())
            self._update_status()

    def _on_bulk_change(self):
        """Ctrl+H — 打开批量変更对话框"""
        from strange_uta_game.frontend.editor.bulk_change_dialog import BulkChangeDialog

        dialog = BulkChangeDialog(self._project, self)
        dialog.exec()

    # ==================== 音频 ====================

    def load_audio(self, file_path: str) -> bool:
        if not self._timing_service:
            return False

        try:
            self._timing_service.load_audio(file_path)
            info = self._timing_service.get_audio_info()
            if info:
                self.transport.set_duration(info.duration_ms)
                self.timeline.set_duration(info.duration_ms)
                self.transport.set_position(0)
                self.timeline.set_position(0)

            self._audio_file_path = file_path
            self.toolbar.lbl_audio.setText(Path(file_path).name)

            InfoBar.success(
                title="音频已加载",
                content=Path(file_path).name,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return True
        except AudioLoadError as e:
            InfoBar.error(
                title="加载失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return False
        except Exception as e:
            InfoBar.error(
                title="加载失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return False

    # ==================== 播放控制 ====================

    def _on_play(self):
        if self._timing_service:
            try:
                self._timing_service.play()
                self.transport.set_playing(self._timing_service.is_playing())
                self.lbl_status.setText("播放中")
            except Exception as e:
                self._show_runtime_error(str(e))

    def _on_pause(self):
        if self._timing_service:
            self._timing_service.pause()
            self.transport.set_playing(False)
            self.lbl_status.setText("已暂停")

    def _on_stop(self):
        if self._timing_service:
            self._timing_service.stop()
            self.transport.set_playing(False)
            self.transport.set_position(0)
            self.timeline.set_position(0)
            self.lbl_status.setText("已停止")

    def _on_seek(self, ms: int):
        if self._timing_service:
            self._timing_service.seek(ms)
            self.transport.set_position(ms)
            self.timeline.set_position(ms)

    def _on_speed_changed(self, speed: float):
        if self._timing_service:
            self._timing_service.set_speed(speed)

    def _on_volume_changed(self, vol: int):
        if self._timing_service:
            self._timing_service.set_volume(vol)

    # ==================== 打轴 ====================

    def _on_tag_now(self):
        if not self._timing_service:
            return

        try:
            self._timing_service.on_timing_key_pressed("SPACE")
            self._timing_service.on_timing_key_released("SPACE")
        except Exception as e:
            self._show_runtime_error(str(e))

    def _on_clear_current_line_tags(self):
        if not self._timing_service:
            return

        self._timing_service.clear_timetags_for_current_line()
        self._update_time_tags_display()
        self._update_status()

    def _on_line_clicked(self, idx: int):
        self._current_line_idx = idx
        self._update_line_info()

    def _on_checkpoint_clicked(self, line_idx: int, char_idx: int, cp_idx: int):
        """点击 checkpoint 标记跳转到对应打轴位置"""
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, cp_idx)
            self._update_time_tags_display()
            self._update_status()

    def _on_char_edit_requested(self, line_idx: int, char_idx: int):
        """F2 键弹出注音编辑对话框"""
        if not self._project or line_idx >= len(self._project.lines):
            return
        line = self._project.lines[line_idx]
        if char_idx >= len(line.chars):
            return
        dialog = CharEditDialog(line, char_idx, self)
        dialog.exec()
        if dialog.was_modified():
            self.preview._update_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store"):
                self._store.notify("lyrics")

    def _toggle_checkpoint(self, delta: int = 1):
        """F4 增加轴点数 (+1)，Alt+F4 减少轴点数 (-1)，最小为 1。"""
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx >= len(self._project.lines):
            return
        line = self._project.lines[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx >= len(line.chars) or char_idx >= len(line.checkpoints):
            return

        old_cp = line.checkpoints[char_idx]
        new_count = max(1, old_cp.check_count + delta)
        line.checkpoints[char_idx] = CheckpointConfig(
            char_idx=old_cp.char_idx,
            check_count=new_count,
            is_line_end=old_cp.is_line_end,
            is_rest=old_cp.is_rest,
        )
        # 重建 timing_service 的全局 checkpoint 列表
        if self._timing_service:
            self._timing_service._rebuild_global_checkpoints()
        self.refresh_lyric_display()
        self._update_status()
        if hasattr(self, "_store") and self._store:
            self._store.notify("checkpoints")

    def _on_nav_line(self, delta: int):
        """方向键导航：上一行 (delta=-1) 或下一行 (delta=+1)"""
        if not self._project:
            return
        new_idx = self._current_line_idx + delta
        if new_idx < 0 or new_idx >= len(self._project.lines):
            return
        if self._timing_service:
            self._timing_service.move_to_checkpoint(new_idx, 0, 0)
            self._update_time_tags_display()
            self._update_status()

    def _on_seek_to_char(self, line_idx: int, char_idx: int):
        """双击字符 → 跳转到该字符的 checkpoint 前 3 秒"""
        if not self._project or line_idx >= len(self._project.lines):
            return
        line = self._project.lines[line_idx]
        if char_idx >= len(line.chars):
            return

        tags = line.get_timetags_for_char(char_idx)
        if tags:
            target_ms = max(0, tags[0].timestamp_ms - 3000)
            self._on_seek(target_ms)

        # 同时移动打轴位置到该字符
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)
            self._update_time_tags_display()
            self._update_status()

    # ==================== 键盘 ====================

    def keyPressEvent(self, a0: Optional[QKeyEvent]):
        if a0 is None:
            return
        key = a0.key()
        modifiers = a0.modifiers()

        # Ctrl 快捷键（优先处理，不走动作映射）
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                self._on_undo()
                a0.accept()
                return
            elif key == Qt.Key.Key_Y:
                self._on_redo()
                a0.accept()
                return
            elif key == Qt.Key.Key_S:
                self._on_save()
                a0.accept()
                return
            elif key == Qt.Key.Key_H:
                self._on_bulk_change()
                a0.accept()
                return
            else:
                super().keyPressEvent(a0)
                return

        # Alt+F4 减少轴点
        if modifiers & Qt.KeyboardModifier.AltModifier and key == Qt.Key.Key_F4:
            if self._project:
                self._toggle_checkpoint(delta=-1)
            a0.accept()
            return

        # Convert Qt key to string name for mapping lookup
        key_name = self._qt_key_to_name(key)
        if not key_name:
            super().keyPressEvent(a0)
            return

        action = self._key_map.get(key_name.upper())
        # Fallback to default key map if settings not loaded yet
        if action is None:
            action = self._default_key_action(key)

        if action == "tag_now":
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and not self._space_pressed:
                try:
                    self._space_pressed = True
                    self._timing_service.on_timing_key_pressed("SPACE")
                except Exception as e:
                    self._space_pressed = False
                    self._show_runtime_error(str(e))
            a0.accept()
            return
        elif action == "play_pause":
            if self._timing_service and self._timing_service.is_playing():
                self._on_pause()
            else:
                self._on_play()
        elif action == "stop":
            self._on_stop()
        elif action == "seek_back":
            if self._timing_service:
                cur = self._timing_service.get_position_ms()
                self._on_seek(max(0, cur - self._rewind_ms))
        elif action == "seek_forward":
            if self._timing_service:
                cur = self._timing_service.get_position_ms()
                dur = self._timing_service.get_duration_ms()
                self._on_seek(min(dur, cur + self._fast_forward_ms))
        elif action == "speed_down":
            v = self.transport.spin_speed.value()
            self.transport.spin_speed.setValue(max(0.5, v - 0.1))
        elif action == "speed_up":
            v = self.transport.spin_speed.value()
            self.transport.spin_speed.setValue(min(2.0, v + 0.1))
        elif action == "volume_up":
            v = self.transport.slider_volume.value()
            self.transport.slider_volume.setValue(min(100, v + 5))
        elif action == "volume_down":
            v = self.transport.slider_volume.value()
            self.transport.slider_volume.setValue(max(0, v - 5))
        elif action == "nav_prev_line":
            self._on_nav_line(-1)
            a0.accept()
            return
        elif action == "nav_next_line":
            self._on_nav_line(1)
            a0.accept()
            return
        elif action == "clear_tags":
            self._on_clear_current_line_tags()
        elif action == "edit_ruby":
            if self._project:
                line_idx = self._current_line_idx
                char_idx = self.preview._current_char_idx
                self._on_char_edit_requested(line_idx, char_idx)
        elif action == "toggle_checkpoint":
            if self._project:
                self._toggle_checkpoint(delta=1)
        else:
            super().keyPressEvent(a0)

    def keyReleaseEvent(self, a0: Optional[QKeyEvent]):
        if a0 is None:
            return
        key = a0.key()
        key_name = self._qt_key_to_name(key)
        action = self._key_map.get(key_name.upper()) if key_name else None
        if action is None:
            action = self._default_key_action(key)
        if action == "tag_now":
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and self._space_pressed:
                try:
                    self._timing_service.on_timing_key_released("SPACE")
                except Exception as e:
                    self._show_runtime_error(str(e))
            self._space_pressed = False
            a0.accept()
            return
        super().keyReleaseEvent(a0)

    def _qt_key_to_name(self, key) -> Optional[str]:
        """Convert Qt key enum to string name for shortcut mapping."""
        _key_names = {
            Qt.Key.Key_Space: "SPACE",
            Qt.Key.Key_Escape: "ESCAPE",
            Qt.Key.Key_F1: "F1",
            Qt.Key.Key_F2: "F2",
            Qt.Key.Key_F3: "F3",
            Qt.Key.Key_F4: "F4",
            Qt.Key.Key_F5: "F5",
            Qt.Key.Key_F6: "F6",
            Qt.Key.Key_F7: "F7",
            Qt.Key.Key_F8: "F8",
            Qt.Key.Key_F9: "F9",
            Qt.Key.Key_F10: "F10",
            Qt.Key.Key_F11: "F11",
            Qt.Key.Key_F12: "F12",
            Qt.Key.Key_Up: "UP",
            Qt.Key.Key_Down: "DOWN",
            Qt.Key.Key_Left: "LEFT",
            Qt.Key.Key_Right: "RIGHT",
        }
        if key in _key_names:
            return _key_names[key]
        # For letter keys A-Z
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key)
        # For digit keys 0-9
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(key)
        return None

    def _default_key_action(self, key) -> Optional[str]:
        """Fallback key mapping when settings not loaded."""
        defaults = {
            Qt.Key.Key_Space: "tag_now",
            Qt.Key.Key_A: "play_pause",
            Qt.Key.Key_S: "stop",
            Qt.Key.Key_Z: "seek_back",
            Qt.Key.Key_X: "seek_forward",
            Qt.Key.Key_Q: "speed_down",
            Qt.Key.Key_W: "speed_up",
            Qt.Key.Key_Escape: "clear_tags",
            Qt.Key.Key_F2: "edit_ruby",
            Qt.Key.Key_F4: "toggle_checkpoint",
            Qt.Key.Key_Up: "volume_up",
            Qt.Key.Key_Down: "volume_down",
            Qt.Key.Key_Left: "nav_prev_line",
            Qt.Key.Key_Right: "nav_next_line",
        }
        return defaults.get(key)

    # ==================== TimingService 回调 ====================

    def on_timetag_added(
        self,
        singer_id: str,
        line_idx: int,
        char_idx: int,
        checkpoint_idx: int,
        timestamp_ms: int,
    ) -> None:
        _ = singer_id, line_idx, char_idx, checkpoint_idx, timestamp_ms
        self._timetag_added_signal.emit()

    def on_position_changed(
        self, position_ms: int, duration_ms: int, singer_positions
    ) -> None:
        self._position_changed_signal.emit(position_ms, duration_ms, singer_positions)

    def on_singer_changed(self, new_singer_id: str, prev_singer_id: str) -> None:
        _ = new_singer_id, prev_singer_id

    def on_checkpoint_moved(self, position: CheckpointPosition) -> None:
        self._checkpoint_moved_signal.emit(position)

    def on_line_end_recording_started(
        self, line_idx: int, char_idx: int, start_time_ms: int
    ) -> None:
        self._line_end_started_signal.emit(line_idx, char_idx, start_time_ms)

    def on_line_end_recording_finished(
        self, line_idx: int, char_idx: int, start_time_ms: int, end_time_ms: int
    ) -> None:
        self._line_end_finished_signal.emit(
            line_idx, char_idx, start_time_ms, end_time_ms
        )

    def on_timing_error(self, error_type: str, message: str) -> None:
        self._timing_error_signal.emit(error_type, message)

    def _handle_position_changed(
        self, position_ms: int, duration_ms: int, singer_positions
    ):
        # 60fps UI 节流：跳过间隔 < 16ms 的更新
        now = time.monotonic()
        if now - self._last_position_update_time < 0.016:
            return
        self._last_position_update_time = now

        _ = singer_positions
        self.transport.set_duration(duration_ms)
        self.timeline.set_duration(duration_ms)
        self.transport.set_position(position_ms)
        self.timeline.set_position(position_ms)
        self.preview.set_current_time_ms(position_ms)
        if self._timing_service:
            self.transport.set_playing(self._timing_service.is_playing())

    def _handle_checkpoint_moved(self, position: CheckpointPosition):
        self._apply_checkpoint_position(position)
        self._update_status()

    def _handle_timetag_added(self):
        self._update_time_tags_display()
        self._update_status()

    def _handle_timing_error(self, error_type: str, message: str):
        InfoBar.warning(
            title=error_type,
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _handle_line_end_recording_started(
        self, line_idx: int, char_idx: int, start_time_ms: int
    ):
        _ = line_idx, char_idx, start_time_ms
        self.lbl_status.setText("句尾长按录制中")

    def _handle_line_end_recording_finished(
        self, line_idx: int, char_idx: int, start_time_ms: int, end_time_ms: int
    ):
        _ = line_idx, char_idx, start_time_ms, end_time_ms
        self.lbl_status.setText("句尾长按录制完成")

    # ==================== 辅助 ====================

    def _apply_checkpoint_position(self, position: CheckpointPosition):
        if not self._project or not self._project.lines:
            self._current_line_idx = 0
            self._update_line_info()
            return

        line_idx = max(0, min(position.line_idx, len(self._project.lines) - 1))
        self._current_line_idx = line_idx
        self.preview.set_current_position(line_idx, position.char_idx)
        self._update_line_info()

    def _show_runtime_error(self, message: str):
        InfoBar.error(
            title="操作失败",
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _update_line_info(self):
        if self._project and self._project.lines:
            total = len(self._project.lines)
            idx = min(self._current_line_idx, total - 1)
            text = self._project.lines[idx].text
            preview = text[:30] + "..." if len(text) > 30 else text
            self.lbl_line_info.setText(f"行 {idx + 1}/{total}: {preview}")
        else:
            self.lbl_line_info.setText("当前行: -")

    def _update_time_tags_display(self):
        if not self._project:
            return
        tags_ms = []
        for line in self._project.lines:
            for tag in line.timetags:
                tags_ms.append(tag.timestamp_ms)
        self.timeline.set_time_tags(tags_ms)

    def _update_status(self):
        if not self._project:
            self.lbl_progress.setText("行: 0/0 | 进度: 0%")
            return
        total = len(self._project.lines)
        timed = sum(1 for l in self._project.lines if l.timetags)
        pct = int(timed / total * 100) if total > 0 else 0
        self.lbl_progress.setText(f"行: {total} | 已打轴: {timed}/{total} ({pct}%)")

    def refresh_lyric_display(self):
        self.preview._update_display()
