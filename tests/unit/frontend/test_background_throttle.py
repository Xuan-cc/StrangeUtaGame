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
    _position_poll_hidden = EditorInterface._position_poll_hidden
    _refresh_position_poll_interval = EditorInterface._refresh_position_poll_interval
    _apply_playback_range = EditorInterface._apply_playback_range
    _poll_audio_position = EditorInterface._poll_audio_position

    def __init__(self, window):
        self._window = window
        self._position_poll_timer = QTimer()
        self._position_poll_fg_interval_ms = 16
        self._position_poll_bg_interval_ms = 200
        self._playback_range_start_ms = None
        self._playback_range_end_ms = None

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


def test_locked_range_keeps_fast_poll_when_hidden(qapp, monkeypatch):
    """锁定播放区间终点是音频行为：隐藏期间也必须保持高频检测。

    否则音频会越过终点约一个后台轮询间隔（200ms）才 seek+暂停，
    违背"音频服务不降频"。
    """
    from strange_uta_game.frontend.editor import timing_interface as ti_module

    monkeypatch.setattr(ti_module, "ui_visible", lambda: True)
    editor = _StubEditor(_StubWindow(visible=False))
    editor._position_poll_timer.setInterval(200)  # 已处于降频态

    editor._playback_range_end_ms = 5000
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 16

    editor._playback_range_end_ms = None
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 200

    editor._window = _StubWindow(visible=True)
    editor._playback_range_end_ms = 5000
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 16


def test_apply_playback_range_refreshes_poll_interval(qapp, monkeypatch):
    """区间变化（含 undo/redo，都走 _apply_playback_range）后立即重估频率。"""
    from strange_uta_game.frontend.editor import timing_interface as ti_module

    monkeypatch.setattr(ti_module, "ui_visible", lambda: True)

    class _RangeRecorder:
        def __init__(self):
            self.ranges = []

        def set_playback_range(self, start, end):
            self.ranges.append((start, end))

    editor = _StubEditor(_StubWindow(visible=False))
    editor.transport = _RangeRecorder()
    editor.timeline = _RangeRecorder()
    EditorInterface._refresh_position_poll_interval(editor)
    assert editor._position_poll_timer.interval() == 200

    editor._apply_playback_range(1000, 5000)
    assert editor._position_poll_timer.interval() == 16

    editor._apply_playback_range(None, None)
    assert editor._position_poll_timer.interval() == 200
    assert editor.transport.ranges == [(1000, 5000), (None, None)]


def test_poll_audio_position_skips_ui_updates_when_hidden(qapp, monkeypatch):
    """隐藏 tick 只做检测（结束/锁定终点），不刷新 UI 控件。"""
    from strange_uta_game.frontend.editor import timing_interface as ti_module

    monkeypatch.setattr(ti_module, "ui_visible", lambda: True)

    class _UIRecorder:
        def __init__(self):
            self.calls = []

        def set_duration(self, value):
            self.calls.append(("duration", value))

        def set_position(self, value):
            self.calls.append(("position", value))

        def set_current_time_ms(self, value):
            self.calls.append(("current", value))

    recorder = _UIRecorder()
    engine = type("_Engine", (), {"is_playing": lambda self: True})()
    service = type(
        "_Service",
        (),
        {
            "_audio_engine": engine,
            "get_position_ms": lambda self: 1000,
            "get_duration_ms": lambda self: 5000,
        },
    )()

    editor = _StubEditor(_StubWindow(visible=False))
    editor.y = lambda: 0
    editor._timing_service = service
    editor.transport = recorder
    editor.timeline = recorder
    editor.preview = recorder
    editor._last_polled_duration_ms = None

    editor._poll_audio_position()
    assert recorder.calls == []
    assert editor._last_polled_duration_ms is None

    editor._window = _StubWindow(visible=True)
    editor._poll_audio_position()
    assert ("duration", 5000) in recorder.calls
    assert ("position", 1000) in recorder.calls
    assert editor._last_polled_duration_ms == 5000


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


def test_theme_poll_reconciles_immediately_on_connect(qapp, monkeypatch):
    """轮询在全部窗口隐藏时启动（如宿主隐藏 SUG 后才初始化主题监听），
    接入节流器时应立即校正为停止态，不等下一次可见性事件。"""
    from strange_uta_game.frontend import theme as theme_module

    class _StubSignal:
        def __init__(self):
            self.connected = []

        def connect(self, slot):
            self.connected.append(slot)

    class _StubThrottle:
        def __init__(self, visible):
            self.visibility_maybe_changed = _StubSignal()
            self.is_visible = visible

    manager = theme_module.theme
    original_timer = manager._poll_timer
    manager._poll_timer = None
    stub = _StubThrottle(visible=False)
    monkeypatch.setattr(theme_module, "background_throttle", lambda: stub)
    try:
        manager._start_polling()
        timer = manager._poll_timer
        assert timer is not None
        assert len(stub.visibility_maybe_changed.connected) == 1
        assert not timer.isActive(), "接入即校正：隐藏态下不应处于运行态"
    finally:
        if manager._poll_timer is not None:
            manager._poll_timer.stop()
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
