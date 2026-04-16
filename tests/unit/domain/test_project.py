"""Project 聚合根测试。"""

import pytest
from strange_uta_game.backend.domain import (
    Project,
    ProjectMetadata,
    Singer,
    LyricLine,
    ValidationError,
    DomainError,
)


class TestProject:
    """Project 聚合根测试类"""
    
    def test_creation_with_defaults(self):
        """测试使用默认值创建 Project"""
        project = Project()
        
        assert project.id is not None
        assert len(project.lines) == 0
        assert len(project.singers) == 1  # 自动生成默认演唱者
        assert project.audio_duration_ms == 0
        assert isinstance(project.metadata, ProjectMetadata)
        
        # 验证默认演唱者
        default_singer = project.singers[0]
        assert default_singer.is_default is True
        assert default_singer.name == "演唱者1"
    
    def test_creation_with_custom_singers(self):
        """测试使用自定义演唱者创建"""
        singer = Singer(name="初音ミク", is_default=True)
        project = Project(singers=[singer])
        
        assert len(project.singers) == 1
        assert project.singers[0].name == "初音ミク"
    
    def test_auto_create_default_singer_if_none(self):
        """测试如果没有提供演唱者则自动创建默认演唱者"""
        project = Project(singers=[])
        
        assert len(project.singers) == 1
        assert project.singers[0].is_default is True
    
    def test_add_singer(self):
        """测试添加演唱者"""
        project = Project()
        
        new_singer = Singer(name="和声", color="#4ECDC4")
        project.add_singer(new_singer)
        
        assert len(project.singers) == 2
    
    def test_add_default_singer_resets_existing_default(self):
        """测试添加新的默认演唱者会重置现有默认演唱者"""
        project = Project()
        original_default = project.get_default_singer()
        
        new_singer = Singer(name="新默认", is_default=True)
        project.add_singer(new_singer)
        
        # 原来的默认演唱者不再是默认
        assert original_default.is_default is False
        # 新的演唱者是默认
        assert new_singer.is_default is True
    
    def test_remove_singer(self):
        """测试删除演唱者"""
        project = Project()
        singer = Singer(name="和声")
        project.add_singer(singer)
        
        project.remove_singer(singer.id)
        
        assert len(project.singers) == 1  # 只剩默认演唱者
    
    def test_cannot_remove_last_singer(self):
        """测试不能删除最后一个演唱者"""
        project = Project()  # 只有一个默认演唱者
        
        with pytest.raises(ValidationError) as exc_info:
            project.remove_singer(project.singers[0].id)
        
        assert "至少保留一个演唱者" in str(exc_info.value)
    
    def test_remove_singer_with_cascade_delete(self):
        """测试删除演唱者时级联删除其歌词"""
        project = Project()
        singer = Singer(name="和声")
        project.add_singer(singer)
        
        # 添加该演唱者的歌词
        line = LyricLine(singer_id=singer.id, text="测试歌词")
        project.add_line(line)
        
        assert len(project.lines) == 1
        
        # 删除演唱者（不传 transfer_to，级联删除）
        project.remove_singer(singer.id)
        
        # 歌词也被删除
        assert len(project.lines) == 0
    
    def test_remove_singer_with_transfer(self):
        """测试删除演唱者时转移歌词到另一个演唱者"""
        project = Project()
        default_singer = project.get_default_singer()
        
        singer = Singer(name="和声")
        project.add_singer(singer)
        
        # 添加该演唱者的歌词
        line = LyricLine(singer_id=singer.id, text="测试歌词")
        project.add_line(line)
        
        # 删除演唱者并转移歌词
        project.remove_singer(singer.id, transfer_to=default_singer.id)
        
        # 歌词还在，但 singer_id 已更改
        assert len(project.lines) == 1
        assert project.lines[0].singer_id == default_singer.id
    
    def test_get_singer(self):
        """测试根据ID获取演唱者"""
        project = Project()
        singer = project.singers[0]
        
        found = project.get_singer(singer.id)
        assert found == singer
        
        not_found = project.get_singer("nonexistent")
        assert not_found is None
    
    def test_get_default_singer(self):
        """测试获取默认演唱者"""
        project = Project()
        default = project.get_default_singer()
        
        assert default.is_default is True
    
    def test_add_line(self):
        """测试添加歌词行"""
        project = Project()
        singer = project.get_default_singer()
        
        line = LyricLine(singer_id=singer.id, text="测试歌词")
        project.add_line(line)
        
        assert len(project.lines) == 1
        assert project.lines[0].text == "测试歌词"
    
    def test_add_line_with_invalid_singer(self):
        """测试添加歌词行时 singer_id 无效应该抛出 ValidationError"""
        project = Project()
        
        with pytest.raises(ValidationError) as exc_info:
            line = LyricLine(singer_id="invalid_id", text="测试")
            project.add_line(line)
        
        assert "不存在" in str(exc_info.value)
    
    def test_remove_line(self):
        """测试删除歌词行"""
        project = Project()
        singer = project.get_default_singer()
        
        line = LyricLine(singer_id=singer.id, text="测试歌词")
        project.add_line(line)
        
        project.remove_line(line.id)
        
        assert len(project.lines) == 0
    
    def test_remove_nonexistent_line_raises_error(self):
        """测试删除不存在的歌词行应该抛出 DomainError"""
        project = Project()
        
        with pytest.raises(DomainError):
            project.remove_line("nonexistent_id")
    
    def test_get_lines_by_singer(self):
        """测试获取指定演唱者的所有歌词行"""
        project = Project()
        default_singer = project.get_default_singer()
        
        another_singer = Singer(name="和声")
        project.add_singer(another_singer)
        
        # 添加 3 行歌词，2 行属于默认演唱者，1 行属于和声
        line1 = LyricLine(singer_id=default_singer.id, text="歌词1")
        line2 = LyricLine(singer_id=default_singer.id, text="歌词2")
        line3 = LyricLine(singer_id=another_singer.id, text="歌词3")
        
        project.add_line(line1)
        project.add_line(line2)
        project.add_line(line3)
        
        default_lines = project.get_lines_by_singer(default_singer.id)
        assert len(default_lines) == 2
        
        another_lines = project.get_lines_by_singer(another_singer.id)
        assert len(another_lines) == 1
    
    def test_move_line(self):
        """测试移动歌词行位置"""
        project = Project()
        singer = project.get_default_singer()
        
        # 添加 3 行歌词
        line1 = LyricLine(singer_id=singer.id, text="第一行")
        line2 = LyricLine(singer_id=singer.id, text="第二行")
        line3 = LyricLine(singer_id=singer.id, text="第三行")
        
        project.add_line(line1)
        project.add_line(line2)
        project.add_line(line3)
        
        # 将第三行移到第一行位置
        project.move_line(line3.id, 0)
        
        assert project.lines[0].text == "第三行"
        assert project.lines[1].text == "第一行"
        assert project.lines[2].text == "第二行"
    
    def test_get_all_timetags(self):
        """测试获取所有时间标签"""
        project = Project()
        singer = project.get_default_singer()
        
        # 添加带时间标签的歌词行
        from strange_uta_game.backend.domain import TimeTag
        
        line1 = LyricLine(singer_id=singer.id, text="AB")
        line1.add_timetag(TimeTag(timestamp_ms=1000, singer_id=singer.id, char_idx=0))
        line1.add_timetag(TimeTag(timestamp_ms=2000, singer_id=singer.id, char_idx=1))
        
        project.add_line(line1)
        
        all_tags = project.get_all_timetags()
        
        assert len(all_tags) == 2
        # 按时间排序
        assert all_tags[0][2].timestamp_ms == 1000
        assert all_tags[1][2].timestamp_ms == 2000
    
    def test_get_timing_statistics(self):
        """测试获取打轴统计信息"""
        project = Project()
        singer = project.get_default_singer()
        
        from strange_uta_game.backend.domain import TimeTag
        
        # 添加 2 行歌词
        line1 = LyricLine(singer_id=singer.id, text="AB")
        line1.add_timetag(TimeTag(timestamp_ms=1000, singer_id=singer.id, char_idx=0))
        line1.add_timetag(TimeTag(timestamp_ms=2000, singer_id=singer.id, char_idx=1))
        
        line2 = LyricLine(singer_id=singer.id, text="CD")
        # line2 未打轴
        
        project.add_line(line1)
        project.add_line(line2)
        
        stats = project.get_timing_statistics()
        
        assert stats["total_lines"] == 2
        assert stats["total_chars"] == 4
        assert stats["total_timetags"] == 2
        assert stats["completed_lines"] == 1
        assert stats["timing_progress"] == "2/4"
    
    def test_validate(self):
        """测试项目验证"""
        project = Project()
        
        # 有效项目应该没有错误
        errors = project.validate()
        assert len(errors) == 0
        assert project.is_valid() is True
    
    def test_validate_detects_invalid_singer_id(self):
        """测试验证能检测无效的 singer_id"""
        project = Project()
        
        # 添加一行，使用无效的 singer_id
        line = LyricLine(singer_id="invalid_id", text="测试")
        # 直接添加到 lines 列表（绕过 add_line 的验证）
        project.lines.append(line)
        
        errors = project.validate()
        assert len(errors) == 1
        assert "singer_id" in errors[0]
    
    def test_validate_detects_no_default_singer(self):
        """测试验证能检测没有默认演唱者"""
        # 创建没有默认演唱者的项目
        singer = Singer(name="测试", is_default=False)
        project = Project(singers=[singer])
        
        errors = project.validate()
        assert any("默认演唱者" in e for e in errors)
    
    def test_update_timestamp_on_modify(self):
        """测试修改时更新时间戳"""
        import time
        
        project = Project()
        original_time = project.metadata.updated_at
        
        # 等待一小段时间确保时间变化
        time.sleep(0.01)
        
        # 添加一行（会触发 _update_timestamp）
        singer = project.get_default_singer()
        line = LyricLine(singer_id=singer.id, text="测试")
        project.add_line(line)
        
        assert project.metadata.updated_at > original_time
