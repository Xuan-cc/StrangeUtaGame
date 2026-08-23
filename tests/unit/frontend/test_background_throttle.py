"""前后台节流：窗口最小化/隐藏时非音频服务降频。

窗口可见但失焦（用户一边看预览一边在别的窗口干活）不降频；
只有窗口真的看不见（最小化/隐藏）才降低 UI 轮询频率。
"""

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from strange_uta_game.frontend.background_throttle import (
    background_throttle,
    set_visibility_override,
)
from strange_uta_game.frontend.editor.timing_interface import EditorInterface


def test_throttle_reacts_to_window_show_hide(qapp):
    throttle = background_throttle()
    assert throttle is not None

    for widget in qapp.topLevelWidgets():
        if widget.isVisible():
            widget.hide()
    qapp.processEvents()
    assert throttle.is_visible is False

    window = QWidget()
    window.show()
    qapp.processEvents()
    assert throttle.is_visible is True

    window.hide()
    qapp.processEvents()
    assert throttle.is_visible is False


def test_throttle_broadcasts_on_window_events(qapp):
    throttle = background_throttle()
    calls = []

    def record():
        calls.append(1)

    throttle.visibility_maybe_changed.connect(record)

    window = QWidget()
    window.show()
    qapp.processEvents()
    window.hide()
    qapp.processEvents()

    assert len(calls) >= 2


class _StubWindow:
    def __init__(self, visible: bool, minimized: bool = False):
        self._visible = visible
        self._minimized = minimized

    def isVisible(self) -> bool:
        return self._visible

    def isMinimized(self) -> bool:
        return self._minimized


class _StubEditor:
    def __init__(self, window):
        self._window = window
        self._position_poll_timer = QTimer()
        self._position_poll_fg_interval_ms = 16
        self._position_poll_bg_interval_ms = 200

    def window(self):
        return self._window


def test_position_poll_keeps_full_rate_when_visible_but_unfocused(qapp, monkeypatch):
    """窗口可见但失焦（分屏看预览场景）必须保持用户设置的刷新率。"""
    from strange_uta_game.frontend.editor import timing_interface as ti_module

    # 隔离全局判定：本用例只验证编辑器自身窗口链的可见性逻辑
    monkeypatch.setattr(ti_module, "ui_visible", lambda: True)
    editor = _StubEditor(_StubWindow(visible=True, minimized=False))
    editor._position_poll_timer.setInterval(editor._position_poll_fg_interval_ms)

    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 16


def test_position_poll_throttles_when_minimized_or_hidden(qapp, monkeypatch):
    from strange_uta_game.frontend.editor import timing_interface as ti_module

    monkeypatch.setattr(ti_module, "ui_visible", lambda: True)

    editor = _StubEditor(_StubWindow(visible=True, minimized=True))
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 200

    editor = _StubEditor(_StubWindow(visible=False, minimized=False))
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 200


def test_position_poll_restores_updated_fg_interval(qapp, monkeypatch):
    """隐藏期间用户改了刷新率设置，恢复可见后应使用新值而非旧值。"""
    from strange_uta_game.frontend.editor import timing_interface as ti_module

    monkeypatch.setattr(ti_module, "ui_visible", lambda: True)

    editor = _StubEditor(_StubWindow(visible=False))
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 200

    editor._position_poll_fg_interval_ms = 33  # 模拟隐藏期间应用 30fps 设置
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 200  # 仍隐藏，保持降频

    editor._window = _StubWindow(visible=True)
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 33


def test_host_notification_forces_hidden_even_with_visible_windows(qapp):
    """嵌入式宿主显式通知 False：即使还有可见窗口也按隐藏处理。

    场景：宿主隐藏 SUG 区域（切标签页）但宿主顶层窗口仍可见——
    编辑器按 self.window() 自动判定解析到宿主窗口，看不到这层。
    """
    throttle = background_throttle()
    assert throttle is not None
    window = QWidget()
    window.show()
    qapp.processEvents()
    assert throttle.is_visible is True

    calls = []
    throttle.visibility_maybe_changed.connect(lambda: calls.append(1))
    try:
        set_visibility_override(False)
        assert throttle.is_visible is False
        assert len(calls) == 1

        # 重复通知幂等，不重复广播
        set_visibility_override(False)
        assert len(calls) == 1

        # True 恢复自动判定（不强制可见）：窗口可见 → True
        set_visibility_override(True)
        assert throttle.is_visible is True
        assert len(calls) == 2
    finally:
        set_visibility_override(None)
    window.hide()


def test_position_poll_hidden_by_host_notification(qapp, monkeypatch):
    """宿主通知隐藏后，编辑器即使窗口链可见也应降频。"""
    from strange_uta_game.frontend.editor import timing_interface as ti_module

    monkeypatch.setattr(ti_module, "ui_visible", lambda: True)
    editor = _StubEditor(_StubWindow(visible=True, minimized=False))
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 16

    monkeypatch.setattr(ti_module, "ui_visible", lambda: False)
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 200


def test_theme_poll_timer_follows_app_visibility(qapp, monkeypatch):
    from strange_uta_game.frontend import theme as theme_module

    class _StubThrottle:
        def __init__(self, visible):
            self.is_visible = visible

    manager = theme_module.theme
    original_timer = manager._poll_timer
    timer = QTimer()
    timer.setInterval(2000)
    timer.start()
    manager._poll_timer = timer
    try:
        monkeypatch.setattr(
            theme_module, "background_throttle", lambda: _StubThrottle(False)
        )
        manager._on_ui_visibility_maybe_changed()
        assert not timer.isActive()

        monkeypatch.setattr(
            theme_module, "background_throttle", lambda: _StubThrottle(True)
        )
        timer.stop()
        manager._on_ui_visibility_maybe_changed()
        assert timer.isActive()
    finally:
        timer.stop()
        manager._poll_timer = original_timer


def test_calibration_animation_follows_dialog_visibility(qapp):
    from strange_uta_game.frontend.settings.calibration_dialog import (
        CalibrationDialog,
    )

    def make_stub(visible, minimized=False):
        stub = type("_StubDialog", (), {})()
        stub.animation_timer = QTimer()
        stub.animation_timer.start()
        stub._window = _StubWindow(visible, minimized)
        stub.isVisible = lambda: stub._window.isVisible()
        stub.isMinimized = lambda: stub._window.isMinimized()
        return stub

    # 可见但失焦：动画继续（分屏场景）
    stub = make_stub(visible=True)
    CalibrationDialog._on_ui_visibility_maybe_changed(stub)
    assert stub.animation_timer.isActive()

    # 最小化 / 隐藏：暂停
    stub = make_stub(visible=True, minimized=True)
    CalibrationDialog._on_ui_visibility_maybe_changed(stub)
    assert not stub.animation_timer.isActive()

    stub = make_stub(visible=False)
    CalibrationDialog._on_ui_visibility_maybe_changed(stub)
    assert not stub.animation_timer.isActive()
