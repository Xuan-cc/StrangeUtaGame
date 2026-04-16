"""ProjectService 测试。"""

import pytest
from pathlib import Path
from strange_uta_game.backend.application import ProjectService, ProjectCallbacks
from strange_uta_game.backend.domain import Project


class TestProjectService:
    """测试项目管理服务"""

    def test_create_project(self):
        service = ProjectService()
        project = service.create_project()

        assert project is not None
        assert isinstance(project, Project)
        assert service.current_project == project

    def test_save_and_load_project(self, tmp_path):
        service = ProjectService()
        project = service.create_project()

        # 添加一些数据
        singer = project.get_default_singer()
        from strange_uta_game.backend.domain import LyricLine

        line = LyricLine(singer_id=singer.id, text="测试歌词")
        project.add_line(line)

        # 保存
        file_path = tmp_path / "test.sug"
        success = service.save_project(str(file_path))

        assert success
        assert file_path.exists()

        # 重新创建服务并加载
        service2 = ProjectService()
        loaded = service2.open_project(str(file_path))

        assert loaded is not None
        assert loaded.id == project.id
        assert len(loaded.lines) == 1

    def test_load_nonexistent_file(self):
        callbacks_triggered = []

        def on_error(msg):
            callbacks_triggered.append(msg)

        callbacks = ProjectCallbacks(on_error=on_error)
        service = ProjectService(callbacks=callbacks)

        result = service.open_project("/nonexistent/file.sug")

        assert result is None
        assert len(callbacks_triggered) == 1
        assert "文件不存在" in callbacks_triggered[0]

    def test_validate_project(self):
        service = ProjectService()
        project = service.create_project()

        # 有效项目
        errors = service.validate_project()
        assert len(errors) == 0

    def test_get_statistics(self):
        service = ProjectService()
        project = service.create_project()

        # 添加数据
        singer = project.get_default_singer()
        from strange_uta_game.backend.domain import LyricLine, TimeTag

        line = LyricLine(singer_id=singer.id, text="AB")
        line.add_timetag(TimeTag(timestamp_ms=1000, singer_id=singer.id, char_idx=0))
        project.add_line(line)

        stats = service.get_project_statistics()

        assert stats["total_lines"] == 1
        assert stats["total_timetags"] == 1

    def test_close_project(self):
        service = ProjectService()
        service.create_project()

        assert service.current_project is not None

        service.close_project()

        assert service.current_project is None
