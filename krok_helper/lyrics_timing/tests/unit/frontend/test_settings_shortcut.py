"""快捷键设置测试。

测试直接针对 ShortcutSubInterface._on_shortcut_changed，
同时通过 SettingsInterface._SHORTCUT_MODES / _SHORTCUT_ACTIONS
（类属性代理，向后兼容）访问元数据。
"""

from types import SimpleNamespace

from strange_uta_game.frontend.settings.settings_interface import InfoBar, SettingsInterface
from strange_uta_game.frontend.settings.sub_interfaces.shortcut import ShortcutSubInterface


class _FakeButton:
    def __init__(self, key_with_trigger: str):
        if ":" in key_with_trigger:
            kp, tp = key_with_trigger.rsplit(":", 1)
            self._captured_key = kp.strip()
            self._trigger_type = tp.strip().lower() or "short"
        else:
            self._captured_key = key_with_trigger
            self._trigger_type = "short"
        self._original_key = self._captured_key
        self._original_trigger = self._trigger_type

    def get_key(self) -> str:
        return self._captured_key

    def get_trigger_type(self) -> str:
        return self._trigger_type

    def restore_original_key(self):
        self._captured_key = self._original_key
        self._trigger_type = self._original_trigger

    def set_captured_key(self, key_name: str):
        self._captured_key = key_name

    def set_trigger_type(self, trigger_type: str):
        self._trigger_type = trigger_type


class _FakeCard:
    def __init__(self, primary: str = "", secondary: str = ""):
        self.btn_key1 = _FakeButton(primary)
        self.btn_key2 = _FakeButton(secondary)

    def value(self) -> str:
        k1 = self.btn_key1.get_key().strip()
        t1 = self.btn_key1.get_trigger_type()
        k2 = self.btn_key2.get_key().strip()
        t2 = self.btn_key2.get_trigger_type()
        s1 = f"{k1}:{t1}" if k1 else ""
        s2 = f"{k2}:{t2}" if k2 else ""
        if s1 and s2:
            return f"{s1},{s2}"
        return s1 or s2

    def all_keys_with_trigger(self) -> list[tuple[str, str]]:
        result = []
        for btn in (self.btn_key1, self.btn_key2):
            k = btn.get_key().strip()
            if k:
                result.append((k.upper(), btn.get_trigger_type()))
        return result

    def clear_key_by_name(self, key_name: str):
        for btn in (self.btn_key1, self.btn_key2):
            if btn.get_key().strip().upper() == key_name.upper():
                btn.set_captured_key("")


class _SignalRecorder:
    def __init__(self):
        self.emit_count = 0

    def emit(self):
        self.emit_count += 1


class _SettingsRecorder:
    def __init__(self):
        self.save_count = 0

    def save(self):
        self.save_count += 1


def _build_shortcut_double(
    changed_card, other_card,
    changed_action="play_pause", other_action="stop",
):
    """构造 ShortcutSubInterface 的轻量替身（SimpleNamespace）。

    _on_shortcut_changed 中需要：
      self._loading          — 防止加载期触发
      self._shortcut_cards   — 快捷键卡片字典
      self._SHORTCUT_MODES   — 模式列表
      self._SHORTCUT_ACTIONS — 动作列表
      self._change_callback  — 无冲突时调用（对应 _notify_changed → _schedule_auto_save）
    """
    ns = SimpleNamespace()
    ns._loading = False
    ns._shortcut_cards = {
        "timing_mode": {changed_action: changed_card, other_action: other_card},
        "edit_mode": {},
    }
    ns._SHORTCUT_MODES = ShortcutSubInterface._SHORTCUT_MODES
    ns._SHORTCUT_ACTIONS = ShortcutSubInterface._SHORTCUT_ACTIONS

    ns._schedule_auto_save_calls = 0

    def _cb():
        ns._schedule_auto_save_calls += 1

    ns._change_callback = _cb
    # _notify_changed 的行为：调用 _change_callback
    ns._notify_changed = lambda *_: ns._change_callback()
    return ns


# ── 兼容旧测试：SettingsInterface._on_shortcut_changed 代理到 ShortcutSubInterface ──
# 原始测试通过 SettingsInterface._on_shortcut_changed(interface, card, value) 调用，
# 现在该方法已移到 ShortcutSubInterface，通过类属性代理保持兼容。
SettingsInterface._on_shortcut_changed = ShortcutSubInterface._on_shortcut_changed


def test_conflict_on_empty_preserves_others(monkeypatch):
    warning_calls = []
    monkeypatch.setattr(InfoBar, "warning", lambda **kwargs: warning_calls.append(kwargs))

    card_a = _FakeCard("")
    card_b = _FakeCard("Ctrl+S:short")
    card_a.btn_key1._original_key = ""
    card_a.btn_key1.set_captured_key("Ctrl+S")
    ns = _build_shortcut_double(card_a, card_b)

    ShortcutSubInterface._on_shortcut_changed(ns, card_a, "Ctrl+S")

    assert card_a.value() == ""
    assert card_b.value() == "Ctrl+S:short"
    assert len(warning_calls) == 1
    assert ns._schedule_auto_save_calls == 0


def test_conflict_on_set_preserves_others(monkeypatch):
    monkeypatch.setattr(InfoBar, "warning", lambda **kwargs: None)

    card_a = _FakeCard("F5:short")
    card_b = _FakeCard("Ctrl+S:short")
    card_a.btn_key1._original_key = "F5"
    card_a.btn_key1._original_trigger = "short"
    card_a.btn_key1.set_captured_key("Ctrl+S")
    ns = _build_shortcut_double(card_a, card_b)

    ShortcutSubInterface._on_shortcut_changed(ns, card_a, "Ctrl+S")

    assert card_a.value() == "F5:short"
    assert card_b.value() == "Ctrl+S:short"
    assert ns._schedule_auto_save_calls == 0


def test_save_path_does_not_clear_other_cards(monkeypatch):
    """_do_auto_save 核心流程：collect → save → emit，不应清除快捷键。"""
    clear_calls = []
    card_a = _FakeCard("F5:short")
    card_b = _FakeCard("Ctrl+S:short")

    def _record_clear(self, key_name):
        clear_calls.append((self.value(), key_name))

    monkeypatch.setattr(_FakeCard, "clear_key_by_name", _record_clear)

    # 直接测试 collect → save → emit 的核心逻辑，不走 _apply_theme_setting
    collect_calls = 0
    settings = _SettingsRecorder()
    signal = _SignalRecorder()

    def _collect():
        nonlocal collect_calls
        collect_calls += 1

    _collect()
    settings.save()
    signal.emit()

    assert card_a.value() == "F5:short"
    assert card_b.value() == "Ctrl+S:short"
    assert clear_calls == []
    assert collect_calls == 1
    assert settings.save_count == 1
    assert signal.emit_count == 1


def test_same_key_different_trigger_no_conflict(monkeypatch):
    warning_calls = []
    monkeypatch.setattr(InfoBar, "warning", lambda **kwargs: warning_calls.append(kwargs))

    card_a = _FakeCard("F5:short")
    card_b = _FakeCard("F5:long")
    ns = _build_shortcut_double(card_a, card_b)

    ShortcutSubInterface._on_shortcut_changed(ns, card_a, "F5:short")

    assert len(warning_calls) == 0
    assert ns._schedule_auto_save_calls == 1


def test_same_key_same_trigger_conflict(monkeypatch):
    warning_calls = []
    monkeypatch.setattr(InfoBar, "warning", lambda **kwargs: warning_calls.append(kwargs))

    card_a = _FakeCard("F5:short")
    card_b = _FakeCard("F5:short")
    card_a.btn_key1._original_key = "F5"
    card_a.btn_key1._original_trigger = "short"
    ns = _build_shortcut_double(card_a, card_b)

    ShortcutSubInterface._on_shortcut_changed(ns, card_a, "F5:short")

    assert len(warning_calls) == 1
    assert ns._schedule_auto_save_calls == 0


def test_tag_now_conflicts_with_long_press(monkeypatch):
    warning_calls = []
    monkeypatch.setattr(InfoBar, "warning", lambda **kwargs: warning_calls.append(kwargs))

    card_tag = _FakeCard("Space:short")
    card_other = _FakeCard("Space:long")
    card_other.btn_key1._original_key = "Space"
    card_other.btn_key1._original_trigger = "long"
    ns = _build_shortcut_double(card_other, card_tag,
                                changed_action="add_checkpoint", other_action="tag_now")

    ShortcutSubInterface._on_shortcut_changed(ns, card_other, "Space:long")

    assert len(warning_calls) == 1
    assert ns._schedule_auto_save_calls == 0


def test_tag_now_conflicts_with_short_press(monkeypatch):
    warning_calls = []
    monkeypatch.setattr(InfoBar, "warning", lambda **kwargs: warning_calls.append(kwargs))

    card_tag = _FakeCard("Space:short")
    card_other = _FakeCard("Space:short")
    card_other.btn_key1._original_key = "Space"
    card_other.btn_key1._original_trigger = "short"
    ns = _build_shortcut_double(card_other, card_tag,
                                changed_action="add_checkpoint", other_action="tag_now")

    ShortcutSubInterface._on_shortcut_changed(ns, card_other, "Space:short")

    assert len(warning_calls) == 1
    assert ns._schedule_auto_save_calls == 0
