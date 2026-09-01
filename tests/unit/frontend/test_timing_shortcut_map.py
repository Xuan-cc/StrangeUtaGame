"""快捷键映射构建回归测试。

锁定 1.6.1 后修复的编辑模式退格键回归：内嵌 config.json 的
``shortcuts.edit_mode`` 段漏写 ``delete_timestamp`` 等打轴专属键时，
该动作回退到单 schema 时代的打轴默认 ``"Backspace:short"``，在
``_collect_map`` 的遍历中后写覆盖了 ``remove_checkpoint`` 的
BACKSPACE 绑定 —— 编辑模式退格实际执行「删除时间戳」而非「减节奏点」。
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt

from strange_uta_game.frontend.editor.timing_interface import EditorInterface
from strange_uta_game.frontend.settings.sub_interfaces.shortcut import (
    ShortcutSubInterface,
)


class _DictSettings(SimpleNamespace):
    """AppSettings 替身：仅支持 get/set 的字典视图。"""

    def __init__(self, data: dict):
        super().__init__()
        self._data = data

    def get(self, path: str, default=None):
        value = self._data
        for key in path.split("."):
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, path: str, value):
        target = self._data
        keys = path.split(".")
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        target[keys[-1]] = value


def _editor_action_names() -> list:
    """从 timing_interface 源码提取 action_names（保持与实现同步）。"""
    import strange_uta_game.frontend.editor.timing_interface as ti

    src = Path(ti.__file__)
    match = re.search(r"action_names = \[(.*?)\]", src.read_text(encoding="utf-8"), re.S)
    return re.findall(r'"([a-z0-9_]+)"', match.group(1))


def _fallback_defaults() -> dict:
    """重建 _apply_settings_inner 中的末级兜底 defaults（键集即可）。"""
    import strange_uta_game.frontend.editor.timing_interface as ti

    src = Path(ti.__file__).read_text(encoding="utf-8")
    match = re.search(r"defaults = \{(.*?)\n        \}", src, re.S)
    return dict(re.findall(r'"([a-z0-9_]+)":\s*"([^"]*)"', match.group(1)))


def _shipped_shortcuts() -> dict:
    """读取内嵌（打包随附）config.json 的 shortcuts 段。"""
    import strange_uta_game.frontend.editor.timing_interface as ti

    config = Path(ti.__file__).resolve().parents[2] / "config" / "config.json"
    return json.loads(config.read_text(encoding="utf-8"))["shortcuts"]


def _build(settings, mode_key):
    return EditorInterface._collect_shortcut_map(
        settings, mode_key, _editor_action_names(), _fallback_defaults()
    )


def test_shipped_config_edit_mode_backspace_removes_checkpoint():
    """内嵌默认配置：编辑模式 BACKSPACE 必须绑定减节奏点，而非删除时间戳。"""
    short, _, _, _ = _build(_DictSettings(_shipped_shortcuts()), "edit_mode")
    assert short.get("BACKSPACE") == "remove_checkpoint"


def test_shipped_config_timing_mode_backspace_deletes_timestamp():
    """打轴模式 BACKSPACE 保持原有「删除时间戳并回滚」绑定。"""
    short, _, _, _ = _build(_DictSettings(_shipped_shortcuts()), "timing_mode")
    assert short.get("BACKSPACE") == "delete_timestamp"


def test_edit_mode_survives_config_gap_on_delete_timestamp():
    """复现 1.6.1 用户场景：edit_mode 段缺 delete_timestamp 等打轴专属键。

    （用户 config.json 由旧版本全量保存、或内嵌配置漏键时，合并结果中
    这些键不存在。）回退默认必须按模式取值，且回退不得覆盖显式绑定，
    BACKSPACE 仍应指向减节奏点。
    """
    shipped = json.loads(json.dumps(_shipped_shortcuts()))  # deep copy
    for key in (
        "delete_timestamp",
        "tag_now",
        "tag_now_extra",
        "seek_back",
        "seek_forward",
        "tag_and_delete_next",
    ):
        shipped["edit_mode"].pop(key, None)

    short, _, _, _ = _build(_DictSettings(shipped), "edit_mode")
    assert short.get("BACKSPACE") == "remove_checkpoint"
    # 打轴专属动作在编辑模式的回退默认是空绑定
    assert "D" not in short  # tag_now 回退为空
    assert "Z" not in short  # seek_back 回退为空


def test_fallback_never_overwrites_explicit_binding():
    """回退默认与显式绑定冲突时，显式绑定获胜（两个方向都验证）。"""
    # 显式 remove_checkpoint=BACKSPACE，delete_timestamp 缺失（回退为空）
    data = {"shortcuts": {"edit_mode": {"remove_checkpoint": "BACKSPACE:short"}}}
    short, _, _, _ = _build(_DictSettings(data), "edit_mode")
    assert short.get("BACKSPACE") == "remove_checkpoint"

    # 显式 delete_timestamp=BACKSPACE，remove_checkpoint 缺失
    # （回退默认也是 BACKSPACE）——显式值生效
    data = {"shortcuts": {"edit_mode": {"delete_timestamp": "BACKSPACE:short"}}}
    short, _, _, _ = _build(_DictSettings(data), "edit_mode")
    assert short.get("BACKSPACE") == "delete_timestamp"


def test_flat_legacy_schema_still_honored():
    """旧扁平 shortcuts.* 显式键位仍被读取（schema 兼容行为不变）。"""
    data = {"shortcuts": {"remove_checkpoint": "3:short"}}
    short, _, actions, migrated = _build(_DictSettings(data), "edit_mode")
    assert short.get("3") == "remove_checkpoint"
    assert actions["remove_checkpoint"] == "3:short"


def test_old_format_trigger_gets_normalized_and_reported():
    """无 :short/:long 后缀的旧格式值被标准化并列入迁移写回。"""
    data = {"shortcuts": {"edit_mode": {"remove_checkpoint": "Backspace"}}}
    _, _, _, migrated = _build(_DictSettings(data), "edit_mode")
    assert ("shortcuts.edit_mode.remove_checkpoint", "Backspace:short") in migrated


def test_shipped_config_covers_every_editable_action():
    """内嵌 config.json 两个模式段必须覆盖设置页全部动作，防止再漏键。"""
    shipped = _shipped_shortcuts()
    for mode in ("timing_mode", "edit_mode"):
        section = shipped.get(mode, {})
        missing = [
            row[0]
            for row in ShortcutSubInterface._SHORTCUT_ACTIONS
            if row[0] not in section
        ]
        assert not missing, f"内嵌 config.json {mode} 段缺键: {missing}"


def test_shipped_config_matches_ui_table_defaults():
    """内嵌配置与设置页 _SHORTCUT_ACTIONS 表的默认键位逐项一致。

    undo/redo/save 等固定功能（readonly）不在编辑器 action_names 中，
    编辑器在 keyPressEvent 里硬编码处理，不参与映射构建，跳过。
    """
    editor_actions = set(_editor_action_names())
    for mode, col in (("timing_mode", 4), ("edit_mode", 5)):
        _, _, actions, _ = _build(_DictSettings(_shipped_shortcuts()), mode)
        for row in ShortcutSubInterface._SHORTCUT_ACTIONS:
            action, default = row[0], row[col]
            if action not in editor_actions:
                continue
            assert actions.get(action) == default, (
                f"{mode}.{action}: config={actions.get(action)!r} UI 表={default!r}"
            )


class _FallbackHarness:
    """Call the pre-settings fallback without constructing a QWidget."""

    _qt_key_to_name = EditorInterface._qt_key_to_name
    _mode_shortcut_defaults = staticmethod(EditorInterface._mode_shortcut_defaults)


def test_preload_fallback_uses_current_mode_defaults():
    """预加载兜底必须与当前双模式默认键位一致，不得保留旧版 D=播放。"""
    fallback = _FallbackHarness()

    assert EditorInterface._default_key_action(
        fallback, Qt.Key.Key_A, playing=False
    ) == "play_pause"
    assert EditorInterface._default_key_action(
        fallback, Qt.Key.Key_D, playing=False
    ) is None
    assert EditorInterface._default_key_action(
        fallback, Qt.Key.Key_Space, playing=False
    ) == "add_checkpoint"

    assert EditorInterface._default_key_action(
        fallback, Qt.Key.Key_A, playing=True
    ) == "play_pause"
    assert EditorInterface._default_key_action(
        fallback, Qt.Key.Key_D, playing=True
    ) == "tag_now"
    assert EditorInterface._default_key_action(
        fallback, Qt.Key.Key_F, playing=True
    ) == "tag_now"
    assert EditorInterface._default_key_action(
        fallback, Qt.Key.Key_Space, playing=True
    ) == "tag_now_extra"


def test_restart_playback_shortcut_stops_before_playing():
    """「从头播放」复用停止和播放路径，且顺序不能颠倒。"""
    calls = []
    editor = SimpleNamespace(
        _on_stop=lambda: calls.append("stop"),
        _on_play=lambda: calls.append("play"),
    )

    EditorInterface._execute_action(editor, "restart_playback", 0)

    assert calls == ["stop", "play"]


def test_restart_playback_default_is_shift_s_in_both_modes():
    """「从头播放」在播放与编辑模式中都默认为 Shift+S。"""
    shipped = _shipped_shortcuts()
    for mode in ("timing_mode", "edit_mode"):
        assert shipped[mode]["restart_playback"] == "SHIFT+S:short"

        # 旧用户配置不含新动作时，也应从设置页默认值回退补全。
        old_config = json.loads(json.dumps(shipped))
        old_config[mode].pop("restart_playback")
        short, _, actions, _ = _build(_DictSettings(old_config), mode)
        assert short["SHIFT+S"] == "restart_playback"
        assert actions["restart_playback"] == "SHIFT+S:short"
