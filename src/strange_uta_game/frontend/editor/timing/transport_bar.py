"""Playback transport bar."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygon, QWheelEvent
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel
from qfluentwidgets import (
    CaptionLabel,
    PrimaryToolButton,
    Slider,
    ToolButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)
from qfluentwidgets.components.widgets.slider import SliderHandle as _SliderHandle

from strange_uta_game.frontend.theme import theme


class PlaybackRangeSlider(Slider):
    """Progress slider with start/end playback-range markers."""

    def __init__(self, orientation: Qt.Orientation, parent=None):
        super().__init__(orientation, parent)
        self._range_start_value: int | None = None
        self._range_end_value: int | None = None

    def set_range_markers(
        self, start_value: int | None, end_value: int | None
    ) -> None:
        self._range_start_value = start_value
        self._range_end_value = end_value
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        span = self.maximum() - self.minimum()
        if span <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left = 7
        usable = max(1, self.width() - left * 2)

        def marker_x(value: int) -> int:
            ratio = (value - self.minimum()) / span
            return left + int(max(0.0, min(1.0, ratio)) * usable)

        if self._range_start_value is not None:
            x = marker_x(self._range_start_value)
            color = theme.status_complete
            painter.setPen(QPen(color, 2))
            painter.drawLine(x, 2, x, self.height() - 2)
            painter.setBrush(color)
            painter.drawPolygon(QPolygon([
                QPoint(x - 5, 1), QPoint(x + 5, 1), QPoint(x, 7)
            ]))

        if self._range_end_value is not None:
            x = marker_x(self._range_end_value)
            color = theme.accent_warning
            painter.setPen(QPen(color, 2))
            painter.drawLine(x, 2, x, self.height() - 2)
            painter.setBrush(color)
            bottom = self.height() - 1
            painter.drawPolygon(QPolygon([
                QPoint(x - 5, bottom), QPoint(x + 5, bottom), QPoint(x, bottom - 6)
            ]))


class _ResetHandle(_SliderHandle):
    """SliderHandle that resets the parent slider on double-click."""

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        p = self.parent()
        if isinstance(p, WheelSpeedSlider):
            p.resetToDefault()
        if event is not None:
            event.accept()


class WheelSpeedSlider(Slider):
    """Slider that accepts wheel input while hovered and resets on double-click."""

    def __init__(
        self,
        orientation: Qt.Orientation,
        parent=None,
        default_value: int | None = None,
    ):
        super().__init__(orientation, parent)
        self._default_value = default_value

    def _postInit(self):
        self.handle = _ResetHandle(self)
        self._pressedPos = QPoint()
        self.lightGrooveColor = QColor()
        self.darkGrooveColor = QColor()
        self.setOrientation(self.orientation())
        self.handle.pressed.connect(self.sliderPressed)
        self.handle.released.connect(self.sliderReleased)
        self.valueChanged.connect(self._adjustHandlePos)

    def setDefaultValue(self, value: int) -> None:
        self._default_value = value

    def resetToDefault(self) -> None:
        """Reset through the same value-change path used by double-click."""
        if self._default_value is not None:
            self.setValue(self._default_value)

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
        self._range_start_ms: int | None = None
        self._range_end_ms: int | None = None
        self._is_playing = False
        self._is_dragging = False
        # 速度滑块拖动中：拖动期间只更新标签，松手（sliderReleased）才应用速度，
        # 避免每个中间值都触发变速（HQ 模式下会刷屏预渲染）。滚轮/点击/键盘
        # 不经拖动，仍即时生效。
        self._speed_dragging = False
        self.setFixedHeight(56)
        self._init_ui()

    def changeEvent(self, event):
        """切语言时拆掉重建——只是几个 slider + label，状态都在 self._* 实例变量上保留。"""
        if event.type() == QEvent.Type.LanguageChange:
            from strange_uta_game.frontend.localization import detach_layout_for_rebuild
            saved = {
                "duration": self._duration_ms,
                "current": self._current_ms,
                "playing": self._is_playing,
                "range_start": self._range_start_ms,
                "range_end": self._range_end_ms,
            }
            detach_layout_for_rebuild(self)
            self._init_ui()
            # 还原显示状态
            self.set_duration(saved["duration"])
            self.set_position(saved["current"])
            self.set_playing(saved["playing"])
            self.set_playback_range(saved["range_start"], saved["range_end"])
        super().changeEvent(event)

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

        self.slider_progress = PlaybackRangeSlider(Qt.Orientation.Horizontal, self)
        self.slider_progress.setRange(0, 10000)
        self.slider_progress.setValue(0)
        self.slider_progress.clicked.connect(self._on_slider_clicked)
        self.slider_progress.sliderPressed.connect(self._on_slider_pressed)
        self.slider_progress.sliderMoved.connect(self._on_slider_moved)
        self.slider_progress.sliderReleased.connect(self._on_seek)
        self._sync_range_markers()
        layout.addWidget(self.slider_progress, stretch=1)

        layout.addWidget(CaptionLabel(self.tr("速度")))
        self.slider_speed = WheelSpeedSlider(Qt.Orientation.Horizontal, self, default_value=100)
        self.slider_speed.setRange(50, 100)
        self.slider_speed.setSingleStep(5)
        self.slider_speed.setPageStep(5)
        self.slider_speed.setValue(100)
        self.slider_speed.setMinimumWidth(116)
        self.slider_speed.setMaximumWidth(200)
        self.slider_speed.valueChanged.connect(self._on_speed_slider_changed)
        self.slider_speed.sliderPressed.connect(self._on_speed_slider_pressed)
        self.slider_speed.sliderReleased.connect(self._on_speed_slider_released)
        layout.addWidget(self.slider_speed)

        self.lbl_speed_value = CaptionLabel("1.00x", self)
        # "1.00x" 在中文/英文都是 5 字符；用 minimum 防止极端字号下挤压。
        self.lbl_speed_value.setMinimumWidth(44)
        self.lbl_speed_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_speed_value)
        self._set_speed_label(100)

        self.lbl_render = CaptionLabel("", self)
        # 渲染状态文本：本地化后 "Rendering 50%" / "レンダリング 50%" 都比中文更长。
        self.lbl_render.setMinimumWidth(96)
        self.lbl_render.setMaximumWidth(180)
        self.lbl_render.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.lbl_render)

        layout.addWidget(CaptionLabel(self.tr("音量")))
        self.slider_volume = WheelSpeedSlider(Qt.Orientation.Horizontal, self, default_value=100)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(100)
        self.slider_volume.setMinimumWidth(100)
        self.slider_volume.setMaximumWidth(180)
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
        self._sync_range_markers()
        self._update_label()

    def set_playback_range(
        self, start_ms: int | None, end_ms: int | None
    ) -> None:
        self._range_start_ms = start_ms
        self._range_end_ms = end_ms
        self._sync_range_markers()

    def _sync_range_markers(self) -> None:
        if not hasattr(self, "slider_progress"):
            return

        def to_value(ms: int | None) -> int | None:
            if ms is None or self._duration_ms <= 0:
                return None
            ratio = max(0.0, min(1.0, ms / self._duration_ms))
            return int(round(ratio * 10000))

        self.slider_progress.set_range_markers(
            to_value(self._range_start_ms), to_value(self._range_end_ms)
        )

    @staticmethod
    def _left_mouse_button_is_down() -> bool:
        """Return whether a real progress-slider drag can still be active."""
        return bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    def _is_progress_drag_active(self) -> bool:
        """Recover when qfluentwidgets loses the handle's release signal.

        qfluentwidgets' custom SliderHandle emits sliderPressed/sliderReleased
        itself and does not maintain QAbstractSlider.isSliderDown().  If the
        handle loses mouse capture before emitting sliderReleased, the local
        flag otherwise remains true forever and blocks every playback update.
        """
        if not self._is_dragging:
            return False
        if self._left_mouse_button_is_down():
            return True
        self._is_dragging = False
        return False

    def set_position(self, ms: int):
        self._current_ms = ms
        if self._duration_ms > 0 and not self._is_progress_drag_active():
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
            self.lbl_render.setText(self.tr("{speed:.2f}x 渲染 {pct}%").format(speed=speed, pct=pct))

    def set_default_speed(self, pct: int) -> None:
        """Set the default speed value for double-click reset."""
        self.slider_speed.setDefaultValue(self._clamp_speed_pct(pct))

    def reset_speed(self) -> None:
        """Restore the configured default speed."""
        self.slider_speed.resetToDefault()

    def set_default_volume(self, vol: int) -> None:
        """Set the default volume value for double-click reset."""
        self.slider_volume.setDefaultValue(max(0, min(100, vol)))

    def reset_volume(self) -> None:
        """Restore the configured default volume."""
        self.slider_volume.resetToDefault()
