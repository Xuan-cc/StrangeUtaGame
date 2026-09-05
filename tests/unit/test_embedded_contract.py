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

    def test_network_dictionary_auto_update_via_provider(self, monkeypatch):
        from strange_uta_game.backend.infrastructure import network_dictionary

        p = MockProvider()
        p.main = {
            "network_dictionary": {
                "enabled": True,
                "auto_update": {
                    "enabled": True,
                    "interval_value": 1,
                    "interval_unit": "week",
                },
                "last_auto_update_at": 0,
                "source_order": ["local", "custom"],
                "sources": [
                    {
                        "id": "custom",
                        "name": "测试源",
                        "url": "https://example.invalid/dictionary.json",
                        "enabled": True,
                    }
                ],
            }
        }

        def fake_update(doc, *, timeout=8.0, proxies=None):
            # proxies：maybe_auto_update_network_dictionary 现在透传应用代理
            source = next(s for s in doc["sources"] if s["id"] == "custom")
            source["entries"] = [{"word": "漢字", "reading": "かんじ"}]
            source["last_fetched"] = 123
            return (["测试源: 1 条"], [])

        monkeypatch.setattr(network_dictionary, "auto_update_enabled_sources", fake_update)

        ok, failed, ran = AppSettings(provider=p).maybe_auto_update_network_dictionary()

        assert (ok, failed, ran) == (["测试源: 1 条"], [], True)
        assert p.extra["network"]["custom"]["entries"] == [
            {"word": "漢字", "reading": "かんじ"}
        ]
        assert p.main["network_dictionary"]["auto_update"]["enabled"] is True
        assert p.main["network_dictionary"]["last_auto_update_at"] > 0

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
        assert embedded.btn_export_to_next.text() == "进入下一步"
        assert embedded._export_button_row.count() == 2
        assert embedded._export_button_row.stretch(0) == 1
        assert embedded._export_button_row.stretch(1) == 1

    def test_export_to_next_button_emits_public_signal(self, qapp):
        from strange_uta_game.frontend.export.export_interface import ExportInterface
        from strange_uta_game.backend.domain import Project

        page = ExportInterface(embedded=True)
        # 「进入下一步」先过 _on_export_to_next：无过滤器时直接发信号。
        # 无项目会被拦截（提示「无项目」），先挂一个最小项目。
        page.set_project(Project())
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
        # 无 axis_groups（旧式 project 对象）→ 单轴
        assert payload["axis_plan"] == {
            "mode": "single",
            "groups": [],
            "unassigned": [],
        }

    def test_export_to_next_payload_axis_plan_split_mode(self):
        """axis_groups 非空 → mode=split，组结构 / 演唱者快照 / 未入组齐全。"""
        from strange_uta_game.frontend.main_window import MainWindow
        from strange_uta_game.backend.domain import AxisGroup

        project = SimpleNamespace(
            metadata=SimpleNamespace(title="曲名"),
            singers=[
                SimpleNamespace(id="a", name="A", color="#FF0000",
                                color_mode="solid", split_colors=[], enabled=True),
                SimpleNamespace(id="b", name="B", color="#00FF00",
                                color_mode="split", split_colors=["#0000FF"],
                                enabled=True),
                SimpleNamespace(id="c", name="C", color="#0000FF",
                                color_mode="solid", split_colors=[], enabled=False),
            ],
            axis_groups=[
                AxisGroup(name="轴1", singer_ids=["a", "b"]),
                AxisGroup(name="轴2", singer_ids=["b", "ghost"]),  # ghost 引用被过滤
            ],
        )
        store = SimpleNamespace(
            project=project,
            save_path="D:/songs/song.sug",
            original_media_path=None,
            audio_path=None,
            get_saveable_media_path=lambda: None,
        )
        host = SimpleNamespace(
            _store=store,
            settingInterface=SimpleNamespace(
                get_settings=lambda: SimpleNamespace(get=lambda key: None)
            ),
        )

        payload = MainWindow.export_to_next_payload(host)
        plan = payload["axis_plan"]

        assert plan["mode"] == "split"
        assert [g["name"] for g in plan["groups"]] == ["轴1", "轴2"]
        assert plan["groups"][0]["singer_ids"] == ["a", "b"]
        assert plan["groups"][1]["singer_ids"] == ["b"]
        # 主分组归一化：源数据未标记 → 首组为主分组，且只有一个
        assert [g["is_primary"] for g in plan["groups"]] == [True, False]
        # 组内冗余演唱者快照（宿主免反查 project）
        assert plan["groups"][0]["singers"] == [
            {"id": "a", "name": "A", "color": "#FF0000",
             "color_mode": "solid", "split_colors": []},
            {"id": "b", "name": "B", "color": "#00FF00",
             "color_mode": "split", "split_colors": ["#0000FF"]},
        ]
        # 未入组：只有启用的演唱者（禁用的 c 不参与分轴）
        assert plan["unassigned"] == []

        # 未入组示例：把 a 移出所有组
        project.axis_groups = [AxisGroup(name="轴2", singer_ids=["b"])]
        plan2 = MainWindow.export_to_next_payload(host)["axis_plan"]
        assert plan2["unassigned"] == ["a"]

        # 空 = 全部：物化为全部启用歌手（禁用的 c 不含），unassigned 归零
        project.axis_groups = [AxisGroup(name="全轴", singer_ids=[])]
        plan3 = MainWindow.export_to_next_payload(host)["axis_plan"]
        assert plan3["groups"][0]["singer_ids"] == ["a", "b"]
        assert plan3["unassigned"] == []

        # 快照隔离：改 payload 不影响 project
        plan["groups"][0]["singer_ids"].append("POISON")
        assert "POISON" not in project.axis_groups[0].singer_ids

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

    def test_public_host_visibility_lifecycle_exists(self):
        from strange_uta_game.frontend.main_window import MainWindow

        assert callable(MainWindow.on_host_visibility_changed)

    def test_host_visibility_forwarded_to_throttle(self, qapp):
        """on_host_visibility_changed 必须转发给前后台节流器。

        嵌入式下宿主隐藏 SUG 区域时宿主顶层窗口仍可见，编辑器自动判定
        看不到这层——只有这条显式通知能让非音频服务降频（EMBEDDING.md §2）。
        """
        from strange_uta_game.frontend import background_throttle as bt
        from strange_uta_game.frontend.main_window import MainWindow

        throttle = bt.background_throttle()
        assert throttle is not None
        # editorInterface 未就绪（SimpleNamespace）也应先完成转发再早返回
        try:
            MainWindow.on_host_visibility_changed(SimpleNamespace(), False)
            assert throttle.is_visible is False

            MainWindow.on_host_visibility_changed(SimpleNamespace(), False)
            assert throttle.is_visible is False  # 幂等

            MainWindow.on_host_visibility_changed(SimpleNamespace(), True)
            # True 恢复自动判定；无可见窗口时为 False，但不再是强制隐藏
            assert throttle._host_hidden is False
        finally:
            bt.set_visibility_override(None)

    def test_network_auto_update_scheduler_uses_default_provider(
        self, monkeypatch, reset_default_provider
    ):
        import threading

        from strange_uta_game.frontend.main_window import MainWindow

        p = MockProvider()
        AppSettings.set_default_provider(p)
        seen_providers = []

        monkeypatch.setattr(
            AppSettings,
            "maybe_auto_update_network_dictionary",
            lambda settings: seen_providers.append(settings._provider),
        )

        class ImmediateThread:
            def __init__(self, *, target, **_kwargs):
                self._target = target

            def start(self):
                self._target()

        monkeypatch.setattr(threading, "Thread", ImmediateThread)

        MainWindow._schedule_network_dict_auto_update(SimpleNamespace())

        assert seen_providers == [p]


class TestExportToNextAxisFlow:
    """「进入下一步」× 导出字幕分组 → 信号链路。

    分组编辑统一在导出页「导出字幕分组」小窗完成；进入下一步只发信号，
    不再弹窗、不改写分组。
    """

    @staticmethod
    def _make_page(qapp):
        from strange_uta_game.frontend.export.export_interface import ExportInterface
        from strange_uta_game.backend.domain import Project, Singer, Sentence

        project = Project(
            singers=[
                Singer(name="A", color="#FF0000", is_default=True),
                Singer(name="B", color="#00FF00"),
            ]
        )
        a, b = project.singers[0].id, project.singers[1].id
        project.add_sentence(Sentence.from_text("あいう", a))
        project.add_sentence(Sentence.from_text("えお", b))

        page = ExportInterface(embedded=True)
        page.set_project(project)
        page._refresh_axis_group_summary()
        dirty_calls = []
        page._store = SimpleNamespace(mark_dirty=lambda: dirty_calls.append(1))
        return page, project, (a, b), dirty_calls

    def test_export_to_next_emits_and_keeps_groups(self, qapp):
        from strange_uta_game.backend.domain import AxisGroup

        page, project, (a, _b), dirty = self._make_page(qapp)
        project.set_axis_groups([AxisGroup(name="轴1", singer_ids=[a])])
        emitted = []
        page.export_to_next_requested.connect(lambda: emitted.append(True))

        page._on_export_to_next()

        assert emitted == [True]
        # 进入下一步不改写分组（编辑归「修改分组...」小窗管）
        assert [(g.name, g.singer_ids) for g in project.axis_groups] == [
            ("轴1", [a])
        ]
        assert not dirty

    def test_export_to_next_never_opens_dialog(self, qapp, monkeypatch):
        """进入下一步不再自动弹出轴分组对话框（原过滤器触发逻辑已移除）。"""
        from strange_uta_game.frontend.export import axis_group_dialog as dlg_mod

        def _boom(*args, **kwargs):
            raise AssertionError("进入下一步不应构造轴分组对话框")

        monkeypatch.setattr(dlg_mod, "AxisGroupDialog", _boom)
        page, _project, _ids, _dirty = self._make_page(qapp)
        emitted = []
        page.export_to_next_requested.connect(lambda: emitted.append(True))

        page._on_export_to_next()

        assert emitted == [True]

    def test_edit_button_dialog_initial_and_write_back(self, qapp, monkeypatch):
        """「修改分组...」：已有分组作为初始态传入对话框，确认后写回。"""
        from strange_uta_game.backend.domain import AxisGroup
        from strange_uta_game.frontend.export import axis_group_dialog as dlg_mod

        page, project, (a, b), dirty = self._make_page(qapp)
        saved = [AxisGroup(name="轴1", singer_ids=[a])]
        project.set_axis_groups(saved)
        page._refresh_axis_group_summary()

        captured = {}

        class FakeDialog:
            def __init__(self, singers, initial_groups=None, parent=None):
                captured["singers"] = singers
                captured["initial"] = initial_groups

            def exec(self):
                return 1  # Accepted

            @staticmethod
            def get_axis_groups():
                return [
                    AxisGroup(name="轴1", singer_ids=[a]),
                    AxisGroup(name="轴2", singer_ids=[a, b]),
                ]

        monkeypatch.setattr(dlg_mod, "AxisGroupDialog", FakeDialog)
        page._on_axis_groups()

        # 已保存分组作为初始态；候选 = 使用中的演唱者
        assert captured["initial"] == saved
        assert captured["singers"] == [(a, "A", "#FF0000"), (b, "B", "#00FF00")]
        assert [(g.name, g.singer_ids) for g in project.axis_groups] == [
            ("轴1", [a]),
            ("轴2", [a, b]),
        ]
        # 写回经 set_axis_groups：主分组归一化（首组）
        assert [g.is_primary for g in project.axis_groups] == [True, False]
        assert dirty
        # 小窗摘要随写回刷新
        assert "轴2" in page._axis_summary_label.text()

    def test_edit_button_cancel_keeps_project(self, qapp, monkeypatch):
        from strange_uta_game.frontend.export import axis_group_dialog as dlg_mod

        page, project, _ids, dirty = self._make_page(qapp)

        class FakeDialog:
            def __init__(self, singers, initial_groups=None, parent=None):
                pass

            def exec(self):
                return 0  # Rejected

        monkeypatch.setattr(dlg_mod, "AxisGroupDialog", FakeDialog)
        page._on_axis_groups()

        assert project.axis_groups == []
        assert not dirty

    def test_summary_reflects_grouping_state(self, qapp):
        from strange_uta_game.backend.domain import AxisGroup

        page, project, (a, b), _dirty = self._make_page(qapp)

        # 未分组
        page._refresh_axis_group_summary()
        assert page._axis_summary_label.text().startswith("未分组")

        # 分组 + 未入组提示
        project.set_axis_groups([AxisGroup(name="轴1", singer_ids=[a])])
        page._refresh_axis_group_summary()
        text = page._axis_summary_label.text()
        assert "共 1 组" in text
        assert "主·轴1（A）" in text
        assert "未入组" in text and "B" in text

        # 空组（= 全部演唱者）：显示「全部」且不再有未入组提示
        project.set_axis_groups([AxisGroup(name="轴1", singer_ids=[])])
        page._refresh_axis_group_summary()
        text = page._axis_summary_label.text()
        assert "轴1（全部）" in text
        assert "未入组" not in text


class TestAxisGroupDialog:
    """轴分组对话框行为（不 exec，直接驱动内部方法）。"""

    SINGERS = [
        ("a", "A", "#FF0000"),
        ("b", "B", "#00FF00"),
        ("c", "C", "#0000FF"),
    ]

    def _make_dialog(self, qapp, initial=None):
        from strange_uta_game.frontend.export.axis_group_dialog import (
            AxisGroupDialog,
        )
        from strange_uta_game.backend.domain import AxisGroup

        groups = None
        if initial is not None:
            groups = []
            for entry in initial:
                name, ids = entry[0], entry[1]
                primary = entry[2] if len(entry) > 2 else False
                groups.append(
                    AxisGroup(name=name, singer_ids=list(ids), is_primary=primary)
                )
        return AxisGroupDialog(list(self.SINGERS), initial_groups=groups)

    def test_initial_groups_restore_cards(self, qapp):
        dialog = self._make_dialog(qapp, initial=[("轴1", {"a", "b"}), ("轴2", {"c"})])
        assert len(dialog._cards) == 2
        assert dialog._cards[0].edit_name.text() == "轴1"
        assert dialog._cards[0].checked_singer_ids() == {"a", "b"}
        assert dialog._cards[1].checked_singer_ids() == {"c"}
        # 只有 1 组时才隐藏删除按钮；2 组时可用
        assert dialog._cards[0].btn_remove.isEnabled()

    def test_single_card_by_default_and_delete_hidden(self, qapp):
        dialog = self._make_dialog(qapp)
        assert len(dialog._cards) == 1
        assert not dialog._cards[0].btn_remove.isEnabled()
        assert not dialog._cards[0].btn_remove.isVisibleTo(dialog)

    def test_add_grows_right_and_remove_keeps_minimum_one(self, qapp):
        dialog = self._make_dialog(qapp)
        first = dialog._cards[0]

        dialog._on_add_group()
        assert len(dialog._cards) == 2
        # 新卡片插在现有卡片右侧（列表末尾 = 最右卡片位），默认名不与现有冲突
        assert dialog._cards[1] is not first
        assert dialog._row_layout.indexOf(dialog._cards[1]) > dialog._row_layout.indexOf(dialog._cards[0])
        assert dialog._cards[1].edit_name.text() == "轴2"

        # 删除后只剩 1 组：删除按钮全部隐藏
        dialog._on_remove_card(dialog._cards[1])
        assert len(dialog._cards) == 1
        assert dialog._cards[0] is first
        assert not first.btn_remove.isEnabled()

        # 再删唯一的组 → no-op
        dialog._on_remove_card(first)
        assert len(dialog._cards) == 1

    def test_get_axis_groups_keeps_empty_as_all_and_orders(self, qapp):
        dialog = self._make_dialog(
            qapp, initial=[("轴1", {"c", "a"}), ("轴2", set())]
        )
        # 空勾选的组保留（singer_ids 为空 = 全部演唱者，不在读取层物化）；
        # 组内顺序按候选列表顺序归一
        groups = dialog.get_axis_groups()
        assert [(g.name, g.singer_ids) for g in groups] == [
            ("轴1", ["a", "c"]),
            ("轴2", []),
        ]

    def test_accept_allows_group_without_singers(self, qapp, monkeypatch):
        """组内不勾选任何演唱者 = 全部演唱者，确认不应被拦截。"""
        from strange_uta_game.frontend.export import axis_group_dialog as dlg_mod

        warned = []
        monkeypatch.setattr(
            dlg_mod, "message_warning",
            lambda *args, **kwargs: warned.append(args),
        )
        dialog = self._make_dialog(qapp, initial=[("轴1", set())])

        dialog._on_accept()

        assert not warned
        assert dialog.result() != 0  # 已 accept

    def test_primary_radio_exclusive_and_default_first(self, qapp):
        dialog = self._make_dialog(
            qapp, initial=[("轴1", {"a"}), ("轴2", {"b"})]
        )
        # 源数据未标记主分组 → 首组默认选中
        assert dialog._cards[0].radio_primary.isChecked()
        assert not dialog._cards[1].radio_primary.isChecked()

        # 切换到轴2 → 轴1 自动取消（跨卡片互斥）
        dialog._cards[1].radio_primary.setChecked(True)
        assert not dialog._cards[0].radio_primary.isChecked()
        assert dialog._cards[1].radio_primary.isChecked()

        groups = dialog.get_axis_groups()
        assert [g.is_primary for g in groups] == [False, True]

    def test_primary_moves_to_first_after_primary_removed(self, qapp):
        dialog = self._make_dialog(
            qapp, initial=[("轴1", {"a"}), ("轴2", {"b"})]
        )
        # 删除主分组（轴1）→ 主分组身份移交剩余首组
        dialog._on_remove_card(dialog._cards[0])
        assert len(dialog._cards) == 1
        assert dialog._cards[0].radio_primary.isChecked()

    def test_accept_blocked_on_empty_group_name(self, qapp, monkeypatch):
        from strange_uta_game.frontend.export import axis_group_dialog as dlg_mod

        warned = []
        monkeypatch.setattr(
            dlg_mod, "message_warning",
            lambda *args, **kwargs: warned.append(args),
        )
        dialog = self._make_dialog(qapp, initial=[("轴1", {"a"})])
        dialog._cards[0].edit_name.setText("   ")

        dialog._on_accept()

        assert any("分组名不能为空" in str(w) for w in warned)
        assert dialog.result() == 0

    def test_accept_blocked_on_duplicate_group_names(self, qapp, monkeypatch):
        from strange_uta_game.frontend.export import axis_group_dialog as dlg_mod

        warned = []
        monkeypatch.setattr(
            dlg_mod, "message_warning",
            lambda *args, **kwargs: warned.append(args),
        )
        dialog = self._make_dialog(
            qapp, initial=[("轴1", {"a"}), ("轴1", {"b"})]
        )

        dialog._on_accept()

        assert any("分组名不能重复" in str(w) for w in warned)
        assert dialog.result() == 0


class TestStandaloneNoRegression:
    def test_file_mode_when_no_provider(self, tmp_path):
        s = AppSettings(config_path=str(tmp_path / "config.json"))
        assert s._provider is None
        assert s._config_path is not None


class TestVideoLoadFfmpegHintContract:
    """视频加载 FFmpeg 缺失提示的指引契约（EMBEDDING §5）。

    embedded 下 SUG 自身的「设置 → 关于/语言 → 工具配置」入口被隐藏
    （about.py tools_group），提示必须引导到工作台设置，否则用户按
    提示找不到任何可操作入口；standalone 下保持原有设置页指引。
    前端 InfoBar（home/file_loader）与异步路径透传的 extract_audio
    报错共用 video_converter.is_embedded 判定源与同款文案。
    """

    def _make_video(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        return str(video)

    def test_missing_ffmpeg_points_to_workbench_in_embedded(
        self, tmp_path, monkeypatch, reset_default_provider
    ):
        from strange_uta_game.backend.infrastructure.audio import video_converter

        AppSettings.set_default_provider(MockProvider())
        monkeypatch.setattr(video_converter.shutil, "which", lambda name: None)

        assert video_converter.is_embedded()
        with pytest.raises(RuntimeError) as ei:
            video_converter.extract_audio(self._make_video(tmp_path))
        assert "工作台" in str(ei.value)
        assert "设置 →" not in str(ei.value), "embedded 下不得指向被隐藏的 SUG 设置入口"

    def test_exe_not_found_points_to_workbench_in_embedded(
        self, tmp_path, monkeypatch, reset_default_provider
    ):
        from strange_uta_game.backend.infrastructure.audio import video_converter

        AppSettings.set_default_provider(MockProvider())
        monkeypatch.setattr(video_converter, "is_ffmpeg_available", lambda: True)
        monkeypatch.setattr(video_converter, "get_ffmpeg_path", lambda: "Z:/nonexistent/ffmpeg.exe")

        with pytest.raises(RuntimeError) as ei:
            video_converter.extract_audio(self._make_video(tmp_path))
        assert "工作台" in str(ei.value)

    def test_missing_ffmpeg_keeps_settings_hint_in_standalone(
        self, tmp_path, monkeypatch, reset_default_provider
    ):
        from strange_uta_game.backend.infrastructure.audio import video_converter

        # patch get_ffmpeg_path 隔离宿主机真实 config 的干扰
        monkeypatch.setattr(video_converter, "get_ffmpeg_path", lambda: "ffmpeg")
        monkeypatch.setattr(video_converter.shutil, "which", lambda name: None)

        assert not video_converter.is_embedded()
        with pytest.raises(RuntimeError) as ei:
            video_converter.extract_audio(self._make_video(tmp_path))
        assert "设置 → 关于/语言" in str(ei.value)


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


class TestLogsDirContract:
    """SUG_LOGS_DIR 契约：嵌入宿主把日志（ai_timing.log / crash.log）收进
    自己的数据目录；不设时行为与 standalone 完全一致。"""

    def test_env_var_takes_priority(self, tmp_path, monkeypatch):
        from strange_uta_game import app_dirs

        custom = tmp_path / "host-logs"
        monkeypatch.setenv("SUG_LOGS_DIR", str(custom))
        assert app_dirs.logs_dir() == custom
        assert custom.is_dir()  # 已确保存在

    def test_ailog_follows_env_var(self, tmp_path, monkeypatch):
        from strange_uta_game.backend.application.ai_timing.ailog import (
            ai_log_path,
            ailog,
        )

        # conftest 为整个会话设了 SUG_AI_TIMING_LOG（worker 精确路径，
        # 优先级更高）；这里验证的是「无精确路径时跟随目录变量」
        monkeypatch.delenv("SUG_AI_TIMING_LOG", raising=False)
        custom = tmp_path / "host-logs"
        monkeypatch.setenv("SUG_LOGS_DIR", str(custom))
        assert ai_log_path() == custom / "ai_timing.log"
        ailog("contract", "写入宿主目录")
        assert (custom / "ai_timing.log").is_file()

    def test_worker_path_marker_lower_priority_than_ailog_env(
        self, tmp_path, monkeypatch
    ):
        """SUG_AI_TIMING_LOG（worker 内部机制）优先于 SUG_LOGS_DIR：
        宿主侧已解析好的绝对路径不被目录级变量改写。"""
        from strange_uta_game.backend.application.ai_timing.ailog import (
            ai_log_path,
        )

        exact = tmp_path / "exact" / "ai_timing.log"
        monkeypatch.setenv("SUG_LOGS_DIR", str(tmp_path / "host-logs"))
        monkeypatch.setenv("SUG_AI_TIMING_LOG", str(exact))
        assert ai_log_path() == exact
