"""快捷键设置测试。"""

from types import SimpleNamespace

from strange_uta_game.frontend.settings.settings_interface import InfoBar
from strange_uta_game.frontend.settings.settings_interface import SettingsInterface


class _FakeButton:
    """模拟按键按钮。

    入参：key_name 初始按键。
    出参：无。
    """

    def __init__(self, key_name: str):
        self._captured_key = key_name
        self._original_key = key_name

    def get_key(self) -> str:
        return self._captured_key

    def restore_original_key(self):
        self._captured_key = self._original_key

    def set_captured_key(self, key_name: str):
        self._captured_key = key_name


class _FakeCard:
    """模拟快捷键卡片。

    入参：primary 第一按键，secondary 第二按键。
    出参：无。
    """

    def __init__(self, primary: str = "", secondary: str = ""):
        self.btn_key1 = _FakeButton(primary)
        self.btn_key2 = _FakeButton(secondary)

    def value(self) -> str:
        first_key = self.btn_key1.get_key().strip()
        second_key = self.btn_key2.get_key().strip()
        if first_key and second_key:
            return f"{first_key},{second_key}"
        return first_key or second_key

    def all_keys(self) -> list[str]:
        keys: list[str] = []
        for button in (self.btn_key1, self.btn_key2):
            key_name = button.get_key().strip()
            if key_name:
                keys.append(key_name.upper())
        return keys

    def clear_key_by_name(self, key_name: str):
        upper_key = key_name.upper()
        for button in (self.btn_key1, self.btn_key2):
            if button.get_key().strip().upper() == upper_key:
                button.set_captured_key("")


class _SignalRecorder:
    """模拟信号对象。

    入参：无。
    出参：无。
    """

    def __init__(self):
        self.emit_count = 0

    def emit(self):
        self.emit_count += 1


class _SettingsRecorder:
    """模拟设置对象。

    入参：无。
    出参：无。
    """

    def __init__(self):
        self.save_count = 0

    def save(self):
        self.save_count += 1


def _build_interface_double(changed_card: _FakeCard, other_card: _FakeCard):
    """构造最小化设置界面替身。

    入参：changed_card 当前修改卡片，other_card 同模式另一卡片。
    出参：可调用 SettingsInterface 方法的替身对象。
    """

    interface = SimpleNamespace()
    interface._loading_settings = False
    interface._shortcut_cards = {
        "timing_mode": {
            "play_pause": changed_card,
            "stop": other_card,
        },
        "edit_mode": {},
    }
    interface._SHORTCUT_MODES = SettingsInterface._SHORTCUT_MODES
    interface._SHORTCUT_ACTIONS = SettingsInterface._SHORTCUT_ACTIONS
    interface._get_all_shortcut_cards = lambda: []
    interface._schedule_auto_save_calls = 0
    interface._schedule_auto_save = lambda *_args: setattr(
        interface,
        "_schedule_auto_save_calls",
        interface._schedule_auto_save_calls + 1,
    )
    return interface


def test_conflict_on_empty_preserves_others(monkeypatch):
    warning_calls: list[dict[str, str]] = []
    monkeypatch.setattr(InfoBar, "warning", lambda **kwargs: warning_calls.append(kwargs))

    card_a = _FakeCard("")
    card_b = _FakeCard("Ctrl+S")
    card_a.btn_key1._original_key = ""
    card_a.btn_key1.set_captured_key("Ctrl+S")
    interface = _build_interface_double(card_a, card_b)

    SettingsInterface._on_shortcut_changed(interface, card_a, "Ctrl+S")

    assert card_a.value() == ""
    assert card_b.value() == "Ctrl+S"
    assert len(warning_calls) == 1
    assert interface._schedule_auto_save_calls == 0


def test_conflict_on_set_preserves_others(monkeypatch):
    monkeypatch.setattr(InfoBar, "warning", lambda **kwargs: None)

    card_a = _FakeCard("F5")
    card_b = _FakeCard("Ctrl+S")
    card_a.btn_key1._original_key = "F5"
    card_a.btn_key1.set_captured_key("Ctrl+S")
    interface = _build_interface_double(card_a, card_b)

    SettingsInterface._on_shortcut_changed(interface, card_a, "Ctrl+S")

    assert card_a.value() == "F5"
    assert card_b.value() == "Ctrl+S"
    assert interface._schedule_auto_save_calls == 0


def test_save_path_does_not_clear_other_cards(monkeypatch):
    clear_calls: list[tuple[str, str]] = []
    card_a = _FakeCard("F5")
    card_b = _FakeCard("Ctrl+S")

    def _record_clear(self, key_name: str):
        clear_calls.append((self.value(), key_name))

    monkeypatch.setattr(_FakeCard, "clear_key_by_name", _record_clear)

    interface = SimpleNamespace()
    interface._collect_settings_calls = 0
    interface._collect_settings = lambda: setattr(
        interface,
        "_collect_settings_calls",
        interface._collect_settings_calls + 1,
    )
    interface._settings = _SettingsRecorder()
    interface.settings_changed = _SignalRecorder()
    interface._store = None
    interface._shortcut_cards = {
        "timing_mode": {
            "add_checkpoint": card_a,
            "play_pause": card_b,
        },
        "edit_mode": {},
    }

    SettingsInterface._do_auto_save(interface)

    assert card_a.value() == "F5"
    assert card_b.value() == "Ctrl+S"
    assert clear_calls == []
    assert interface._collect_settings_calls == 1
    assert interface._settings.save_count == 1
    assert interface.settings_changed.emit_count == 1
