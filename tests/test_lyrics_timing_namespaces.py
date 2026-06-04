import json

import pytest

from krok_helper.settings import (
    AppSettings as HostSettings,
    load_app_settings,
    migrate_strange_uta_game_settings,
    save_app_settings,
)


def test_load_app_settings_preserves_lyrics_timing_list_namespaces(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    settings = HostSettings(
        lyrics_timing_dictionary=[{"word": "猫", "reading": "ねこ"}],
        lyrics_timing_singers=[{"id": "s1", "name": "Vocal"}],
        lyrics_timing_network_dictionary={"custom": {"entries": [{"word": "青"}]}},
    )

    save_app_settings(settings)
    loaded = load_app_settings()

    assert loaded.lyrics_timing_dictionary == [{"word": "猫", "reading": "ねこ"}]
    assert loaded.lyrics_timing_singers == [{"id": "s1", "name": "Vocal"}]
    assert loaded.lyrics_timing_network_dictionary == {"custom": {"entries": [{"word": "青"}]}}


def test_migrate_strange_uta_game_settings_imports_list_namespaces(tmp_path):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "config.json").write_text(
        json.dumps({"audio": {"default_volume": 66}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (legacy_dir / "dictionary.json").write_text(
        json.dumps([{"word": "猫", "reading": "ねこ"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (legacy_dir / "singers.json").write_text(
        json.dumps([{"id": "s1", "name": "Vocal"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (legacy_dir / "network_dictionary.json").write_text(
        json.dumps({"custom": {"entries": [{"word": "青"}]}}, ensure_ascii=False),
        encoding="utf-8",
    )

    settings = HostSettings()
    assert migrate_strange_uta_game_settings(settings, legacy_dir=legacy_dir) is True

    assert settings.lyrics_timing_dictionary == [{"word": "猫", "reading": "ねこ"}]
    assert settings.lyrics_timing_singers == [{"id": "s1", "name": "Vocal"}]
    assert settings.lyrics_timing_network_dictionary == {"custom": {"entries": [{"word": "青"}]}}


def test_strange_uta_game_provider_extra_namespaces_round_trip():
    import krok_helper.lyrics_timing  # noqa: F401 - installs bundled src path
    from strange_uta_game.frontend.settings.app_settings import AppSettings

    class Provider:
        def __init__(self):
            self.config = {}
            self.extras = {"dictionary": [], "singers": [], "network": {}}

        def load(self):
            return dict(self.config)

        def save(self, data):
            self.config = dict(data)

        def load_extra(self, key, default):
            return self.extras.get(key, default)

        def save_extra(self, key, data):
            self.extras[key] = data

    provider = Provider()
    AppSettings.set_default_provider(provider)
    try:
        settings = AppSettings()
        settings.register_dictionary_word("猫", "ねこ")
        assert AppSettings().load_dictionary()[0]["reading"] == "ねこ"
        assert provider.extras["dictionary"][0]["word"] == "猫"

        singers = [{"id": "s1", "name": "Vocal", "color": "#ff5a6f"}]
        settings.save_singer_presets(singers)
        assert AppSettings().load_singer_presets() == singers
        assert provider.extras["singers"] == singers

        network_doc = {
            "enabled": True,
            "sources": [
                {
                    "id": "custom",
                    "name": "Custom",
                    "url": "https://example.invalid/dict.json",
                    "enabled": True,
                    "entries": [{"word": "青", "reading": "あお"}],
                    "last_fetched": "2026-06-04T00:00:00",
                }
            ],
            "source_order": ["custom"],
        }
        pytest.importorskip("numpy")
        settings.save_network_dictionary(network_doc)
        assert provider.extras["network"]["custom"]["entries"][0]["word"] == "青"
        assert any(source.get("id") == "custom" for source in AppSettings().load_network_dictionary()["sources"])
    finally:
        AppSettings.set_default_provider(None)


def test_strange_uta_game_provider_partial_save_preserves_newer_shortcuts():
    import krok_helper.lyrics_timing  # noqa: F401 - installs bundled src path
    from strange_uta_game.frontend.settings.app_settings import AppSettings

    class Provider:
        def __init__(self):
            self.config = {}

        def load(self):
            return json.loads(json.dumps(self.config))

        def save(self, data):
            self.config = json.loads(json.dumps(data))

        def save_partial(self, changes):
            for path, value in changes.items():
                self._set_nested(path, value)

        def _set_nested(self, path, value):
            cursor = self.config
            keys = path.split(".")
            for key in keys[:-1]:
                child = cursor.get(key)
                if not isinstance(child, dict):
                    child = {}
                    cursor[key] = child
                cursor = child
            cursor[keys[-1]] = value

        def load_extra(self, key, default):
            return default

        def save_extra(self, key, data):
            _ = key, data

    provider = Provider()
    AppSettings.set_default_provider(provider)
    try:
        stale_settings = AppSettings()

        fresh_settings = AppSettings()
        fresh_settings.set("shortcuts.timing_mode.tag_now", "SPACE:short")
        fresh_settings.save()

        stale_settings.set("export.last_export_dir", "D:/lyrics")
        stale_settings.save()

        reloaded = AppSettings()
        assert reloaded.get("shortcuts.timing_mode.tag_now") == "SPACE:short"
        assert reloaded.get("export.last_export_dir") == "D:/lyrics"
    finally:
        AppSettings.set_default_provider(None)


def test_krok_helper_settings_bridge_partial_save_merges_lyrics_timing(monkeypatch, tmp_path):
    from krok_helper.gui_qt import KrokHelperSettingsBridge

    monkeypatch.setenv("APPDATA", str(tmp_path))
    saved = HostSettings(
        lyrics_timing={
            "shortcuts": {"timing_mode": {"tag_now": "SPACE:short"}},
        }
    )
    save_app_settings(saved)

    in_memory = HostSettings(lyrics_timing={})
    bridge = KrokHelperSettingsBridge(in_memory, lambda: save_app_settings(in_memory))

    bridge.save_partial({"export.last_export_dir": "D:/lyrics"})

    loaded = load_app_settings()
    assert loaded.lyrics_timing["shortcuts"]["timing_mode"]["tag_now"] == "SPACE:short"
    assert loaded.lyrics_timing["export"]["last_export_dir"] == "D:/lyrics"
    assert in_memory.lyrics_timing == loaded.lyrics_timing
