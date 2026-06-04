"""Playback transport bar."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon as FIF,
    PrimaryToolButton,
    Slider,
    ToolButton,
)


class WheelSpeedSlider(Slider):
    """Speed slider that accepts wheel input while hovered."""

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        if event is None:
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return
        step = self.singleStep() or 5
        value = self.value() + (step if delta > 0 else -step)
        self.setValue(max(self.minimum(), min(self.maximum(), value)))
        event.accept()


class TransportBar(QFrame):
    """Compact playback transport bar."""

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
        self._is_dragging = False
        # 速度滑块拖动中：拖动期间只更新标签，松手（sliderReleased）才应用速度，
        # 避免每个中间值都触发变速（HQ 模式下会刷屏预渲染）。滚轮/点击/键盘
        # 不经拖动，仍即时生效。
        self._speed_dragging = False
        self.setFixedHeight(56)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        self.btn_stop = ToolButton(FIF.CANCEL, self)
        self.btn_stop.setFixedSize(40, 40)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.btn_stop)

        self.btn_play = PrimaryToolButton(FIF.PLAY, self)
        self.btn_play.setFixedSize(40, 40)
        self.btn_play.clicked.connect(self._on_play_clicked)
        layout.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00.00 / 00:00.00")
        self.lbl_time.setStyleSheet("font-family: monospace; font-size: 12px;")
        self.lbl_time.setMinimumWidth(140)
        layout.addWidget(self.lbl_time)

        self.slider_progress = Slider(Qt.Orientation.Horizontal, self)
        self.slider_progress.setRange(0, 10000)
        self.slider_progress.setValue(0)
        self.slider_progress.clicked.connect(self._on_slider_clicked)
        self.slider_progress.sliderPressed.connect(self._on_slider_pressed)
        self.slider_progress.sliderMoved.connect(self._on_slider_moved)
        self.slider_progress.sliderReleased.connect(self._on_seek)
        layout.addWidget(self.slider_progress, stretch=1)

        layout.addWidget(CaptionLabel("速度"))
        self.slider_speed = WheelSpeedSlider(Qt.Orientation.Horizontal, self)
        self.slider_speed.setRange(50, 100)
        self.slider_speed.setSingleStep(5)
        self.slider_speed.setPageStep(5)
        self.slider_speed.setValue(100)
        self.slider_speed.setFixedWidth(116)
        self.slider_speed.valueChanged.connect(self._on_speed_slider_changed)
        self.slider_speed.sliderPressed.connect(self._on_speed_slider_pressed)
        self.slider_speed.sliderReleased.connect(self._on_speed_slider_released)
        layout.addWidget(self.slider_speed)

        self.lbl_speed_value = CaptionLabel("1.00x", self)
        self.lbl_speed_value.setFixedWidth(44)
        self.lbl_speed_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_speed_value)
        self._set_speed_label(100)

        self.lbl_render = CaptionLabel("", self)
        self.lbl_render.setFixedWidth(96)
        self.lbl_render.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.lbl_render)

        layout.addWidget(CaptionLabel("音量"))
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

    def _on_slider_pressed(self):
        self._is_dragging = True

    def _on_slider_clicked(self, value: int):
        # 注意：点击轨道是瞬时操作，不经过 sliderPressed/sliderReleased，
        # 不能设 _is_dragging = True，否则后续 set_position() 将永远被屏蔽。
        if self._duration_ms > 0:
            ms = int((value / 10000) * self._duration_ms)
            self._update_label_with_time(ms)
            self.seek_requested.emit(ms)

    def _on_slider_moved(self, value: int):
        self._is_dragging = True
        if self._duration_ms > 0:
            self._update_label_with_time(int((value / 10000) * self._duration_ms))

    def _on_seek(self):
        self._is_dragging = False
        if self._duration_ms > 0:
            self.seek_requested.emit(
                int((self.slider_progress.value() / 10000) * self._duration_ms)
            )

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self._update_label()

    def set_position(self, ms: int):
        self._current_ms = ms
        if self._duration_ms > 0 and not self._is_dragging:
            self.slider_progress.setValue(int((ms / self._duration_ms) * 10000))
            self._update_label()

    def set_playing(self, playing: bool):
        self._is_playing = playing
        self.btn_play.setIcon(FIF.PAUSE if playing else FIF.PLAY)

    def _update_label(self):
        self._update_label_with_time(self._current_ms)

    def _update_label_with_time(self, current_ms: int):
        def fmt(ms):
            s = ms // 1000
            c = (ms % 1000) // 10
            return f"{s // 60:02d}:{s % 60:02d}.{c:02d}"

        self.lbl_time.setText(f"{fmt(current_ms)} / {fmt(self._duration_ms)}")

    @classmethod
    def _speed_to_pct(cls, speed: float) -> int:
        return int(round(float(speed) * 100.0))

    @staticmethod
    def _hard_clamp_speed_pct(pct: int) -> int:
        # 引擎支持 0.2x~2.0x；下限对齐到 20%（此前误写 25% 导致设置项最低只能 0.25x）。
        return max(20, min(200, int(pct)))

    def _clamp_speed_pct(self, pct: int | float) -> int:
        pct = self._hard_clamp_speed_pct(int(round(float(pct))))
        return max(self.slider_speed.minimum(), min(self.slider_speed.maximum(), pct))

    def _set_speed_label(self, pct: int) -> None:
        self.lbl_speed_value.setText(f"{pct / 100.0:.2f}x")
        self.slider_speed.setToolTip(
            f"{self.slider_speed.minimum() / 100.0:.2f}x - "
            f"{self.slider_speed.maximum() / 100.0:.2f}x"
        )

    def _on_speed_slider_changed(self, pct: int):
        pct = self._clamp_speed_pct(pct)
        self._set_speed_label(pct)
        # 拖动中只更新标签，松手时再应用（见 _on_speed_slider_released）；
        # 滚轮/点击/键盘/程序化设值不经拖动，立即生效。
        if not self._speed_dragging:
            self.speed_changed.emit(pct / 100.0)

    def _on_speed_slider_pressed(self):
        self._speed_dragging = True

    def _on_speed_slider_released(self):
        self._speed_dragging = False
        pct = self._clamp_speed_pct(self.slider_speed.value())
        self._set_speed_label(pct)
        self.speed_changed.emit(pct / 100.0)

    def set_speed_range(
        self,
        min_speed: float,
        max_speed: float,
        emit_signal: bool = False,
    ) -> int:
        min_pct = self._hard_clamp_speed_pct(self._speed_to_pct(min_speed))
        max_pct = self._hard_clamp_speed_pct(self._speed_to_pct(max_speed))
        if min_pct > max_pct:
            min_pct, max_pct = max_pct, min_pct
        current = self.slider_speed.value()
        clamped = max(min_pct, min(max_pct, current))

        self.slider_speed.blockSignals(True)
        self.slider_speed.setRange(min_pct, max_pct)
        self.slider_speed.setSingleStep(5)
        self.slider_speed.setPageStep(5)
        self.slider_speed.setValue(clamped)
        self.slider_speed.blockSignals(False)
        self.slider_speed._adjustHandlePos()
        self._set_speed_label(clamped)

        if emit_signal and clamped != current:
            self.speed_changed.emit(clamped / 100.0)
        return clamped

    def set_speed_value(self, pct: int, emit_signal: bool = True) -> int:
        pct = self._clamp_speed_pct(pct)
        if emit_signal:
            self.slider_speed.setValue(pct)
            self._set_speed_label(pct)
        else:
            self.slider_speed.blockSignals(True)
            self.slider_speed.setValue(pct)
            self.slider_speed.blockSignals(False)
            self.slider_speed._adjustHandlePos()
            self._set_speed_label(pct)
        return pct

    def get_speed_value(self) -> int:
        return self._clamp_speed_pct(self.slider_speed.value())

    def set_render_progress(self, speed: float, progress: float) -> None:
        if progress >= 0.999:
            self.lbl_render.setText("")
        else:
            pct = max(0, min(99, int(progress * 100)))
            self.lbl_render.setText(f"{speed:.2f}x 渲染 {pct}%")
