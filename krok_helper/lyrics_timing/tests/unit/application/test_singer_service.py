"""SingerService 测试。"""

import pytest
from strange_uta_game.backend.application import SingerService
from strange_uta_game.backend.domain import Project, Singer


class TestSingerService:
    """测试演唱者服务"""

    def test_add_singer(self):
        project = Project()
        service = SingerService(project)

        singer = service.add_singer("和声")

        assert singer.name == "和声"
        assert len(project.singers) == 2

    def test_add_singer_auto_color(self):
        """测试自动分配颜色"""
        project = Project()
        service = SingerService(project)

        singer1 = service.add_singer("演唱者1")
        singer2 = service.add_singer("演唱者2")

        # 颜色应该不同
        assert singer1.color != singer2.color

    def test_remove_singer(self):
        project = Project()
        service = SingerService(project)

        singer = service.add_singer("和声")
        singer_id = singer.id

        success = service.remove_singer(singer_id)

        assert success
        assert len(project.singers) == 1

    def test_rename_singer(self):
        project = Project()
        service = SingerService(project)

        singer = service.add_singer("旧名称")

        success = service.rename_singer(singer.id, "新名称")

        assert success
        assert singer.name == "新名称"

    def test_change_singer_color(self):
        project = Project()
        service = SingerService(project)

        singer = service.add_singer("测试", color="#FF0000")

        success = service.change_singer_color(singer.id, "#00FF00")

        assert success
        assert singer.color == "#00FF00"

    def test_set_singer_enabled(self):
        project = Project()
        service = SingerService(project)

        singer = service.add_singer("测试")

        success = service.set_singer_enabled(singer.id, False)

        assert success
        assert not singer.enabled

    def test_get_singer(self):
        project = Project()
        service = SingerService(project)

        singer = service.add_singer("测试")

        found = service.get_singer(singer.id)

        assert found == singer

    def test_get_singer_not_found(self):
        project = Project()
        service = SingerService(project)

        found = service.get_singer("nonexistent")

        assert found is None

    def test_get_all_singers(self):
        project = Project()
        service = SingerService(project)

        service.add_singer("演唱者1")
        service.add_singer("演唱者2")

        singers = service.get_all_singers()

        assert len(singers) == 3  # 1 个默认 + 2 个新增

    def test_get_enabled_singers_only(self):
        project = Project()
        service = SingerService(project)

        singer = service.add_singer("测试")
        singer.set_enabled(False)

        singers = service.get_all_singers(include_disabled=False)

        assert singer not in singers

    def test_callbacks(self):
        """测试回调函数"""
        callbacks_triggered = []

        def on_added(singer):
            callbacks_triggered.append(("added", singer.name))

        def on_removed(singer_id):
            callbacks_triggered.append(("removed", singer_id))

        def on_updated(singer):
            callbacks_triggered.append(("updated", singer.name))

        from strange_uta_game.backend.application import SingerCallbacks

        callbacks = SingerCallbacks(
            on_singer_added=on_added,
            on_singer_removed=on_removed,
            on_singer_updated=on_updated,
        )

        project = Project()
        service = SingerService(project, callbacks=callbacks)

        singer = service.add_singer("测试")
        assert ("added", "测试") in callbacks_triggered

        service.rename_singer(singer.id, "新名称")
        assert ("updated", "新名称") in callbacks_triggered


class TestReorderSingers:
    """测试 reorder_singers"""

    def _make_service(self):
        project = Project()
        service = SingerService(project)
        a = service.add_singer("A")
        b = service.add_singer("B")
        c = service.add_singer("C")
        return project, service, a, b, c

    def test_reorder_basic(self):
        project, service, a, b, c = self._make_service()
        default_id = project.get_default_singer().id
        # 反转顺序
        new_order = [c.id, b.id, a.id, default_id]
        assert service.reorder_singers(new_order)
        assert [s.id for s in project.singers] == new_order

    def test_reorder_wrong_ids_fails(self):
        project, service, a, b, c = self._make_service()
        # 传入不完整 ID 集合
        assert not service.reorder_singers([a.id, b.id])

    def test_reorder_unknown_id_fails(self):
        project, service, a, b, c = self._make_service()
        default_id = project.get_default_singer().id
        assert not service.reorder_singers([a.id, b.id, c.id, "nonexistent"])


class TestMoveSingers:
    """测试 move_singers"""

    def _ids(self, project):
        return [s.id for s in project.singers]

    def _make_service(self, n=4):
        project = Project()
        service = SingerService(project)
        singers = [service.add_singer(str(i)) for i in range(n)]
        return project, service, singers

    def test_move_up_single(self):
        project, service, singers = self._make_service(4)
        default_id = project.get_default_singer().id
        # singers 顺序: default, 0, 1, 2, 3
        all_ids = self._ids(project)
        target_id = all_ids[2]  # 索引2
        service.move_singers([target_id], "up")
        new_ids = self._ids(project)
        assert new_ids.index(target_id) == 1

    def test_move_down_single(self):
        project, service, singers = self._make_service(4)
        all_ids = self._ids(project)
        target_id = all_ids[1]
        service.move_singers([target_id], "down")
        new_ids = self._ids(project)
        assert new_ids.index(target_id) == 2

    def test_move_up_at_top_no_change(self):
        project, service, singers = self._make_service(3)
        all_ids = self._ids(project)
        top_id = all_ids[0]
        service.move_singers([top_id], "up")
        assert self._ids(project)[0] == top_id

    def test_move_down_at_bottom_no_change(self):
        project, service, singers = self._make_service(3)
        all_ids = self._ids(project)
        bot_id = all_ids[-1]
        service.move_singers([bot_id], "down")
        assert self._ids(project)[-1] == bot_id

    def test_move_top(self):
        project, service, singers = self._make_service(4)
        all_ids = self._ids(project)
        # 选中最后两项置顶
        to_top = [all_ids[3], all_ids[4]]
        service.move_singers(to_top, "top")
        new_ids = self._ids(project)
        assert new_ids[0] in to_top
        assert new_ids[1] in to_top

    def test_move_bottom(self):
        project, service, singers = self._make_service(4)
        all_ids = self._ids(project)
        to_bot = [all_ids[0], all_ids[1]]
        service.move_singers(to_bot, "bottom")
        new_ids = self._ids(project)
        assert new_ids[-2] in to_bot
        assert new_ids[-1] in to_bot

    def test_move_multi_up_preserves_gap(self):
        """多选上移保持相对间隔：选 1,3 上移 → 0,2"""
        project, service, singers = self._make_service(4)
        all_ids = self._ids(project)
        sel = [all_ids[1], all_ids[3]]
        service.move_singers(sel, "up")
        new_ids = self._ids(project)
        assert new_ids.index(all_ids[1]) == 0
        assert new_ids.index(all_ids[3]) == 2

    def test_move_invalid_direction(self):
        project, service, singers = self._make_service(2)
        all_ids = self._ids(project)
        assert not service.move_singers([all_ids[0]], "left")

    def test_move_empty_ids(self):
        project, service, singers = self._make_service(2)
        assert not service.move_singers([], "up")


class TestBatchRemoveSingers:
    """测试 batch_remove_singers"""

    def test_remove_single(self):
        project = Project()
        service = SingerService(project)
        a = service.add_singer("A")
        b = service.add_singer("B")
        assert service.batch_remove_singers([a.id], b.id)
        assert project.get_singer(a.id) is None
        assert len(project.singers) == 2  # default + B

    def test_remove_refuses_to_empty_all(self):
        project = Project()
        service = SingerService(project)
        a = service.add_singer("A")
        all_ids = [s.id for s in project.singers]
        assert not service.batch_remove_singers(all_ids, None)

    def test_remove_transfer_must_not_be_in_deleted(self):
        project = Project()
        service = SingerService(project)
        a = service.add_singer("A")
        b = service.add_singer("B")
        # 转移目标也在被删列表中
        assert not service.batch_remove_singers([a.id, b.id], a.id)

    def test_remove_default_reassigns(self):
        project = Project()
        service = SingerService(project)
        a = service.add_singer("A")
        default = project.get_default_singer()
        # 删除默认演唱者，转移给 A
        assert service.batch_remove_singers([default.id], a.id)
        assert project.get_default_singer().id == a.id


class TestBatchSetEnabled:
    """测试 batch_set_enabled"""

    def test_disable_then_enable(self):
        project = Project()
        service = SingerService(project)
        a = service.add_singer("A")
        b = service.add_singer("B")
        assert service.batch_set_enabled([a.id, b.id], False)
        assert not a.enabled
        assert not b.enabled
        assert service.batch_set_enabled([a.id], True)
        assert a.enabled
        assert not b.enabled

    def test_empty_ids_fails(self):
        project = Project()
        service = SingerService(project)
        assert not service.batch_set_enabled([], True)

    def test_unknown_id_returns_false(self):
        project = Project()
        service = SingerService(project)
        assert not service.batch_set_enabled(["nonexistent"], True)
