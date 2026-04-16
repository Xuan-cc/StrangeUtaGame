"""SUG 项目文件解析器测试。"""

import pytest
from pathlib import Path
from datetime import datetime

from strange_uta_game.backend.infrastructure.persistence.sug_parser import (
    SugProjectParser,
    SugParseError,
)
from strange_uta_game.backend.domain import (
    Project,
    Singer,
    LyricLine,
    TimeTag,
    Ruby,
)


class TestSugProjectParser:
    """测试 SUG 项目文件解析器"""

    def test_save_and_load_simple_project(self, tmp_path):
        """测试保存和加载简单项目"""
        # 创建项目
        project = Project()
        singer = project.get_default_singer()

        line = LyricLine(singer_id=singer.id, text="测试歌词")
        project.add_line(line)

        # 保存
        file_path = tmp_path / "test.sug"
        SugProjectParser.save(project, str(file_path))

        # 验证文件存在
        assert file_path.exists()

        # 加载
        loaded = SugProjectParser.load(str(file_path))

        # 验证
        assert loaded.id == project.id
        assert len(loaded.lines) == 1
        assert loaded.lines[0].text == "测试歌词"

    def test_save_and_load_with_timetags(self, tmp_path):
        """测试保存和加载带时间标签的项目"""
        project = Project()
        singer = project.get_default_singer()

        line = LyricLine(singer_id=singer.id, text="赤い花")
        line.add_timetag(TimeTag(timestamp_ms=1000, singer_id=singer.id, char_idx=0))
        line.add_timetag(TimeTag(timestamp_ms=1500, singer_id=singer.id, char_idx=1))
        line.add_timetag(TimeTag(timestamp_ms=2000, singer_id=singer.id, char_idx=2))

        project.add_line(line)

        # 保存并加载
        file_path = tmp_path / "test.sug"
        SugProjectParser.save(project, str(file_path))
        loaded = SugProjectParser.load(str(file_path))

        # 验证
        assert len(loaded.lines[0].timetags) == 3
        assert loaded.lines[0].timetags[0].timestamp_ms == 1000

    def test_save_and_load_with_rubies(self, tmp_path):
        """测试保存和加载带注音的项目"""
        project = Project()
        singer = project.get_default_singer()

        line = LyricLine(singer_id=singer.id, text="赤い花")
        line.add_ruby(Ruby(text="あか", start_idx=0, end_idx=1))
        line.add_ruby(Ruby(text="はな", start_idx=2, end_idx=3))

        project.add_line(line)

        # 保存并加载
        file_path = tmp_path / "test.sug"
        SugProjectParser.save(project, str(file_path))
        loaded = SugProjectParser.load(str(file_path))

        # 验证
        assert len(loaded.lines[0].rubies) == 2
        assert loaded.lines[0].rubies[0].text == "あか"

    def test_save_and_load_multiple_singers(self, tmp_path):
        """测试保存和加载多演唱者项目"""
        project = Project()

        # 添加演唱者
        singer2 = Singer(name="和声", color="#4ECDC4")
        project.add_singer(singer2)

        # 添加歌词
        line1 = LyricLine(singer_id=project.get_default_singer().id, text="主唱")
        line2 = LyricLine(singer_id=singer2.id, text="和声")

        project.add_line(line1)
        project.add_line(line2)

        # 保存并加载
        file_path = tmp_path / "test.sug"
        SugProjectParser.save(project, str(file_path))
        loaded = SugProjectParser.load(str(file_path))

        # 验证
        assert len(loaded.singers) == 2
        assert len(loaded.lines) == 2

    def test_load_nonexistent_file_raises_error(self, tmp_path):
        """测试加载不存在的文件应该报错"""
        with pytest.raises(SugParseError) as exc_info:
            SugProjectParser.load(str(tmp_path / "nonexistent.sug"))

        assert "文件不存在" in str(exc_info.value)

    def test_load_invalid_json_raises_error(self, tmp_path):
        """测试加载无效的 JSON 文件应该报错"""
        file_path = tmp_path / "invalid.sug"
        file_path.write_text("not valid json", encoding="utf-8")

        with pytest.raises(SugParseError) as exc_info:
            SugProjectParser.load(str(file_path))

        assert "JSON" in str(exc_info.value)
