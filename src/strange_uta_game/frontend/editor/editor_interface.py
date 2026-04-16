"""编辑器界面。

编辑器界面包含：
- 卡拉OK预览区（多行歌词 + ワイプ效果）
- 播放控制栏（播放/暂停/停止/进度/速度）
- 时间轴（波形 + 时间标签）
- 快捷操作区（打轴按钮）
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QSplitter,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QDoubleSpinBox,
    QFileDialog,
    QMessageBox,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    TogglePushButton,
    Slider,
    SpinBox,
    DoubleSpinBox,
    LineEdit,
    CardWidget,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    SplitPushButton,
    RoundMenu,
    Action,
    FluentWindow,
)

from typing import Optional, List, Callable
from pathlib import Path

from strange_uta_game.backend.domain import Project, LyricLine, TimeTag
from strange_uta_game.backend.application import (
    ProjectService,
    CommandManager,
    AddTimeTagCommand,
    RemoveTimeTagCommand,
)
from strange_uta_game.backend.infrastructure.audio import SoundDeviceEngine


class TransportBar(CardWidget):
    """播放控制栏

    包含：
    - 播放/暂停/停止按钮
    - 进度条
    - 速度控制
    - 音量控制
    - 时间显示
    """

    play_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    seek_requested = pyqtSignal(int)  # 毫秒
    speed_changed = pyqtSignal(float)
    volume_changed = pyqtSignal(int)  # 0-100

    def __init__(self, parent=None):
        super().__init__(parent)

        self._duration_ms = 0
        self._current_ms = 0
        self._is_playing = False

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(10)

        # 播放控制按钮
        self.btn_stop = PushButton("", self, icon=FIF.CANCEL)
        self.btn_stop.setFixedSize(36, 36)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)

        self.btn_play = PrimaryPushButton("", self, icon=FIF.PLAY)
        self.btn_play.setFixedSize(44, 44)
        self.btn_play.clicked.connect(self._on_play_clicked)

        layout.addWidget(self.btn_stop)
        layout.addWidget(self.btn_play)

        # 时间显示
        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setMinimumWidth(100)
        layout.addWidget(self.lbl_time)

        # 进度条
        self.slider_progress = Slider(Qt.Orientation.Horizontal, self)
        self.slider_progress.setMinimum(0)
        self.slider_progress.setMaximum(1000)
        self.slider_progress.setValue(0)
        self.slider_progress.sliderReleased.connect(self._on_seek)
        layout.addWidget(self.slider_progress, stretch=1)

        # 速度控制
        layout.addWidget(QLabel("速度:"))
        self.spin_speed = DoubleSpinBox(self)
        self.spin_speed.setRange(0.5, 2.0)
        self.spin_speed.setSingleStep(0.1)
        self.spin_speed.setValue(1.0)
        self.spin_speed.setDecimals(1)
        self.spin_speed.setSuffix("x")
        self.spin_speed.valueChanged.connect(self.speed_changed.emit)
        self.spin_speed.setFixedWidth(70)
        layout.addWidget(self.spin_speed)

        # 音量控制
        layout.addWidget(QLabel("音量:"))
        self.slider_volume = Slider(Qt.Orientation.Horizontal, self)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(100)
        self.slider_volume.setFixedWidth(80)
        self.slider_volume.valueChanged.connect(self.volume_changed.emit)
        layout.addWidget(self.slider_volume)

    def _on_play_clicked(self):
        """播放/暂停切换"""
        if self._is_playing:
            self.pause_clicked.emit()
        else:
            self.play_clicked.emit()

    def _on_seek(self):
        """进度条拖动"""
        if self._duration_ms > 0:
            ratio = self.slider_progress.value() / 1000
            position_ms = int(ratio * self._duration_ms)
            self.seek_requested.emit(position_ms)

    def set_duration(self, duration_ms: int):
        """设置音频时长"""
        self._duration_ms = duration_ms
        self._update_time_label()

    def set_position(self, position_ms: int):
        """设置当前播放位置"""
        self._current_ms = position_ms
        if self._duration_ms > 0:
            ratio = position_ms / self._duration_ms
            self.slider_progress.setValue(int(ratio * 1000))
        self._update_time_label()

    def set_playing(self, is_playing: bool):
        """设置播放状态"""
        self._is_playing = is_playing
        if is_playing:
            self.btn_play.setIcon(FIF.PAUSE)
        else:
            self.btn_play.setIcon(FIF.PLAY)

    def _update_time_label(self):
        """更新时间标签"""
        current_sec = self._current_ms // 1000
        duration_sec = self._duration_ms // 1000

        current_min = current_sec // 60
        current_sec_rem = current_sec % 60

        duration_min = duration_sec // 60
        duration_sec_rem = duration_sec % 60

        self.lbl_time.setText(
            f"{current_min:02d}:{current_sec_rem:02d} / "
            f"{duration_min:02d}:{duration_sec_rem:02d}"
        )


class KaraokePreview(QWidget):
    """卡拉OK预览组件

    显示多行歌词，带有ワイプ（逐字高亮）效果。
    """

    line_clicked = pyqtSignal(int)  # 行索引

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None
        self._current_line_idx = 0
        self._current_char_idx = 0
        self._visible_lines = 5  # 可见行数

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 标题
        self.lbl_title = QLabel("歌词预览")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        self.lbl_title.setFont(font)
        layout.addWidget(self.lbl_title)

        # 歌词行容器
        self.lines_container = QWidget()
        self.lines_layout = QVBoxLayout(self.lines_container)
        self.lines_layout.setSpacing(10)
        self.lines_layout.addStretch()

        layout.addWidget(self.lines_container)

        # 初始化空行
        self._line_labels: List[QLabel] = []
        self._create_line_labels()

    def _create_line_labels(self):
        """创建歌词行标签"""
        # 清除旧标签
        for label in self._line_labels:
            label.deleteLater()
        self._line_labels.clear()

        # 创建新标签
        for i in range(self._visible_lines):
            label = QLabel("")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            font = QFont()
            font.setPointSize(24)
            label.setFont(font)
            label.setStyleSheet("color: #666666;")
            self._line_labels.append(label)
            self.lines_layout.insertWidget(i, label)

        # 安装事件过滤器处理点击
        self._click_line_idx = -1
        for i, label in enumerate(self._line_labels):
            label.installEventFilter(self)
            label.setProperty("line_idx", i)

    def set_project(self, project: Project):
        """设置项目"""
        self._project = project
        if project.metadata:
            self.lbl_title.setText(project.metadata.title or "歌词预览")
        self._update_display()

    def set_current_position(self, line_idx: int, char_idx: int = 0):
        """设置当前播放位置"""
        self._current_line_idx = line_idx
        self._current_char_idx = char_idx
        self._update_display()

    def _update_display(self):
        """更新显示"""
        if not self._project or not self._project.lines:
            for label in self._line_labels:
                label.setText("")
            return

        # 计算可见范围
        total_lines = len(self._project.lines)
        half_visible = self._visible_lines // 2

        start_idx = max(0, self._current_line_idx - half_visible)
        end_idx = min(total_lines, start_idx + self._visible_lines)

        # 调整起始位置以确保填满可见行
        if end_idx - start_idx < self._visible_lines:
            start_idx = max(0, end_idx - self._visible_lines)

        # 更新每行显示
        for i, label in enumerate(self._line_labels):
            line_idx = start_idx + i

            if line_idx < total_lines:
                line = self._project.lines[line_idx]

                if line_idx == self._current_line_idx:
                    # 当前行 - 显示ワイプ效果
                    label.setText(self._format_with_wipe(line, self._current_char_idx))
                    label.setStyleSheet("color: #000000; font-weight: bold;")
                elif line_idx < self._current_line_idx:
                    # 已演唱的行
                    label.setText(line.text)
                    label.setStyleSheet("color: #999999;")
                else:
                    # 未演唱的行
                    label.setText(line.text)
                    label.setStyleSheet("color: #666666;")
            else:
                label.setText("")

    def _format_with_wipe(self, line: LyricLine, char_idx: int) -> str:
        """格式化带有ワイプ效果的文本

        使用 HTML 实现逐字高亮。
        """
        if not line.chars:
            return line.text

        # 构建带高亮的文本
        result = []
        for i, char in enumerate(line.chars):
            if i < char_idx:
                # 已演唱的字符（高亮）
                result.append(
                    f'<span style="color: #FF6B6B; font-weight: bold;">{char}</span>'
                )
            elif i == char_idx:
                # 当前字符（特殊高亮）
                result.append(
                    f'<span style="color: #FF6B6B; font-weight: bold; text-decoration: underline;">{char}</span>'
                )
            else:
                # 未演唱的字符
                result.append(f'<span style="color: #666666;">{char}</span>')

        return "".join(result)

    def eventFilter(self, a0, a1):
        """事件过滤器处理标签点击"""
        from PyQt6.QtCore import QEvent

        if a1.type() == QEvent.Type.MouseButtonPress:
            line_idx = a0.property("line_idx")
            if line_idx is not None:
                self._on_line_click(line_idx)
                return True
        return super().eventFilter(a0, a1)

    def _on_line_click(self, idx: int):
        """行点击事件"""
        # 计算实际行索引
        total_lines = len(self._project.lines) if self._project else 0
        half_visible = self._visible_lines // 2
        start_idx = max(0, self._current_line_idx - half_visible)
        actual_idx = start_idx + idx

        if actual_idx < total_lines:
            self.line_clicked.emit(actual_idx)


class TimelineWidget(QWidget):
    """时间轴组件

    显示：
    - 音频波形（简化显示）
    - 时间标签标记
    - 当前播放位置指示器
    """

    seek_requested = pyqtSignal(int)  # 毫秒

    def __init__(self, parent=None):
        super().__init__(parent)

        self._duration_ms = 0
        self._current_ms = 0
        self._time_tags: List[int] = []  # 毫秒位置列表

        self.setMinimumHeight(100)
        self.setMaximumHeight(150)

    def set_duration(self, duration_ms: int):
        """设置音频时长"""
        self._duration_ms = duration_ms
        self.update()

    def set_position(self, position_ms: int):
        """设置当前位置"""
        self._current_ms = position_ms
        self.update()

    def set_time_tags(self, tags_ms: List[int]):
        """设置时间标签"""
        self._time_tags = sorted(tags_ms)
        self.update()

    def paintEvent(self, a0):
        """绘制时间轴"""
        from PyQt6.QtGui import QPainter, QPen, QBrush, QColor

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()

        # 背景
        painter.fillRect(self.rect(), QColor("#F5F5F5"))

        if self._duration_ms <= 0:
            # 显示提示文字
            painter.setPen(QColor("#999999"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请加载音频文件"
            )
            return

        # 绘制时间网格
        pen = QPen(QColor("#DDDDDD"))
        pen.setWidth(1)
        painter.setPen(pen)

        # 每 10 秒一个主网格
        for t in range(0, self._duration_ms + 1, 10000):
            x = int((t / self._duration_ms) * width)
            painter.drawLine(x, 0, x, height)

        # 绘制时间标签标记
        painter.setPen(QPen(QColor("#FF6B6B"), 2))
        for tag_ms in self._time_tags:
            x = int((tag_ms / self._duration_ms) * width)
            y1 = int(height * 0.3)
            y2 = int(height * 0.7)
            painter.drawLine(x, y1, x, y2)

        # 绘制当前位置指示器
        if 0 <= self._current_ms <= self._duration_ms:
            x = int((self._current_ms / self._duration_ms) * width)
            painter.setPen(QPen(QColor("#4ECDC4"), 2))
            painter.drawLine(x, 0, x, height)

            # 绘制位置标记点
            painter.setBrush(QBrush(QColor("#4ECDC4")))
            painter.drawEllipse(x - 5, height // 2 - 5, 10, 10)

    def mousePressEvent(self, a0):
        """鼠标点击跳转到指定位置"""
        if self._duration_ms <= 0:
            return

        x = a0.position().x()
        width = self.width()

        ratio = max(0.0, min(1.0, x / width))
        position_ms = int(ratio * self._duration_ms)

        self.seek_requested.emit(position_ms)


class TimingControls(QWidget):
    """打轴控制面板

    包含打轴功能按钮：
    - Space: 在当前位置打轴
    - 功能键 F1-F9: 快速打轴
    """

    time_tag_added = pyqtSignal(int, int)  # line_idx, timestamp_ms

    def __init__(self, parent=None):
        super().__init__(parent)

        self._current_line_idx = 0
        self._current_timestamp_ms = 0

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # 标题
        title = QLabel("打轴控制")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        # 当前行信息
        self.lbl_current = QLabel("当前行: -")
        layout.addWidget(self.lbl_current)

        # 主按钮：在当前位置打轴
        self.btn_tag_now = PrimaryPushButton("在当前位置打轴 (Space)", self)
        self.btn_tag_now.setIcon(FIF.PIN)
        self.btn_tag_now.setMinimumHeight(50)
        self.btn_tag_now.clicked.connect(self._on_tag_now)
        layout.addWidget(self.btn_tag_now)

        # 功能键区域
        layout.addWidget(QLabel("快速打轴键:"))

        func_layout = QGridLayout()
        self._func_buttons = []

        for i in range(9):
            btn = PushButton(f"F{i + 1}", self)
            btn.setFixedSize(50, 40)
            btn.clicked.connect(lambda checked, idx=i: self._on_func_key(idx))
            self._func_buttons.append(btn)
            func_layout.addWidget(btn, i // 3, i % 3)

        layout.addLayout(func_layout)

        # 其他功能按钮
        layout.addSpacing(20)

        self.btn_clear = PushButton("清除当前行时间标签", self, icon=FIF.DELETE)
        self.btn_clear.clicked.connect(self._on_clear_line)
        layout.addWidget(self.btn_clear)

        layout.addStretch()

    def set_current_line(self, line_idx: int):
        """设置当前行"""
        self._current_line_idx = line_idx
        self.lbl_current.setText(f"当前行: {line_idx + 1}")

    def set_timestamp(self, timestamp_ms: int):
        """设置当前时间戳"""
        self._current_timestamp_ms = timestamp_ms

    def _on_tag_now(self):
        """在当前位置打轴"""
        self.time_tag_added.emit(self._current_line_idx, self._current_timestamp_ms)

    def _on_func_key(self, func_idx: int):
        """功能键按下"""
        # 功能键可以实现特殊功能，比如跳转到特定检查点
        pass

    def _on_clear_line(self):
        """清除当前行的时间标签"""
        # 清除逻辑由外部处理
        pass


class EditorInterface(QWidget):
    """编辑器界面主容器

    整合所有编辑器组件，管理打轴流程。
    """

    project_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None
        self._audio_engine: Optional[SoundDeviceEngine] = None
        self._command_manager = CommandManager()

        self._init_ui()
        self._init_timer()

    def _init_ui(self):
        """初始化界面"""
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 分割器：上半部分（预览 + 时间轴）| 下半部分（控制）
        main_splitter = QSplitter(Qt.Orientation.Vertical)

        # 上半部分容器
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(10, 10, 10, 10)
        top_layout.setSpacing(10)

        # 播放控制栏
        self.transport_bar = TransportBar(self)
        self.transport_bar.play_clicked.connect(self._on_play)
        self.transport_bar.pause_clicked.connect(self._on_pause)
        self.transport_bar.stop_clicked.connect(self._on_stop)
        self.transport_bar.seek_requested.connect(self._on_seek)
        self.transport_bar.speed_changed.connect(self._on_speed_changed)
        self.transport_bar.volume_changed.connect(self._on_volume_changed)
        top_layout.addWidget(self.transport_bar)

        # 水平分割器：预览 | 时间轴 + 打轴控制
        content_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：卡拉OK预览
        self.karaoke_preview = KaraokePreview(self)
        self.karaoke_preview.line_clicked.connect(self._on_line_clicked)
        content_splitter.addWidget(self.karaoke_preview)

        # 右侧容器
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 时间轴
        self.timeline = TimelineWidget(self)
        self.timeline.seek_requested.connect(self._on_seek)
        right_layout.addWidget(self.timeline, stretch=2)

        # 打轴控制面板
        self.timing_controls = TimingControls(self)
        self.timing_controls.time_tag_added.connect(self._on_time_tag_added)
        right_layout.addWidget(self.timing_controls, stretch=1)

        content_splitter.addWidget(right_widget)
        content_splitter.setSizes([600, 400])

        top_layout.addWidget(content_splitter)

        main_splitter.addWidget(top_widget)
        main_splitter.setSizes([500, 0])  # 上半部分占大部分

        layout.addWidget(main_splitter)

    def _init_timer(self):
        """初始化定时器（用于 60fps 位置更新）"""
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._on_timer_update)
        self._update_timer.setInterval(16)  # ~60fps

    def set_project(self, project: Project):
        """设置项目"""
        self._project = project
        self.karaoke_preview.set_project(project)
        self.timing_controls.set_current_line(0)
        self._update_time_tags_display()

        # 设置标题
        if project.metadata and project.metadata.title:
            self.window().setWindowTitle(f"StrangeUtaGame - {project.metadata.title}")

    def load_audio(self, file_path: str) -> bool:
        """加载音频文件"""
        try:
            # 释放旧的音频引擎
            if self._audio_engine:
                self._audio_engine.release()

            # 创建新的音频引擎
            self._audio_engine = SoundDeviceEngine()

            # 加载音频
            if not self._audio_engine.load(file_path):
                InfoBar.error(
                    title="加载失败",
                    content="无法加载音频文件",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )
                return False

            # 获取音频信息
            info = self._audio_engine.get_audio_info()
            if info:
                self.transport_bar.set_duration(info.duration_ms)
                self.timeline.set_duration(info.duration_ms)

            # 设置回调
            self._audio_engine.set_position_callback(self._on_audio_position_changed)

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

    def _update_time_tags_display(self):
        """更新时间标签显示"""
        if not self._project:
            return

        # 收集所有时间标签位置
        tags_ms = []
        for line in self._project.lines:
            for tag in line.timetags:
                tags_ms.append(tag.timestamp_ms)

        self.timeline.set_time_tags(tags_ms)

    # ==================== 播放控制 ====================

    def _on_play(self):
        """播放"""
        if self._audio_engine:
            if self._audio_engine.play():
                self.transport_bar.set_playing(True)
                self._update_timer.start()

    def _on_pause(self):
        """暂停"""
        if self._audio_engine:
            self._audio_engine.pause()
            self.transport_bar.set_playing(False)
            self._update_timer.stop()

    def _on_stop(self):
        """停止"""
        if self._audio_engine:
            self._audio_engine.stop()
            self.transport_bar.set_playing(False)
            self._update_timer.stop()
            self._update_position_display(0)

    def _on_seek(self, position_ms: int):
        """跳转位置"""
        if self._audio_engine:
            self._audio_engine.set_position_ms(position_ms)
            self._update_position_display(position_ms)

    def _on_speed_changed(self, speed: float):
        """速度改变"""
        if self._audio_engine:
            self._audio_engine.set_speed(speed)

    def _on_volume_changed(self, volume: int):
        """音量改变"""
        if self._audio_engine:
            self._audio_engine.set_volume(volume)

    def _on_audio_position_changed(self, position_ms: int):
        """音频位置回调（由音频引擎调用）"""
        # 这个回调会在音频线程中被调用，需要小心处理
        # 这里我们只设置一个标志，在主线程中处理
        self._pending_position_ms = position_ms

    def _on_timer_update(self):
        """定时器更新（60fps）"""
        if hasattr(self, "_pending_position_ms"):
            self._update_position_display(self._pending_position_ms)
            delattr(self, "_pending_position_ms")

    def _update_position_display(self, position_ms: int):
        """更新位置显示"""
        self.transport_bar.set_position(position_ms)
        self.timeline.set_position(position_ms)
        self.timing_controls.set_timestamp(position_ms)

        # 更新当前歌词位置
        if self._project:
            line_idx, char_idx = self._find_current_lyric_position(position_ms)
            self.karaoke_preview.set_current_position(line_idx, char_idx)
            self.timing_controls.set_current_line(line_idx)

    def _find_current_lyric_position(self, position_ms: int) -> tuple:
        """根据时间查找当前歌词位置

        Returns:
            (line_idx, char_idx)
        """
        if not self._project:
            return (0, 0)

        # 查找当前行
        for i, line in enumerate(self._project.lines):
            if not line.timetags:
                continue

            # 获取该行的第一个和最后一个时间标签
            first_tag = min(line.timetags, key=lambda t: t.timestamp_ms)
            last_tag = max(line.timetags, key=lambda t: t.timestamp_ms)

            if first_tag.timestamp_ms <= position_ms <= last_tag.timestamp_ms:
                # 在当前行内，查找字符位置
                char_idx = self._find_char_position_in_line(line, position_ms)
                return (i, char_idx)

            if position_ms < first_tag.timestamp_ms:
                # 还没到这一行
                return (max(0, i - 1), 0)

        # 在所有时间标签之后，返回最后一行
        return (len(self._project.lines) - 1, 0)

    def _find_char_position_in_line(self, line: LyricLine, position_ms: int) -> int:
        """在行内查找字符位置"""
        if not line.timetags:
            return 0

        # 按时间排序的时间标签
        sorted_tags = sorted(line.timetags, key=lambda t: t.timestamp_ms)

        for i, tag in enumerate(sorted_tags):
            if position_ms < tag.timestamp_ms:
                return max(0, i - 1)

        return len(sorted_tags) - 1

    # ==================== 打轴功能 ====================

    def _on_line_clicked(self, line_idx: int):
        """歌词行被点击"""
        self.timing_controls.set_current_line(line_idx)

    def _on_time_tag_added(self, line_idx: int, timestamp_ms: int):
        """添加时间标签"""
        if not self._project or line_idx >= len(self._project.lines):
            return

        line = self._project.lines[line_idx]

        # 创建命令并执行
        command = AddTimeTagCommand(
            project=self._project,
            line_id=line.id,
            timestamp_ms=timestamp_ms,
            char_idx=0,  # 简化为整行时间标签
            checkpoint_idx=0,
        )

        self._command_manager.execute(command)

        # 更新时间标签显示
        self._update_time_tags_display()

        InfoBar.success(
            title="时间标签已添加",
            content=f"第 {line_idx + 1} 行: {self._format_time(timestamp_ms)}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _format_time(self, ms: int) -> str:
        """格式化时间为 mm:ss.xx"""
        total_sec = ms // 1000
        minutes = total_sec // 60
        seconds = total_sec % 60
        centis = (ms % 1000) // 10
        return f"{minutes:02d}:{seconds:02d}.{centis:02d}"

    # ==================== 键盘事件 ====================

    def keyPressEvent(self, a0):
        """键盘按下事件"""
        key = a0.key()

        if key == Qt.Key.Key_Space:
            # Space: 在当前位置打轴
            timestamp_ms = 0
            if self._audio_engine:
                timestamp_ms = self._audio_engine.get_position_ms()
            self._on_time_tag_added(
                self.timing_controls._current_line_idx,
                timestamp_ms,
            )
        elif key == Qt.Key.Key_A:
            # A: 播放/暂停
            if self.transport_bar._is_playing:
                self._on_pause()
            else:
                self._on_play()
        elif key == Qt.Key.Key_S:
            # S: 停止
            self._on_stop()
        elif key == Qt.Key.Key_Z:
            # Z: 后退 5 秒
            if self._audio_engine:
                current = self._audio_engine.get_position_ms()
                self._on_seek(max(0, current - 5000))
        elif key == Qt.Key.Key_X:
            # X: 前进 5 秒
            if self._audio_engine:
                current = self._audio_engine.get_position_ms()
                duration = self._audio_engine.get_duration_ms()
                self._on_seek(min(duration, current + 5000))
        elif key == Qt.Key.Key_Q:
            # Q: 减速
            speed = self.transport_bar.spin_speed.value()
            new_speed = max(0.5, speed - 0.1)
            self.transport_bar.spin_speed.setValue(new_speed)
        elif key == Qt.Key.Key_W:
            # W: 加速
            speed = self.transport_bar.spin_speed.value()
            new_speed = min(2.0, speed + 0.1)
            self.transport_bar.spin_speed.setValue(new_speed)
        elif key == Qt.Key.Key_Escape:
            # ESC: 清除当前行时间标签
            self._on_clear_current_line_tags()
        else:
            super().keyPressEvent(a0)

    def _on_clear_current_line_tags(self):
        """清除当前行的时间标签"""
        if not self._project:
            return

        line_idx = self.timing_controls._current_line_idx
        if line_idx >= len(self._project.lines):
            return

        line = self._project.lines[line_idx]

        # 清除所有时间标签
        for tag in list(line.timetags):
            command = RemoveTimeTagCommand(
                project=self._project, line_id=line.id, tag=tag
            )
            self._command_manager.execute(command)

        self._update_time_tags_display()

        InfoBar.info(
            title="时间标签已清除",
            content=f"第 {line_idx + 1} 行",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def undo(self):
        """撤销"""
        if self._command_manager.can_undo():
            self._command_manager.undo()
            self._update_time_tags_display()

    def redo(self):
        """重做"""
        if self._command_manager.can_redo():
            self._command_manager.redo()
            self._update_time_tags_display()

    def save_project(self, file_path: str) -> bool:
        """保存项目"""
        if not self._project:
            return False

        try:
            # 使用 SugProjectParser 保存
            from strange_uta_game.backend.infrastructure.persistence.sug_parser import (
                SugProjectParser,
            )

            SugProjectParser.save(self._project, file_path)

            InfoBar.success(
                title="保存成功",
                content=file_path,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

            self.project_saved.emit()
            return True

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
            return False
