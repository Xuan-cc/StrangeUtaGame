"""``strange_uta_game.updater.settings`` 单元测试。

为避免 ``AppSettings`` 默认会去读真实 ``config.json``，本测试用 monkeypatch
覆盖 ``AppSettings.get_config_dir``。
"""

import time
from pathlib import Path

import pytest

from strange_uta_game.frontend.settings.app_settings import AppSettings
from strange_uta_game.updater.settings import (
    DEFAULT_MIN_CHECK_INTERVAL_HOURS,
    DEFAULTS,
    SETTINGS_NAMESPACE,
    UpdaterSettings,
    ensure_persisted,
)


@pytest.fixture
def temp_app_settings(tmp_path, monkeypatch):
    """让 AppSettings 把配置写到 tmp_path，避免污染真实环境。"""
    monkeypatch.setattr(AppSettings, "get_config_dir", staticmethod(lambda: tmp_path))
    yield AppSettings()


class TestUpdaterSettingsLoadSave:
    def test_defaults(self, temp_app_settings):
        s = UpdaterSettings.load(temp_app_settings)
        assert s.enabled is True
        assert s.check_on_startup is True
        assert s.min_check_interval_hours == DEFAULT_MIN_CHECK_INTERVAL_HOURS == 8
        assert s.source_order == DEFAULTS["source_order"]
        assert s.proxy_mode == "system"
        assert s.proxy_manual_url == ""
        assert s.skipped_version == ""
        assert s.last_check_at == 0

    def test_save_then_load(self, temp_app_settings):
        s = UpdaterSettings.load(temp_app_settings)
        s.enabled = False
        s.check_on_startup = False
        s.min_check_interval_hours = 24
        s.source_order = ["fastgit", "github", "ghproxy"]
        s.proxy_mode = "manual"
        s.proxy_manual_url = "http://127.0.0.1:7890"
        s.skipped_version = "0.3.3"
        s.last_check_at = 1700000000
        s.save(temp_app_settings)

        # 重新加载（reload 确保读到磁盘最新）
        temp_app_settings.reload()
        s2 = UpdaterSettings.load(temp_app_settings)
        assert s2.enabled is False
        assert s2.check_on_startup is False
        assert s2.min_check_interval_hours == 24
        assert s2.source_order == ["fastgit", "github", "ghproxy"]
        assert s2.proxy_mode == "manual"
        assert s2.proxy_manual_url == "http://127.0.0.1:7890"
        assert s2.skipped_version == "0.3.3"
        assert s2.last_check_at == 1700000000

    def test_namespace_isolated(self, temp_app_settings):
        """updater 配置不应影响其它顶层节点。"""
        # 设置其它节点
        temp_app_settings.set("ui.theme", "dark")
        temp_app_settings.save()

        # updater 改写
        s = UpdaterSettings.load(temp_app_settings)
        s.enabled = False
        s.save(temp_app_settings)

        temp_app_settings.reload()
        assert temp_app_settings.get("ui.theme") == "dark"
        assert temp_app_settings.get(f"{SETTINGS_NAMESPACE}.enabled") is False

    def test_missing_section_uses_defaults(self, temp_app_settings, monkeypatch):
        """如果 ``config.json`` 中没有 updater 节点，load 仍应回退到内置默认值。"""
        # 模拟"用户 config 中缺失 updater 节点"：直接清掉
        if SETTINGS_NAMESPACE in temp_app_settings._settings:
            del temp_app_settings._settings[SETTINGS_NAMESPACE]
        s = UpdaterSettings.load(temp_app_settings)
        assert s.enabled is True
        assert s.min_check_interval_hours == 8
        assert s.source_order == ["github", "ghproxy", "fastgit"]


class TestUpdaterSettingsToDict:
    def test_to_dict_keys(self):
        s = UpdaterSettings()
        d = s.to_dict()
        assert set(d.keys()) == {
            "enabled",
            "check_on_startup",
            "min_check_interval_hours",
            "source_order",
            "proxy_mode",
            "proxy_manual_url",
            "skipped_version",
            "last_seen_version",
            "last_check_at",
        }


class TestCheckCooldown:
    def test_never_checked_returns_false(self):
        s = UpdaterSettings()
        s.last_check_at = 0
        assert s.is_within_check_cooldown() is False

    def test_within_window(self):
        s = UpdaterSettings()
        s.min_check_interval_hours = 8
        now = time.time()
        s.last_check_at = int(now - 3600)  # 1 小时前
        assert s.is_within_check_cooldown(now=now) is True

    def test_outside_window(self):
        s = UpdaterSettings()
        s.min_check_interval_hours = 8
        now = time.time()
        s.last_check_at = int(now - 9 * 3600)  # 9 小时前
        assert s.is_within_check_cooldown(now=now) is False

    def test_zero_interval_disables_cooldown(self):
        s = UpdaterSettings()
        s.min_check_interval_hours = 0
        now = time.time()
        s.last_check_at = int(now - 60)
        assert s.is_within_check_cooldown(now=now) is False


class TestEnsurePersisted:
    def test_writes_when_missing(self, temp_app_settings):
        # 模拟"用户 config 中没有 updater 节点"：清掉内存状态并落盘
        if SETTINGS_NAMESPACE in temp_app_settings._settings:
            del temp_app_settings._settings[SETTINGS_NAMESPACE]
            temp_app_settings.save()
            temp_app_settings.reload()

        # 此时 packaged defaults 加载后可能又有 updater 节点 —— 再清一次
        # （AppSettings._load_settings 会把 packaged + user 做 deep merge，
        # 而我们的 packaged config.json 内置了 updater，所以 reload 后会再出现）
        # 为了在测试里准确触发 ensure_persisted 的"主动写"分支，直接 patch
        # AppSettings.get 让其返回 None。
        original_get = temp_app_settings.get
        called = {"save": 0}
        temp_app_settings.get = lambda path, default=None: (  # type: ignore[assignment]
            None if path == SETTINGS_NAMESPACE else original_get(path, default)
        )
        original_save = temp_app_settings.save
        def counting_save():
            called["save"] += 1
            original_save()
        temp_app_settings.save = counting_save  # type: ignore[assignment]

        try:
            s = ensure_persisted(temp_app_settings)
        finally:
            temp_app_settings.get = original_get  # type: ignore[assignment]
            temp_app_settings.save = original_save  # type: ignore[assignment]

        assert s.enabled is True
        assert s.min_check_interval_hours == 8
        # ensure_persisted 必须主动写盘一次
        assert called["save"] == 1

    def test_idempotent(self, temp_app_settings):
        s1 = ensure_persisted(temp_app_settings)
        # 修改 last_check_at 模拟正常使用，再调一次 ensure_persisted
        s1.last_check_at = 9999
        s1.save(temp_app_settings)
        temp_app_settings.reload()
        s2 = ensure_persisted(temp_app_settings)
        # ensure_persisted 不应该覆盖已有数据
        assert s2.last_check_at == 9999
