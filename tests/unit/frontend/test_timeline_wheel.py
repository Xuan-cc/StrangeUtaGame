from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, Qt

from strange_uta_game.frontend.editor.timing.timeline_widget import WaveformDisplay


class _WheelEvent:
    def __init__(self, delta: int, *, ctrl: bool = False, x: float = 500.0):
        self._delta = delta
        self._ctrl = ctrl
        self._x = x
        self.accepted = False
        self.ignored = False

    def angleDelta(self) -> QPoint:  # noqa: N802 - 模拟 Qt 事件接口
        return QPoint(0, self._delta)

    def modifiers(self) -> Qt.KeyboardModifier:
        if self._ctrl:
            return Qt.KeyboardModifier.ControlModifier
        return Qt.KeyboardModifier.NoModifier

    def position(self) -> QPointF:
        return QPointF(self._x, 0.0)

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


def _display(qapp) -> WaveformDisplay:
    display = WaveformDisplay()
    display.resize(1_000, 100)
    display.set_duration(10_000)
    display.set_zoom(10.0)
    display.set_scroll_position(0.5)
    return display


def test_plain_wheel_pans_without_changing_zoom(qapp):
    display = _display(qapp)
    event = _WheelEvent(-120)

    display.wheelEvent(event)

    assert display._zoom_factor == 10.0
    assert display._scroll_position == 0.51
    assert display._auto_scroll_suspended
    assert event.accepted


def test_ctrl_wheel_zooms_around_pointer(qapp):
    display = _display(qapp)
    event = _WheelEvent(120, ctrl=True, x=250.0)

    display.wheelEvent(event)

    assert display._zoom_factor == 12.0
    # 指针下的音频位置在缩放前后保持不变。
    assert display._scroll_position == 0.5 + 0.25 / 10.0 - 0.25 / 12.0
    assert display._auto_scroll_suspended
    assert event.accepted


def test_plain_wheel_clamps_at_timeline_edges(qapp):
    display = _display(qapp)
    display.set_scroll_position(0.0)

    display.wheelEvent(_WheelEvent(120))

    assert display._scroll_position == 0.0
