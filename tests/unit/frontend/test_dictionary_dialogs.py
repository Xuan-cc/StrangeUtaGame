"""词典编辑对话框（本地词典 + 网络源条目）过滤与置顶/置底单测。

守护行为：
- 过滤框按「词」列子串实时隐藏不匹配行，可见行行号保持原词典序号
- 过滤期间上移/下移禁用；置顶/置底仍可用且语义按原词典整体生效
- 过滤不丢数据（get_entries 始终返回全量、原顺序）
- 置顶/置底保持选中行相对顺序，并重新选中移动后的可见行
- 添加条目自动清空过滤，确保新行可见
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLabel, QTableWidgetSelectionRange

from strange_uta_game.frontend.settings.dictionary_dialog import DictionaryEditDialog
from strange_uta_game.frontend.settings.network_dictionary_dialog import (
    NetworkDictionaryDialog,
    NetworkSourceEntriesDialog,
)


def _sample_entries() -> list:
    return [
        {"enabled": True, "word": "微笑", "reading": "{微笑||ほほ,え}"},
        {"enabled": False, "word": "大冒険", "reading": "{大冒険||だ|い,ぼ|う,け|ん}"},
        {"enabled": True, "word": "光", "reading": "{光||ひかり}"},
        {"enabled": True, "word": "微笑み", "reading": "{微笑||ほほ,え}み"},
        {"enabled": True, "word": "Hero", "reading": "{Hero||ヒーロー}"},
    ]


def _visible_rows(dlg) -> list:
    return [
        r for r in range(dlg._table.rowCount()) if not dlg._table.isRowHidden(r)
    ]


def _words(dlg) -> list:
    return [e["word"] for e in dlg.get_entries()]


def _selected_rows(dlg) -> set:
    return {i.row() for i in dlg._table.selectedIndexes()}


@pytest.fixture(params=["local", "network"])
def dlg(request, qapp):
    """同一套用例跑两种对话框（构造签名不同）。"""
    if request.param == "local":
        return DictionaryEditDialog(_sample_entries())
    return NetworkSourceEntriesDialog("测试源", _sample_entries())


class TestFilter:
    def test_filter_hides_non_matching(self, dlg):
        dlg._filter_edit.setText("微笑")
        # 可见行号 = 原词典序号（0「微笑」、3「微笑み」）
        assert _visible_rows(dlg) == [0, 3]

    def test_filter_case_insensitive(self, dlg):
        dlg._filter_edit.setText("hero")
        assert _visible_rows(dlg) == [4]

    def test_filter_clear_restores_all(self, dlg):
        dlg._filter_edit.setText("微笑")
        dlg._filter_edit.clear()
        assert _visible_rows(dlg) == [0, 1, 2, 3, 4]

    def test_filter_preserves_entries(self, dlg):
        dlg._filter_edit.setText("微笑")
        assert _words(dlg) == ["微笑", "大冒険", "光", "微笑み", "Hero"]

    def test_filter_disables_up_down(self, dlg):
        dlg._filter_edit.setText("微笑")
        assert not dlg._btn_up.isEnabled()
        assert not dlg._btn_down.isEnabled()
        dlg._filter_edit.clear()
        assert dlg._btn_up.isEnabled()
        assert dlg._btn_down.isEnabled()

    def test_filter_clears_selection(self, dlg):
        # 先选中再过滤：隐藏行的选中态不应残留（防「删除选中」误删）
        dlg._table.selectRow(1)
        dlg._filter_edit.setText("微笑")
        assert _selected_rows(dlg) == set()


class TestMoveToTopBottom:
    def test_move_to_top_plain(self, dlg):
        dlg._table.selectRow(2)  # 光
        dlg._on_move_to_top()
        assert _words(dlg) == ["光", "微笑", "大冒険", "微笑み", "Hero"]
        assert _selected_rows(dlg) == {0}

    def test_move_to_bottom_plain_multi_keeps_order(self, dlg):
        # setRangeSelected 累加多选（selectRow 程序化调用会替换选择）
        last_col = dlg._table.columnCount() - 1
        dlg._table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, last_col), True)
        dlg._table.setRangeSelected(QTableWidgetSelectionRange(2, 0, 2, last_col), True)
        assert _selected_rows(dlg) == {0, 2}
        dlg._on_move_to_bottom()
        assert _words(dlg) == ["大冒険", "微笑み", "Hero", "微笑", "光"]
        assert _selected_rows(dlg) == {3, 4}

    def test_move_to_top_noop_when_already_top(self, dlg):
        dlg._table.selectRow(0)
        before = _words(dlg)
        dlg._on_move_to_top()
        assert _words(dlg) == before

    def test_move_to_top_under_filter(self, dlg):
        dlg._filter_edit.setText("微笑")  # 可见 [0, 3]
        dlg._table.selectRow(3)  # 微笑み
        dlg._on_move_to_top()
        assert _words(dlg) == ["微笑み", "微笑", "大冒険", "光", "Hero"]
        # 过滤仍生效，可见的是两个「微笑*」（行号已按新顺序重排）
        assert _visible_rows(dlg) == [0, 1]
        assert _selected_rows(dlg) == {0}

    def test_move_to_bottom_under_filter(self, dlg):
        dlg._filter_edit.setText("微笑")
        dlg._table.selectRow(0)  # 微笑
        dlg._on_move_to_bottom()
        assert _words(dlg) == ["大冒険", "光", "微笑み", "Hero", "微笑"]
        assert _visible_rows(dlg) == [2, 4]
        assert _selected_rows(dlg) == {4}


class TestAdd:
    def test_add_clears_filter_and_shows_new_row(self, dlg):
        dlg._filter_edit.setText("微笑")
        dlg._on_add()
        assert dlg._filter_edit.text() == ""
        # 新空行置顶，原 5 行全部恢复可见
        assert _visible_rows(dlg) == [0, 1, 2, 3, 4, 5]
        assert dlg._table.item(0, 1).text() == ""


def test_network_dictionary_dialog_shows_host_managed_cache(qapp):
    dialog = NetworkDictionaryDialog({"sources": []}, cache_path=None)

    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("条目缓存：由宿主管理" in text for text in labels)
    assert all("None" not in text for text in labels)
