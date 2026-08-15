"""进程级字体缓存（font_cache）的单元测试。"""

from __future__ import annotations

from PyQt6.QtGui import QFontDatabase

from strange_uta_game.frontend import font_cache, font_names


def _patch_families(monkeypatch, names: list[str]) -> dict:
    """替换 ``QFontDatabase.families`` 并统计调用次数（缓存应复用单次枚举）。

    伪造函数读取传入列表的**当前内容**，测试中途修改列表即可模拟字体库变化。
    """
    calls = {"count": 0}

    def fake_families():
        calls["count"] += 1
        return list(names)

    monkeypatch.setattr(
        "strange_uta_game.frontend.font_cache.QFontDatabase.families",
        fake_families,
    )
    return calls


def test_snapshot_reuses_single_enumeration(qapp, monkeypatch):
    calls = _patch_families(monkeypatch, ["Arial", "Microsoft YaHei"])

    first = font_cache.installed_families()
    second = font_cache.installed_families()
    assert first == second == ("Arial", "Microsoft YaHei")
    assert font_cache.installed_family_map() == {
        "arial": "Arial",
        "microsoft yahei": "Microsoft YaHei",
    }
    # has_installed_family 保持与旧 ``family in families()`` 一致的精确匹配语义
    assert font_cache.has_installed_family("Arial")
    assert not font_cache.has_installed_family("arial")
    assert not font_cache.has_installed_family("Missing")
    assert calls["count"] == 1


def test_invalidate_forces_reenumeration(qapp, monkeypatch):
    names = ["Arial"]
    calls = _patch_families(monkeypatch, names)

    assert font_cache.installed_families() == ("Arial",)

    names.append("SimSun")
    font_cache.invalidate(clear_alias_map=False)
    assert font_cache.installed_families() == ("Arial", "SimSun")
    assert calls["count"] == 2


def test_picker_entries_cached_and_skip_bitmap_fonts(qapp, monkeypatch):
    calls = _patch_families(monkeypatch, ["Arial", "Terminal"])
    monkeypatch.setattr(
        "strange_uta_game.frontend.font_cache.QFontDatabase.isSmoothlyScalable",
        lambda fam: fam != "Terminal",
    )
    monkeypatch.setattr(
        font_cache, "_alias_map", lambda: {"Arial": {0x0411: "アリアル"}}
    )

    entries = font_cache.font_picker_entries()
    # Arial 的书写系统不含日文，preferred_native 回退到任一非 ASCII 本地名
    assert entries == [("Arial", "アリアル  (Arial)", "arial アリアル")]

    # 再次获取命中缓存，不重新枚举；返回的是副本，改动不影响缓存
    again = font_cache.font_picker_entries()
    again.clear()
    assert font_cache.font_picker_entries() == entries
    assert calls["count"] == 1

    # 失效后重建
    font_cache.invalidate(clear_alias_map=False)
    assert font_cache.font_picker_entries() == entries
    assert calls["count"] == 2


def test_font_utils_routes_through_cache(qapp, monkeypatch):
    calls = _patch_families(
        monkeypatch, ["Microsoft YaHei", "Yu Gothic UI", "Segoe UI"]
    )
    from strange_uta_game.frontend import font_utils

    # 固定候选表，使断言不依赖运行平台的内置候选字体
    monkeypatch.setattr(
        font_utils, "_ui_font_candidates", lambda code: ("Microsoft YaHei", "Yu Gothic UI")
    )

    assert font_utils.ui_font(10).families()[0] == "Microsoft YaHei"
    assert font_utils.ui_font(12).families()[0] == "Microsoft YaHei"
    assert font_utils.resolve_font_family("Yu Gothic UI") == "Yu Gothic UI"
    assert font_utils.resolve_font_family("NoSuch") == font_utils.DEFAULT_FONT_FAMILY
    assert calls["count"] == 1


def test_prewarm_populates_caches(qapp, monkeypatch):
    calls = _patch_families(monkeypatch, ["Arial"])
    monkeypatch.setattr(font_cache, "_alias_map", lambda: {})

    font_cache.prewarm()

    assert calls["count"] == 1
    assert font_cache.installed_families() == ("Arial",)
    # 快照与条目共用同一次枚举
    assert font_cache.font_picker_entries() == [("Arial", "Arial", "arial")]
    assert calls["count"] == 1

    # include_picker_entries=False 只预热快照
    font_cache.invalidate(clear_alias_map=False)
    font_cache.prewarm(include_picker_entries=False)
    assert calls["count"] == 2
    assert font_cache.installed_families() == ("Arial",)


def test_prewarm_async_warms_alias_map_in_background(qapp, monkeypatch):
    warmed = []
    monkeypatch.setattr(font_cache, "_alias_map", lambda: warmed.append(1) or {})

    started = font_cache.prewarm_async()
    assert started is True
    assert font_cache._alias_warm_done.wait(timeout=5)
    assert warmed == [1]
    # 重复调用幂等：不再新起后台扫描
    assert font_cache.prewarm_async() is False

    # 全量失效后可再次预热
    font_cache.invalidate()
    assert font_cache.prewarm_async() is True
    assert font_cache._alias_warm_done.wait(timeout=5)
    assert warmed == [1, 1]


def test_invalidate_can_clear_alias_map(monkeypatch):
    clears = {"count": 0}

    def fake_alias_map():
        return {}

    fake_alias_map.cache_clear = lambda: clears.__setitem__("count", clears["count"] + 1)
    monkeypatch.setattr(font_names, "localized_alias_map", fake_alias_map)

    font_cache.invalidate()
    assert clears["count"] == 1

    font_cache.invalidate(clear_alias_map=False)
    assert clears["count"] == 1
