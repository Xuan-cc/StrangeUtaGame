from types import SimpleNamespace

from PyQt6.QtCore import Qt

from strange_uta_game.frontend.editor.timing_interface import EditorInterface


class _FakeTimer:
    def __init__(self, active=True):
        self.active = active
        self.start_count = 0
        self.stop_count = 0

    def isActive(self):
        return self.active

    def start(self):
        self.active = True
        self.start_count += 1

    def stop(self):
        self.active = False
        self.stop_count += 1


class _FakeKeyEvent:
    def __init__(self, *, auto_repeat):
        self._auto_repeat = auto_repeat
        self.accepted = False
        self.ignored = False

    def key(self):
        return Qt.Key.Key_F5

    def modifiers(self):
        return Qt.KeyboardModifier.NoModifier

    def nativeVirtualKey(self):
        return 0

    def nativeScanCode(self):
        return 0

    def isAutoRepeat(self):
        return self._auto_repeat

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


def _editor_double(timer):
    executed = []
    return SimpleNamespace(
        _timing_service=None,
        _key_map_short={"F5": "short_action"},
        _key_map_long={"F5": "long_action"},
        _settings_loaded=True,
        _pending_press_key="F5",
        _pending_press_action_short="short_action",
        _pending_press_action_long="long_action",
        _long_press_timer=timer,
        _qt_key_to_name=lambda *args: "F5",
        _execute_action=lambda action, key: executed.append((action, key)),
        _executed=executed,
    )


def test_long_press_auto_repeat_press_does_not_restart_timer():
    timer = _FakeTimer(active=True)
    editor = _editor_double(timer)
    event = _FakeKeyEvent(auto_repeat=True)

    EditorInterface._keyPressEvent_impl(editor, event)

    assert event.ignored is True
    assert timer.start_count == 0
    assert editor._pending_press_key == "F5"
    assert editor._executed == []


def test_long_press_auto_repeat_release_does_not_trigger_short_action():
    timer = _FakeTimer(active=True)
    editor = _editor_double(timer)
    event = _FakeKeyEvent(auto_repeat=True)

    EditorInterface.keyReleaseEvent(editor, event)

    assert event.ignored is True
    assert timer.stop_count == 0
    assert editor._pending_press_key == "F5"
    assert editor._executed == []


def test_short_only_binding_keeps_auto_repeat_behavior():
    timer = _FakeTimer(active=False)
    editor = _editor_double(timer)
    editor._key_map_long = {}
    event = _FakeKeyEvent(auto_repeat=True)

    EditorInterface._keyPressEvent_impl(editor, event)

    assert event.accepted is True
    assert editor._executed == [("short_action", Qt.Key.Key_F5)]
