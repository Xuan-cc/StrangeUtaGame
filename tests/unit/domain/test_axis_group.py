"""轴分组（AxisGroup）领域模型测试。

覆盖：实体构造规约、Project 挂载 / singer 删除清理、set_axis_groups
引用过滤、.sug 序列化往返与旧文件兼容。
"""

import pytest

from strange_uta_game.backend.domain import Project, Singer, AxisGroup
from strange_uta_game.backend.infrastructure.persistence.sug_io import (
    SugProjectParser,
)


def _two_singer_project() -> tuple:
    project = Project(
        singers=[
            Singer(name="A", color="#FF0000", is_default=True),
            Singer(name="B", color="#00FF00"),
        ]
    )
    return project, project.singers[0].id, project.singers[1].id


class TestAxisGroupEntity:
    def test_name_stripped_and_ids_deduped(self):
        group = AxisGroup(name="  轴1 ", singer_ids=["a", "b", "a", "", None])
        assert group.name == "轴1"
        assert group.singer_ids == ["a", "b"]

    def test_default_construction(self):
        group = AxisGroup()
        assert group.name == ""
        assert group.singer_ids == []

    def test_contains_singer(self):
        group = AxisGroup(name="轴1", singer_ids=["a"])
        assert group.contains_singer("a")
        assert not group.contains_singer("b")

    def test_dict_roundtrip(self):
        group = AxisGroup(name="轴1", singer_ids=["a", "b"], is_primary=True)
        assert AxisGroup.from_dict(group.to_dict()) == group

    def test_dict_roundtrip_without_primary_key(self):
        # 旧版 .sug 无 is_primary 键 → 缺省 False
        group = AxisGroup.from_dict({"name": "轴1", "singer_ids": ["a"]})
        assert group.is_primary is False

    def test_from_dict_tolerates_bad_payload(self):
        assert AxisGroup.from_dict("junk") == AxisGroup()
        assert AxisGroup.from_dict({}) == AxisGroup()
        assert AxisGroup.from_dict({"singer_ids": [None, 5]}).singer_ids == ["5"]


class TestProjectAxisGroups:
    def test_default_empty(self):
        assert Project().axis_groups == []

    def test_set_axis_groups_filters_unknown_ids(self):
        project, a, b = _two_singer_project()
        project.set_axis_groups(
            [AxisGroup(name="轴1", singer_ids=[a, b, "ghost"])]
        )
        assert project.axis_groups[0].singer_ids == [a, b]

    def test_set_axis_groups_clear(self):
        project, a, _b = _two_singer_project()
        project.set_axis_groups([AxisGroup(name="轴1", singer_ids=[a])])
        project.set_axis_groups([])
        assert project.axis_groups == []

    def test_assigned_axis_singer_ids_union(self):
        project, a, b = _two_singer_project()
        project.set_axis_groups(
            [
                AxisGroup(name="轴1", singer_ids=[a]),
                AxisGroup(name="轴2", singer_ids=[b, a]),
            ]
        )
        assert project.assigned_axis_singer_ids() == {a, b}

    def test_remove_singer_cleans_references(self):
        project, a, b = _two_singer_project()
        project.set_axis_groups(
            [
                AxisGroup(name="轴1", singer_ids=[a, b]),
                AxisGroup(name="轴2", singer_ids=[b]),
            ]
        )
        project.remove_singer(b)
        # 引用被清理；被清空的"轴2"整组丢弃
        assert [(g.name, g.singer_ids) for g in project.axis_groups] == [
            ("轴1", [a])
        ]

    def test_remove_singer_keeps_preexisting_empty_group(self):
        """本来就空的组（= 全部演唱者口径）不被 remove_singer 丢弃。"""
        project, a, b = _two_singer_project()
        project.set_axis_groups(
            [
                AxisGroup(name="全部轴", singer_ids=[]),
                AxisGroup(name="轴B", singer_ids=[b]),
            ]
        )
        project.remove_singer(b)
        # 显式组"轴B"被清空 → 丢弃；空组"全部轴"保留原样
        assert [(g.name, g.singer_ids) for g in project.axis_groups] == [
            ("全部轴", [])
        ]

    def test_primary_normalization_promotes_first(self):
        # 无主分组 → 首组提升
        project, a, b = _two_singer_project()
        project.set_axis_groups(
            [AxisGroup(name="轴1", singer_ids=[a]), AxisGroup(name="轴2", singer_ids=[b])]
        )
        assert [g.is_primary for g in project.axis_groups] == [True, False]

    def test_primary_normalization_keeps_first_marked_only(self):
        # 多个标记主分组 → 只保留第一个
        project, a, b = _two_singer_project()
        project.set_axis_groups(
            [
                AxisGroup(name="轴1", singer_ids=[a], is_primary=True),
                AxisGroup(name="轴2", singer_ids=[b], is_primary=True),
            ]
        )
        assert [g.is_primary for g in project.axis_groups] == [True, False]

    def test_primary_normalization_on_construct(self):
        # 直接构造（不走 set_axis_groups）也归一化
        project, a, b = _two_singer_project()
        project.axis_groups = [
            AxisGroup(name="轴1", singer_ids=[a]),
            AxisGroup(name="轴2", singer_ids=[b]),
        ]
        project._normalize_axis_groups()
        assert project.primary_axis_group().name == "轴1"

    def test_primary_axis_group_fallback_and_empty(self):
        project, a, _b = _two_singer_project()
        assert project.primary_axis_group() is None
        project.set_axis_groups([AxisGroup(name="轴1", singer_ids=[a])])
        assert project.primary_axis_group() is project.axis_groups[0]


class TestSugIoAxisGroups:
    def test_roundtrip(self, tmp_path):
        project, a, b = _two_singer_project()
        project.set_axis_groups(
            [
                AxisGroup(name="轴1", singer_ids=[a, b]),
                AxisGroup(name="轴2", singer_ids=[b], is_primary=True),
            ]
        )
        file_path = tmp_path / "axis.sug"
        SugProjectParser.save(project, str(file_path))
        loaded = SugProjectParser.load(str(file_path))
        assert loaded.axis_groups == project.axis_groups
        # is_primary 序列化往返（轴2 显式主分组，轴1 归一化降级）
        assert [g.is_primary for g in loaded.axis_groups] == [False, True]

    def test_not_written_when_empty(self, tmp_path):
        project, _a, _b = _two_singer_project()
        data = SugProjectParser.serialize(project)
        assert "axis_groups" not in data

    def test_legacy_file_without_key_loads_empty(self, tmp_path):
        import json

        project, _a, _b = _two_singer_project()
        data = SugProjectParser.serialize(project)
        legacy = {k: v for k, v in data.items() if k != "axis_groups"}
        file_path = tmp_path / "legacy.sug"
        file_path.write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )
        loaded = SugProjectParser.load(str(file_path))
        assert loaded.axis_groups == []
