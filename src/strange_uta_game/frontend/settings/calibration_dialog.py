"""Offset 校准弹窗 — 节拍器 + 空格跟拍偏移测量。

依赖 ``SettingsInterface`` 的 ``card_offset`` 字段（由父对象提供），
校准完成后把结果写回该卡片。
"""

from __future__ import annotations

import threading
import time as _time
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QKeyEvent, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    InfoBar,
    InfoBarPosition,
    PushButton,
    SpinBox,
)

if TYPE_CHECKING:
    from .settings_interface import SettingsInterface


class CalibrationCanvas(QWidget):
    """Offset 校准动画画布。"""

    def __init__(self, dialog: "CalibrationDialog", parent=None):
        super().__init__(parent)
        self._dialog = dialog
        self.setMinimumHeight(260)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, a0: QPaintEvent | None):
        _ = a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(18, 18, 22))

        width = max(1, self.width())
        height = max(1, self.height())
        center_x = width / 2
        center_y = height / 2

        painter.setPen(QPen(QColor(235, 70, 70), 3))
        painter.drawLine(int(center_x), 24, int(center_x), height - 24)

        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(2):
            phase = self._dialog.block_phase(index)
            x = ((phase + 0.5) % 1.0) * width
            proximity = 1.0 - min(abs(x - center_x) / max(center_x, 1.0), 1.0)
            scale = 0.55 + proximity * 0.85
            block_width = 18 + 22 * scale
            block_height = 44 + 44 * scale
            alpha = int(110 + 145 * proximity)

            painter.setBrush(QBrush(QColor(255, 255, 255, alpha)))
            rect_x = int(round(x - block_width / 2))
            rect_y = int(round(center_y - block_height / 2))
            rect_w = int(round(block_width))
            rect_h = int(round(block_height))
            painter.drawRoundedRect(
                rect_x,
                rect_y,
                rect_w,
                rect_h,
                10,
                10,
            )


class CalibrationDialog(QDialog):
    """Offset 校准弹窗。"""

    def __init__(self, parent: "SettingsInterface"):
        super().__init__(parent)
        self._settings_interface = parent
        self._sample_rate = 44100
        self._bpm = 120
        self._beat_interval = 60.0 / self._bpm
        self._start_time = _time.monotonic()
        self._next_beat_time = self._start_time
        self._schedule_version = 0
        self._running = False
        self._state_lock = threading.Lock()
        self._metronome_thread: Optional[threading.Thread] = None
        self._beat_times: list[float] = []
        self._tap_offsets_ms: list[float] = []
        self._latest_offset_ms: Optional[float] = None
        self._click_audio = self._generate_click()

        self.setWindowTitle("Offset 校准")
        self.setModal(True)
        self.resize(880, 420)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        left_layout = QVBoxLayout()
        left_layout.setSpacing(4)
        self.lbl_latest = QLabel("最近偏移: -- ms", self)
        self.lbl_latest.setFont(QFont("Microsoft YaHei", 10))
        self.lbl_average = QLabel("平均偏移: -- ms", self)
        self.lbl_average.setFont(QFont("Microsoft YaHei", 10))
        left_layout.addWidget(self.lbl_latest)
        left_layout.addWidget(self.lbl_average)
        top_row.addLayout(left_layout)
        top_row.addStretch(1)

        right_layout = QHBoxLayout()
        right_layout.setSpacing(8)
        self.lbl_bpm = QLabel("节拍 BPM", self)
        self.lbl_bpm.setFont(QFont("Microsoft YaHei", 10))
        self.spin_bpm = SpinBox(self)
        self.spin_bpm.setRange(60, 240)
        self.spin_bpm.setValue(self._bpm)
        self.spin_bpm.setSuffix(" BPM")
        self.spin_bpm.setFixedWidth(130)
        self.spin_bpm.setFont(QFont("Microsoft YaHei", 10))
        self.btn_reset = PushButton("重置", self)
        self.btn_reset.setFont(QFont("Microsoft YaHei", 10))
        self.btn_apply = PushButton("应用", self)
        self.btn_apply.setFont(QFont("Microsoft YaHei", 10))

        right_layout.addWidget(self.lbl_bpm)
        right_layout.addWidget(self.spin_bpm)
        right_layout.addWidget(self.btn_reset)
        right_layout.addWidget(self.btn_apply)
        top_row.addLayout(right_layout)
        root.addLayout(top_row)

        self.canvas = CalibrationCanvas(self, self)
        root.addWidget(self.canvas)

        self.lbl_hint = QLabel(
            "按空格键跟拍，可持续任意次数，关闭窗口前都会保持运行", self
        )
        self.lbl_hint.setFont(QFont("Microsoft YaHei", 9))
        root.addWidget(self.lbl_hint)

        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(16)
        self.animation_timer.timeout.connect(self.canvas.update)

        self.spin_bpm.valueChanged.connect(self._on_bpm_changed)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_apply.clicked.connect(self._on_apply)

        self.spin_bpm.installEventFilter(self)
        self.btn_reset.installEventFilter(self)
        self.btn_apply.installEventFilter(self)
        self.canvas.installEventFilter(self)

    def showEvent(self, a0):
        super().showEvent(a0)
        self._start_metronome()
        self.animation_timer.start()
        QTimer.singleShot(0, self.canvas.setFocus)

    def closeEvent(self, a0):
        self.animation_timer.stop()
        self._stop_metronome()
        super().closeEvent(a0)

    def eventFilter(self, a0, a1):
        if (
            a1 is not None
            and a1.type() == a1.Type.KeyPress
            and isinstance(a1, QKeyEvent)
            and a1.key() == Qt.Key.Key_Space
            and not a1.isAutoRepeat()
        ):
            self._handle_tap()
            a1.accept()
            return True
        return super().eventFilter(a0, a1)

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is not None and a0.key() == Qt.Key.Key_Space and not a0.isAutoRepeat():
            self._handle_tap()
            a0.accept()
            return
        super().keyPressEvent(a0)

    def block_phase(self, index: int) -> float:
        num_blocks = 2
        with self._state_lock:
            start_time = self._start_time
            beat_interval = self._beat_interval
        cycle_duration = beat_interval * num_blocks
        return (
            (_time.monotonic() - start_time) / cycle_duration + index / num_blocks
        ) % 1.0

    def _generate_click(self, sr=44100, duration_ms=30, freq=1000):
        n = int(sr * duration_ms / 1000)
        t = np.arange(n) / sr
        click = 0.5 * np.sin(2 * np.pi * freq * t)
        fade = np.linspace(1.0, 0.0, n)
        click *= fade
        return click.astype(np.float32)

    def _start_metronome(self):
        with self._state_lock:
            if self._running:
                return
            now = _time.monotonic()
            self._start_time = now
            self._next_beat_time = now
            self._beat_times.clear()
            self._schedule_version += 1
            self._running = True

        self._metronome_thread = threading.Thread(
            target=self._play_metronome_loop, daemon=True
        )
        self._metronome_thread.start()

    def _stop_metronome(self):
        with self._state_lock:
            self._running = False
            self._schedule_version += 1
        if self._metronome_thread and self._metronome_thread.is_alive():
            self._metronome_thread.join(timeout=1.0)
        self._metronome_thread = None

    def _play_metronome_loop(self):
        stream = None
        try:
            stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                latency="low",
            )
            stream.start()
        except Exception:
            stream = None

        try:
            while True:
                with self._state_lock:
                    if not self._running:
                        return
                    next_beat_time = self._next_beat_time
                    version = self._schedule_version

                now = _time.monotonic()
                wait_time = next_beat_time - now

                if wait_time > 0.003:
                    _time.sleep(min(wait_time - 0.002, 0.008))
                    continue

                # 精确自旋等待，确保判定时间与视觉中心一致
                while _time.monotonic() < next_beat_time:
                    pass

                beat_time = next_beat_time
                if stream is not None:
                    try:
                        stream.write(self._click_audio)
                    except Exception:
                        pass

                with self._state_lock:
                    if not self._running:
                        return
                    self._beat_times.append(beat_time)
                    if len(self._beat_times) > 256:
                        self._beat_times = self._beat_times[-256:]
                    if (
                        version == self._schedule_version
                        and abs(self._next_beat_time - beat_time) < 0.02
                    ):
                        self._next_beat_time = beat_time + self._beat_interval
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    def _on_bpm_changed(self, value: int):
        now = _time.monotonic()
        new_interval = 60.0 / max(60, min(240, value))
        with self._state_lock:
            old_interval = self._beat_interval
            current_beats = (now - self._start_time) / old_interval
            phase = current_beats - int(current_beats)
            self._bpm = value
            self._beat_interval = new_interval
            self._start_time = now - phase * new_interval
            remaining_phase = 1.0 if phase < 0.000001 else 1.0 - phase
            self._next_beat_time = now + remaining_phase * new_interval
            self._schedule_version += 1
        self.canvas.setFocus()

    def _handle_tap(self):
        tap_time = _time.monotonic()
        offset_ms = self._calculate_tap_offset_ms(tap_time)
        if offset_ms is None:
            return
        self._latest_offset_ms = offset_ms
        self._tap_offsets_ms.append(offset_ms)
        self._update_offset_labels()
        self.canvas.setFocus()

    def _calculate_tap_offset_ms(self, tap_time: float) -> Optional[float]:
        """计算 tap 偏移量（毫秒）。委托后端 :func:`compute_tap_offset_ms`。

        正值 = 按早了（实际 tap 在完美时间之前），负值 = 按晚了。
        """
        from strange_uta_game.backend.application import compute_tap_offset_ms

        with self._state_lock:
            start_time = self._start_time
            beat_interval = self._beat_interval
            if not self._running:
                return None

        return compute_tap_offset_ms(tap_time, start_time, beat_interval)

    def _filtered_average_offset_ms(self) -> Optional[float]:
        from strange_uta_game.backend.application import filtered_average_offset_ms

        return filtered_average_offset_ms(self._tap_offsets_ms)

    def _format_offset_text(self, value: Optional[float]) -> str:
        if value is None:
            return "-- ms"
        return f"{round(value):+d} ms"

    def _update_offset_labels(self):
        average = self._filtered_average_offset_ms()
        self.lbl_latest.setText(
            f"最近偏移: {self._format_offset_text(self._latest_offset_ms)}"
        )
        self.lbl_average.setText(f"平均偏移: {self._format_offset_text(average)}")

    def _on_reset(self):
        self._tap_offsets_ms.clear()
        self._latest_offset_ms = None
        self._update_offset_labels()
        self.canvas.setFocus()

    def _on_apply(self):
        average = self._filtered_average_offset_ms()
        applied_offset = round(average) if average is not None else 0
        self._settings_interface.card_offset.setValue(applied_offset)
        InfoBar.success(
            title="校准完成",
            content=f"已应用 Offset：{applied_offset:+d} ms",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self._settings_interface,
        )
        self._stop_metronome()
        self.accept()
