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
