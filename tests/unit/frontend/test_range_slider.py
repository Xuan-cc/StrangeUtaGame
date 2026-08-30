"""RangeSlider（双柄范围滑块，frontend/fluent_widgets）单元测试。"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from strange_uta_game.frontend.fluent_widgets import RangeSlider


def _send_mouse(widget, event_type, x, y=11.0):
    """向控件合成并发送一个鼠标事件（press/move/release）。"""
    if event_type == QEvent.Type.MouseButtonPress:
        event = QMouseEvent(
            event_type, QPointF(x, y), QPointF(x, y),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    elif event_type == QEvent.Type.MouseButtonRelease:
        event = QMouseEvent(
            event_type, QPointF(x, y), QPointF(x, y),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    else:
        event = QMouseEvent(
            event_type, QPointF(x, y), QPointF(x, y),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    QApplication.sendEvent(widget, event)


class TestRangeSliderValues:
    def test_default_spans_full_range(self, qapp):
        slider = RangeSlider()
        slider.set_range(0.0, 100.0)
        assert slider.low() == 0.0
        assert slider.high() == 100.0

    def test_set_values_clamps_and_emits(self, qapp):
        slider = RangeSlider()
        slider.set_range(0.0, 100.0)
        changed, committed = [], []
        slider.rangeChanged.connect(lambda lo, hi: changed.append((lo, hi)))
        slider.rangeCommitted.connect(lambda lo, hi: committed.append((lo, hi)))

        slider.set_values(20.0, 80.0, emit=True)
        assert slider.low() == 20.0 and slider.high() == 80.0
        assert changed == [(20.0, 80.0)]
        assert committed == [(20.0, 80.0)]

        # 越界钳到值域
        slider.set_values(-50.0, 500.0, emit=True)
        assert slider.low() == 0.0 and slider.high() == 100.0

    def test_min_span_enforced(self, qapp):
        slider = RangeSlider()
        slider.set_range(0.0, 100.0)
        # 低柄最多推到 high - 5（值域 5%）
        slider.set_low(98.0)
        assert slider.low() == pytest.approx(95.0)
        # 高柄最少抬到 low + 5
        slider.set_values(10.0, 90.0)
        slider.set_high(11.0)
        assert slider.high() == pytest.approx(15.0)
        assert slider.high() > slider.low()

    def test_set_values_insufficient_span_spreads(self, qapp):
        slider = RangeSlider()
        slider.set_range(0.0, 100.0)
        slider.set_values(40.0, 41.0)  # 间距 1 < 5：以中点对撑
        assert slider.high() - slider.low() == pytest.approx(5.0)
        assert slider.low() + slider.high() == pytest.approx(81.0)

    def test_set_values_no_emit_by_default(self, qapp):
        slider = RangeSlider()
        slider.set_range(0.0, 100.0)
        changed = []
        slider.rangeChanged.connect(lambda lo, hi: changed.append((lo, hi)))
        slider.set_values(20.0, 80.0)
        assert changed == []


class TestRangeSliderMouse:
    def test_press_move_release_emits_commit(self, qapp):
        slider = RangeSlider()
        slider.set_range(0.0, 100.0)
        slider.resize(208, 22)
        slider.show()
        committed = []
        slider.rangeCommitted.connect(lambda lo, hi: committed.append((lo, hi)))

        margin = slider._HANDLE_R + 2.0  # 8
        usable = 208 - 2 * margin        # 192
        # 按下高柄（初始 x = margin+192=200）并向左拖到 25% 处（x=56）
        _send_mouse(slider, QEvent.Type.MouseButtonPress, 200.0)
        _send_mouse(slider, QEvent.Type.MouseMove, 56.0)
        _send_mouse(slider, QEvent.Type.MouseButtonRelease, 56.0)

        assert slider.high() == pytest.approx(25.0, abs=1.5)
        assert slider.low() == 0.0
        assert committed == [(slider.low(), slider.high())]

    def test_track_click_snaps_nearest_handle(self, qapp):
        slider = RangeSlider()
        slider.set_range(0.0, 100.0)
        slider.resize(208, 22)
        slider.show()
        # 点击中点偏左（离低柄近）：低柄吸附到 50% 再拖到 60%
        _send_mouse(slider, QEvent.Type.MouseButtonPress, 100.0)
        assert slider.low() == pytest.approx(48.0, abs=2.0)
        _send_mouse(slider, QEvent.Type.MouseMove, 124.0)
        _send_mouse(slider, QEvent.Type.MouseButtonRelease, 124.0)
        assert slider.low() == pytest.approx(60.0, abs=1.5)
        assert slider.high() == 100.0

    def test_low_cannot_cross_high_while_dragging(self, qapp):
        slider = RangeSlider()
        slider.set_range(0.0, 100.0)
        slider.set_values(30.0, 70.0)
        slider.resize(208, 22)
        slider.show()
        # 拖低柄越过高柄：被最小间距挡住
        low_x = slider._value_to_x(slider.low())
        _send_mouse(slider, QEvent.Type.MouseButtonPress, low_x)
        _send_mouse(slider, QEvent.Type.MouseMove, slider._value_to_x(95.0))
        _send_mouse(slider, QEvent.Type.MouseButtonRelease, slider._value_to_x(95.0))
        assert slider.low() == pytest.approx(65.0)
        assert slider.high() == 70.0
