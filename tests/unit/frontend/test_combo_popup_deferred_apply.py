"""qfluentwidgets 弹层内同步重活的延迟应用回归测试。

背景（同 ai_timing_dialog 模型下拉闪退修复）：qfluentwidgets 的
ComboBox.currentIndexChanged 在弹层（RoundMenu）自身的激活/关闭调用栈内
同步发出，槽函数若在同一栈里落盘 / 重建控件，会与弹层销毁竞争，
快速连续点击闪退。这里覆盖四处同类修复：演唱者分组下拉、语言下拉、
词典更新间隔、更新器代理模式下拉——全部应满足：

1. 变更信号触发时不立即执行重活（pending 置位）；
2. 连续快速切换被防抖合并，事件循环派发后只应用最后一次；
3. 便宜的状态更新（如 _group_filter）仍即时生效。
"""

import pytest
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import ExpandLayout

from strange_uta_game.frontend.localization import localization
from strange_uta_game.frontend.settings.app_settings import AppSettings
from strange_uta_game.frontend.settings.sub_interfaces.about import AboutSubInterface
from strange_uta_game.frontend.settings.sub_interfaces.dictionary import (
    DictionarySubInterface,
)
from strange_uta_game.frontend.singer.singer_interface import SingerManagerInterface
from strange_uta_game.updater.settings import UpdaterSettings
from strange_uta_game.updater.ui import proxy_card


def _drain(qapp, rounds: int = 20) -> None:
    """派发事件循环直到 0ms singleShot 全部执行。"""
    for _ in range(rounds):
        qapp.processEvents()


class _FakeSettings:
    """记录 set/save 调用的最小 AppSettings 替身。"""

    def __init__(self):
        self.values: dict = {}
        self.saved = 0

    def get(self, path, default=None):
        return self.values.get(path, default)

    def set(self, path, value):
        self.values[path] = value

    def save(self):
        self.saved += 1


class TestSingerGroupFilterDeferredRefresh:
    """分组切换触发的 _refresh_list 延迟到事件循环：不在弹层销毁栈内
    clear() 下拉自身，且连续切换只刷新一次。"""

    def test_rapid_group_switches_refresh_once(self, qapp):
        iface = SingerManagerInterface()
        calls = []
        iface._refresh_list = lambda: calls.append(1)
        iface.combo_group_filter.addItem("G1", userData="g1")

        iface.combo_group_filter.setCurrentIndex(1)
        assert iface._list_refresh_pending is True
        # 便宜状态即时更新，重活（列表重建）尚未发生
        assert iface._group_filter == "g1"
        assert calls == []

        iface.combo_group_filter.setCurrentIndex(0)
        assert iface._list_refresh_pending is True
        assert iface._group_filter == ""
        assert calls == []

        _drain(qapp)
        assert iface._list_refresh_pending is False
        assert len(calls) == 1


class TestLanguageComboDeferredApply:
    """语言下拉切换的落盘 + apply_language + InfoBar 延迟到事件循环，
    连续切换只应用最后一次。"""

    def test_rapid_language_switches_apply_last(self, qapp, monkeypatch):
        iface = AboutSubInterface()
        fake = _FakeSettings()
        iface._settings_ref = fake
        # 信号连接在 connect_signals（由外层 SettingsInterface 调用）
        iface.connect_signals()
        applied = []
        monkeypatch.setattr(
            localization, "apply_language", lambda code: applied.append(code)
        )

        iface._language_card.setCurrentIndex(2)  # ja_JP
        assert iface._language_apply_pending is True
        iface._language_card.setCurrentIndex(3)  # en_US
        assert iface._language_apply_pending is True
        assert applied == []
        assert fake.saved == 0

        _drain(qapp)
        assert iface._language_apply_pending is False
        assert applied == ["en_US"]
        assert fake.values["ui.language"] == "en_US"
        assert fake.saved == 1


class TestIntervalComboDeferredApply:
    """词典更新间隔的单位下拉 / 数值输入变更延迟落盘，
    连续调整只 save 一次。"""

    def test_rapid_unit_changes_save_once(self, qapp):
        iface = DictionarySubInterface()
        fake = _FakeSettings()
        iface._settings_ref = fake
        iface.connect_signals()

        iface._interval_combo.setCurrentIndex(1)  # 天
        assert iface._interval_apply_pending is True
        iface._interval_combo.setCurrentIndex(2)  # 小时
        assert iface._interval_apply_pending is True
        assert fake.saved == 0

        _drain(qapp)
        assert iface._interval_apply_pending is False
        assert fake.saved == 1
        assert fake.values["network_dictionary.auto_update.interval_unit"] == "hour"


class TestProxyModeComboDeferredApply:
    """代理模式下拉切换的 load/save 设置 + 状态刷新延迟到事件循环，
    连续切换只落盘最后一次。"""

    def test_rapid_mode_changes_save_last_once(self, qapp, tmp_path, monkeypatch):
        settings = AppSettings(str(tmp_path / "config.json"))

        class _Host(QWidget):
            def __init__(self, s):
                super().__init__()
                self._s = s
                self.scrollWidget = QWidget()
                self.expandLayout = ExpandLayout(self.scrollWidget)

            def get_settings(self):
                return self._s

        host = _Host(settings)
        # 屏蔽状态卡刷新：resolve_proxy 在 system 模式失败时会同步扫本地端口
        monkeypatch.setattr(proxy_card, "_update_status", lambda *a, **k: None)
        saves = []
        orig_save = UpdaterSettings.save

        def _counting_save(self, app=None):
            saves.append(1)
            orig_save(self, app)

        monkeypatch.setattr(UpdaterSettings, "save", _counting_save)

        proxy_card.attach_proxy_group(host)
        combo = host.card_proxy_mode.combo
        # attach 阶段 ensure_persisted 可能已落盘一次默认值，不计入
        saves.clear()

        combo.setCurrentIndex(3)  # manual
        combo.setCurrentIndex(0)  # off
        assert saves == []

        _drain(qapp)
        assert len(saves) == 1
        assert UpdaterSettings.load(settings).proxy_mode == "off"
