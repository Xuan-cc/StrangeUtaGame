"""AppSettings 进程级共享实例语义（切换项目丢记忆设置的回归测试）。

背景：``ai_timing.*``（AI 打轴环境配置）、``auto_check.delete_ruby_types``
（自动删除注音类型）等记忆设置由短寿命 ``AppSettings()`` 实例写盘，而
设置页的长寿命实例随后整字典 ``save()`` 会把旧内存快照写回、回滚这些键
（典型触发：切换项目时 ``_apply_project_extras`` 重置 nicokara_tags）。
共享实例后所有写入者落在同一份内存 + 磁盘上，回滚源消失。
"""

import json

from strange_uta_game.frontend.settings.app_settings import AppSettings


class _MemProvider:
    """最小 SettingsProvider 实现，数据存内存并计数 save 次数。"""

    def __init__(self):
        self.main = {}
        self.save_count = 0

    def load(self):
        return dict(self.main)

    def save(self, data):
        self.save_count += 1
        self.main = dict(data)

    def load_extra(self, key, default):
        return default

    def save_extra(self, key, data):
        pass


class TestSharedInstance:
    def test_same_config_path_returns_same_instance(self, tmp_path):
        cfg = str(tmp_path / "config.json")
        assert AppSettings(config_path=cfg) is AppSettings(config_path=cfg)

    def test_default_dir_instances_shared(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            AppSettings, "get_config_dir", staticmethod(lambda: tmp_path)
        )
        assert AppSettings() is AppSettings()

    def test_provider_instances_keyed_by_provider(self):
        p1, p2 = _MemProvider(), _MemProvider()
        assert AppSettings(provider=p1) is AppSettings(provider=p1)
        assert AppSettings(provider=p1) is not AppSettings(provider=p2)


class TestProjectSwitchRegression:
    def test_project_switch_does_not_rollback_dialog_writes(self, tmp_path):
        """用户 repro：项目 A 中弹窗写入记忆设置 → 切到项目 B 不丢失。"""
        cfg = tmp_path / "config.json"
        shared = AppSettings(config_path=str(cfg))
        shared.set("nicokara_tags", {"title": "A"})
        shared.save()

        # 模拟 timing_interface._save_ai_timing_settings：新建实例写入
        dialog = AppSettings(config_path=str(cfg))
        dialog.set("ai_timing.provider", "mms_fa")
        dialog.save()

        # 模拟 file_loader._apply_project_extras：共享实例 set + 整字典 save
        shared.set("nicokara_tags", {"title": "B"})
        shared.save()

        assert shared.get("ai_timing.provider") == "mms_fa"
        disk = json.loads(cfg.read_text(encoding="utf-8"))
        assert disk["ai_timing"]["provider"] == "mms_fa"
        assert disk["nicokara_tags"]["title"] == "B"

    def test_fulltext_delete_ruby_types_survive_settings_ui_save(self, tmp_path):
        """模拟 fulltext 删除注音对话框写入后，设置页自动保存不回滚。"""
        cfg = str(tmp_path / "config.json")
        settings_ui = AppSettings(config_path=cfg)
        settings_ui.set("ui.theme", "dark")
        settings_ui.save()

        dialog = AppSettings(config_path=cfg)
        dialog.set("auto_check.delete_ruby_types", ["hiragana", "symbol"])
        dialog.save()

        # 设置页后续任何一次整字典保存（_do_auto_save）
        settings_ui.set("audio.default_volume", 55)
        settings_ui.save()

        assert settings_ui.get("auto_check.delete_ruby_types") == [
            "hiragana",
            "symbol",
        ]

    def test_provider_write_notifies_host(self):
        """embedded：写入经 provider.save 通知宿主保存。"""
        p = _MemProvider()
        s = AppSettings(provider=p)
        before = p.save_count
        s.set("ai_timing.provider", "mms_fa")
        s.save()
        assert p.save_count > before
        assert p.main["ai_timing"]["provider"] == "mms_fa"


class TestResetSharedInstances:
    def test_flushes_pending_changes_before_drop(self, tmp_path):
        """实例被丢弃前，set 过但未 save 的修改先写盘（交接语义）。"""
        cfg = tmp_path / "config.json"
        s = AppSettings(config_path=str(cfg))
        s.set("export.offset_ms", 123)
        if cfg.exists():
            disk_before = json.loads(cfg.read_text(encoding="utf-8"))
            assert disk_before.get("export", {}).get("offset_ms") != 123

        AppSettings.reset_shared_instances()

        disk = json.loads(cfg.read_text(encoding="utf-8"))
        assert disk["export"]["offset_ms"] == 123
        # 缓存清空后重建实例读到同一磁盘状态
        assert AppSettings(config_path=str(cfg)).get("export.offset_ms") == 123

    def test_rebuilds_from_scratch_after_file_deleted(self, tmp_path):
        """「重置设置」路径：清缓存 + 删文件后，新实例回到内嵌默认值。"""
        cfg = tmp_path / "config.json"
        s = AppSettings(config_path=str(cfg))
        s.set("export.offset_ms", 123)
        s.save()

        AppSettings.reset_shared_instances()
        cfg.unlink()

        s2 = AppSettings(config_path=str(cfg))
        assert s2.get("export.offset_ms", 0) != 123
