"""播放控制栏。

包含播放/暂停/停止按钮、进度条、速度/音量滑块、进度预渲染显示。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit
from qfluentwidgets import (
    FluentIcon as FIF,
    PrimaryToolButton,
    Slider,
    ToolButton,
)


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
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.btn_stop)

        # 播放/暂停
        self.btn_play = PrimaryToolButton(FIF.PLAY, self)
        self.btn_play.setFixedSize(40, 40)
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

        # 速度（百分比显示，输入框，内部转换为倍率）
        lbl_speed = QLabel("速度")
        lbl_speed.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(lbl_speed)
        self.edit_speed = QLineEdit(self)
        self.edit_speed.setText("100%")
        self.edit_speed.setFixedWidth(60)
        self.edit_speed.setFixedHeight(32)
        self.edit_speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit_speed.setStyleSheet("font-size: 12px;")
        self.edit_speed.editingFinished.connect(self._on_speed_editing_finished)
        layout.addWidget(self.edit_speed)

        # 渲染进度提示：固定宽度避免速度输入框被挤动；默认空字符串隐身。
        self.lbl_render = QLabel("", self)
        self.lbl_render.setFixedWidth(96)
        self.lbl_render.setStyleSheet("font-size: 11px; color: #888;")
        self.lbl_render.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.lbl_render)

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

    def _on_speed_editing_finished(self):
        """速度输入框编辑完成 — 解析并发射信号"""
        text = self.edit_speed.text().strip().replace("%", "")
        try:
            val = int(text)
            val = max(50, min(200, val))
        except ValueError:
            val = 100
        self.edit_speed.setText(f"{val}%")
        self.speed_changed.emit(val / 100.0)

    def set_speed_value(self, pct: int):
        """设置速度值（百分比整数，如 100）"""
        pct = max(50, min(200, pct))
        self.edit_speed.setText(f"{pct}%")
        self.speed_changed.emit(pct / 100.0)

    def get_speed_value(self) -> int:
        """获取当前速度值（百分比整数，如 100）"""
        text = self.edit_speed.text().strip().replace("%", "")
        try:
            return max(50, min(200, int(text)))
        except ValueError:
            return 100

    def set_render_progress(self, speed: float, progress: float) -> None:
        """更新渲染进度指示。``progress>=1.0`` 时清空。

        ``speed`` 已经由引擎量化（保留 2 位小数），直接 ``{:.2f}`` 显示。
        """
        if progress >= 0.999:
            self.lbl_render.setText("")
        else:
            pct = max(0, min(99, int(progress * 100)))
            self.lbl_render.setText(f"{speed:.2f}× 渲染 {pct}%")


# ──────────────────────────────────────────────
# 工具栏
# ──────────────────────────────────────────────
