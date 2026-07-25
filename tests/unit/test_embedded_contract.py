"""嵌入契约回归测试。

守护 docs/EMBEDDING.md 描述的 SUG↔宿主嵌入契约。改 embedded 代码后跑这个，
确认契约没破、且 standalone 行为没回退。

- 不依赖宿主（用 MockProvider 模拟 SettingsProvider）。
- 需要 QApplication 的用例用 pytest-qt 的 `qapp` fixture。
"""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from strange_uta_game.frontend.settings.app_settings import AppSettings, SettingsProvider


class MockProvider:
    """最小 SettingsProvider 实现，所有数据存内存。"""

    def __init__(self):
        self.main = {}
        self.extra = {}

    def load(self):
        return deepcopy(self.main)

    def save(self, d):
        self.main = deepcopy(d)

    def load_extra(self, key, default):
        return deepcopy(self.extra.get(key, default))

    def save_extra(self, key, data):
        self.extra[key] = deepcopy(data)


@pytest.fixture
def reset_default_provider():
    """确保 set_default_provider 的进程级全局状态不泄漏到其它测试。"""
    yield
    AppSettings.set_default_provider(None)


class TestSettingsProviderContract:
    def test_runtime_checkable(self):
        assert isinstance(MockProvider(), SettingsProvider)

    def test_provider_mode_skips_filesystem(self):
        s = AppSettings(provider=MockProvider())
        assert s._config_path is None
        assert s._dict_path is None
        assert s._singers_path is None
        # 内嵌默认值仍可读
        assert s.get("audio.default_volume") == 80

    def test_main_config_roundtrip(self):
        p = MockProvider()
        s = AppSettings(provider=p)
        s.set("audio.default_volume", 42)
        s.save()
        assert p.main["audio"]["default_volume"] == 42
        # reload 丢弃内存改动、回到 provider 值
        s.set("audio.default_volume", 999)
        s.reload()
        assert s.get("audio.default_volume") == 42

    def test_dictionary_via_provider(self):
        p = MockProvider()
        s = AppSettings(provider=p)
        s.register_dictionary_word("漢字", "かんじ")
        assert any(e.get("word") == "漢字" for e in s.load_dictionary())
        assert any(e.get("word") == "漢字" for e in p.extra.get("dictionary", []))

    def test_empty_provider_receives_packaged_dictionary_and_version(self):
        p = MockProvider()
        s = AppSettings(provider=p)

        assert s.load_dictionary()
        assert p.extra["dictionary"] == s.load_dictionary()
        assert p.main["applied_dictionary_version"] == s.get("dictionary_version")

    def test_current_version_preserves_intentionally_empty_dictionary(self):
        p = MockProvider()
        packaged_version = AppSettings(provider=p).get("dictionary_version")
        p.main = {"applied_dictionary_version": packaged_version}
        p.extra["dictionary"] = []

        s = AppSettings(provider=p)

        assert s.load_dictionary() == []

    def test_provider_upgrade_keeps_custom_entries(self):
        p = MockProvider()
        p.main = {"applied_dictionary_version": 0}
        p.extra["dictionary"] = [
            {"enabled": True, "word": "custom-only", "reading": "custom"}
        ]

        s = AppSettings(provider=p)

        assert any(e.get("word") == "custom-only" for e in s.load_dictionary())
        assert len(s.load_dictionary()) > 1
        assert p.main["applied_dictionary_version"] == s.get("dictionary_version")

    def test_singers_via_provider(self):
        p = MockProvider()
        s = AppSettings(provider=p)
        s.save_singer_presets([{"name": "歌手A"}])
        assert any(x.get("name") == "歌手A" for x in s.load_singer_presets())
        assert any(x.get("name") == "歌手A" for x in p.extra.get("singers", []))

    def test_deepcopy_isolation(self):
        p = MockProvider()
        s = AppSettings(provider=p)
        s.register_dictionary_word("foo", "bar")
        got = s.load_dictionary()
        got.append({"word": "POISON", "reading": "x"})
        assert not any(e.get("word") == "POISON" for e in s.load_dictionary())

    def test_set_default_provider(self, reset_default_provider):
        p = MockProvider()
        AppSettings.set_default_provider(p)
        bare = AppSettings()  # 裸调用应自动走全局 provider
        assert bare._provider is p

    def test_explicit_provider_beats_default(self, reset_default_provider):
        default_p = MockProvider()
        explicit_p = MockProvider()
        AppSettings.set_default_provider(default_p)
        s = AppSettings(provider=explicit_p)
        assert s._provider is explicit_p


class TestCacheRedirectContract:
    def test_env_redirects_all_three(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUG_CACHE_DIR", str(tmp_path))
        from strange_uta_game.frontend import project_store as ps
        from strange_uta_game.backend.infrastructure.audio import tsm_cache, video_converter

        assert ps._get_cache_dir() == tmp_path
        assert tsm_cache._get_cache_dir() == tmp_path
        # video_converter 的提取音频固定在 .cache 的 extracted 子目录下
        assert video_converter._get_cache_dir() == tmp_path / "extracted"

    def test_untitled_temp_is_lazy(self, tmp_path, monkeypatch):
        # 项目临时文件已迁移到备份目录下的隐藏 .temp 子目录（SUG_BACKUP_DIR 可重定向）。
        monkeypatch.setenv("SUG_BACKUP_DIR", str(tmp_path))
        from strange_uta_game.frontend import project_store as ps

        temp_parent = ps._untitled_temp_path().parent
        assert temp_parent == ps._temp_dir()
        assert temp_parent == tmp_path / ".temp"

    def test_no_env_is_standalone(self, monkeypatch):
        import sys

        monkeypatch.delenv("SUG_CACHE_DIR", raising=False)
        from strange_uta_game.frontend import project_store as ps

        cache = ps._get_cache_dir()
        if sys.platform == "darwin":
            # macOS：bundle 只读，缓存落在 ~/Library/Caches/<App>
            assert "Caches" in cache.parts
        else:
            assert cache.name == ".cache"


class TestEmbeddedUIContract:
    """about 页在 embedded 隐藏 standalone-only 入口，standalone 正常显示。"""

    def _make_about(self, qapp):
        from strange_uta_game.frontend.settings.sub_interfaces.about import (
            AboutSubInterface,
        )

        return AboutSubInterface()

    def test_hidden_in_embedded(self, qapp):
        about = self._make_about(qapp)
        embedded_settings = SimpleNamespace(
            _provider=object(), _config_path=None, get=lambda k, d=None: d
        )
        about.load_settings(embedded_settings)
        assert about._path_card.isHidden()
        assert about.tools_group.isHidden()
        # 语言归宿主独占（与主题同理，见 EMBEDDING.md §5）
        assert about.language_group.isHidden()

    def test_visible_in_standalone(self, qapp):
        about = self._make_about(qapp)
        standalone_settings = SimpleNamespace(
            _provider=None,
            _config_path=Path("C:/x/config.json"),
            get=lambda k, d=None: d,
        )
        about.load_settings(standalone_settings)
        assert not about._path_card.isHidden()
        assert not about.tools_group.isHidden()
        # standalone 红线：语言卡必须可见
        assert not about.language_group.isHidden()

    def test_language_change_noop_in_embedded(self, qapp):
        """即使 embedded 下程序化触发了语言变更信号，SUG 也不应改 ui.language
        或调 localization.apply_language —— 否则会污染宿主语言态。"""
        from strange_uta_game.frontend.localization import localization

        about = self._make_about(qapp)
        captured = {"set": [], "save": 0}
        embedded_settings = SimpleNamespace(
            _provider=object(),
            _config_path=None,
            get=lambda k, d=None: d,
            set=lambda k, v: captured["set"].append((k, v)),
            save=lambda: captured.update(save=captured["save"] + 1),
        )
        about.load_settings(embedded_settings)
        before_code = localization.current_code

        # 直接调内部 handler 模拟"宿主或代码以某种方式触发了 index_changed"
        about._on_language_changed(0)

        assert captured["set"] == [], "embedded 下不得写 ui.language"
        assert captured["save"] == 0, "embedded 下不得 save"
        assert localization.current_code == before_code, (
            "embedded 下 SUG 不得改全局 LocalizationManager"
        )

    def test_dead_buttons_no_crash_in_embedded(self, qapp):
        about = self._make_about(qapp)
        about.load_settings(
            SimpleNamespace(_provider=object(), _config_path=None, get=lambda k, d=None: d)
        )
        # _config_path is None -> 必须早返回，不能 None.parent 崩
        about._open_config_dir()
        about._change_config_dir()

    def test_export_to_next_button_only_exists_in_embedded(self, qapp):
        from strange_uta_game.frontend.export.export_interface import ExportInterface

        standalone = ExportInterface(embedded=False)
        embedded = ExportInterface(embedded=True)

        assert standalone.btn_export_to_next is None
        assert standalone._export_button_row.count() == 1
        assert embedded.btn_export_to_next is not None
        assert embedded.btn_export_to_next.text() == "导出到下一步"
        assert embedded._export_button_row.count() == 2
        assert embedded._export_button_row.stretch(0) == 1
        assert embedded._export_button_row.stretch(1) == 1

    def test_export_to_next_button_emits_public_signal(self, qapp):
        from strange_uta_game.frontend.export.export_interface import ExportInterface

        page = ExportInterface(embedded=True)
        emitted = []
        page.export_to_next_requested.connect(lambda: emitted.append(True))

        page.btn_export_to_next.click()

        assert emitted == [True]

    def test_export_to_next_payload_is_isolated_and_complete(self):
        from strange_uta_game.frontend.main_window import MainWindow

        project = SimpleNamespace(metadata=SimpleNamespace(title="曲名"), singers=[])
        tags = {"title": "标签曲名", "custom": ["@Emoji=主唱"]}
        store = SimpleNamespace(
            project=project,
            save_path="D:/songs/song.sug",
            original_media_path="D:/songs/song.webm",
            audio_path="D:/cache/song.mp3",
            get_saveable_media_path=lambda: "D:/songs/song.webm",
        )
        host = SimpleNamespace(
            _store=store,
            settingInterface=SimpleNamespace(
                get_settings=lambda: SimpleNamespace(
                    get=lambda key: tags if key == "nicokara_tags" else None
                )
            ),
        )

        payload = MainWindow.export_to_next_payload(host)

        assert payload["project"] is not project
        assert payload["nicokara_tags"] == tags
        assert payload["nicokara_tags"] is not tags
        assert payload["source_path"] == "D:/songs/song.sug"
        assert payload["media_path"] == "D:/songs/song.webm"
        assert payload["media_kind"] == "video"
        assert payload["audio_path"] == "D:/cache/song.mp3"

    def test_trigger_save_reports_whether_async_save_started(self):
        from strange_uta_game.frontend.main_window import MainWindow

        started = SimpleNamespace(_on_global_save=lambda: True)
        cancelled = SimpleNamespace(_on_global_save=lambda: False)

        assert MainWindow.trigger_save(started) is True
        assert MainWindow.trigger_save(cancelled) is False

    def test_public_save_lifecycle_signals_exist(self):
        from strange_uta_game.frontend.main_window import MainWindow

        assert MainWindow.project_save_finished is not None
        assert MainWindow.project_save_failed is not None


class TestStandaloneNoRegression:
    def test_file_mode_when_no_provider(self, tmp_path):
        s = AppSettings(config_path=str(tmp_path / "config.json"))
        assert s._provider is None
        assert s._config_path is not None


class TestEmbeddedThemeContract:
    """主题反向写入禁令：embedded 下 SUG 不能改全局 qfluentwidgets Theme，
    也不能掀翻 QApplication palette —— 这两个都归宿主独占。

    根因：``SettingsInterface._apply_theme_setting`` 在改 ``theme.mode`` 时
    会触发 ``_sync_app_palette()`` + ``setTheme()``，是 embedded "半亮半暗"
    崩坏画面的源头。修复后该方法在 embedded 下应 noop。
    """

    def _make_settings_interface(self, qapp, provider):
        from strange_uta_game.frontend.settings.settings_interface import SettingsInterface
        si = SettingsInterface(settings_provider=provider)
        return si

    def test_apply_theme_setting_noop_in_embedded(self, qapp):
        """embedded + 任何 ui.theme 值，调 _apply_theme_setting 不应：
        - 改变 qfluentwidgets ``qconfig`` 的 theme
        - 改变 ``QApplication.palette().color(Window)``
        """
        from PyQt6.QtWidgets import QApplication
        from qfluentwidgets import qconfig

        p = MockProvider()
        p.main = {"ui": {"theme": "dark"}}
        si = self._make_settings_interface(qapp, p)

        # 捕获改动前状态
        before_theme = qconfig.theme
        before_window = QApplication.instance().palette().color(
            QApplication.instance().palette().ColorRole.Window
        )

        # 直接调本方法（不依赖 _do_auto_save 时序）
        si._apply_theme_setting()

        # 断言：embedded 路径下 noop
        assert qconfig.theme == before_theme, (
            "embedded SUG 不应修改 qfluentwidgets 全局 Theme（已写入 EMBEDDING.md §5）"
        )
        after_window = QApplication.instance().palette().color(
            QApplication.instance().palette().ColorRole.Window
        )
        assert after_window == before_window, (
            "embedded SUG 不应修改 QApplication palette（会污染宿主）"
        )

    def test_ui_settings_card_theme_hidden_in_embedded(self, qapp):
        """ui_settings 子页面在 embedded 模式应隐藏 ``card_theme``。"""
        from types import SimpleNamespace
        from strange_uta_game.frontend.settings.sub_interfaces.ui_settings import (
            UISubInterface,
        )

        page = UISubInterface()
        embedded_settings = SimpleNamespace(
            _provider=object(),
            get=lambda k, d=None: d,
        )
        page.load_settings(embedded_settings)
        assert page.card_theme.isHidden()

    def test_ui_settings_card_theme_visible_in_standalone(self, qapp):
        """standalone 应继续显示主题卡（红线：standalone 行为不变）。"""
        from types import SimpleNamespace
        from strange_uta_game.frontend.settings.sub_interfaces.ui_settings import (
            UISubInterface,
        )

        page = UISubInterface()
        standalone_settings = SimpleNamespace(
            _provider=None,
            get=lambda k, d=None: d,
        )
        page.load_settings(standalone_settings)
        assert not page.card_theme.isHidden()
