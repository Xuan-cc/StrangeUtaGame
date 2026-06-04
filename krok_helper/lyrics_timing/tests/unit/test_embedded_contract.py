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
        monkeypatch.setenv("SUG_CACHE_DIR", str(tmp_path))
        from strange_uta_game.frontend import project_store as ps

        assert ps._untitled_temp_path().parent == tmp_path

    def test_no_env_is_standalone(self, monkeypatch):
        monkeypatch.delenv("SUG_CACHE_DIR", raising=False)
        from strange_uta_game.frontend import project_store as ps

        assert ps._get_cache_dir().name == ".cache"


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

    def test_dead_buttons_no_crash_in_embedded(self, qapp):
        about = self._make_about(qapp)
        about.load_settings(
            SimpleNamespace(_provider=object(), _config_path=None, get=lambda k, d=None: d)
        )
        # _config_path is None -> 必须早返回，不能 None.parent 崩
        about._open_config_dir()
        about._change_config_dir()


class TestStandaloneNoRegression:
    def test_file_mode_when_no_provider(self, tmp_path):
        s = AppSettings(config_path=str(tmp_path / "config.json"))
        assert s._provider is None
        assert s._config_path is not None
