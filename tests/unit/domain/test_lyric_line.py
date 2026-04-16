"""LyricLine 实体测试。"""

import pytest
from strange_uta_game.backend.domain import (
    LyricLine,
    TimeTag,
    TimeTagType,
    Ruby,
    CheckpointConfig,
    ValidationError,
    DomainError,
)


class TestLyricLine:
    """LyricLine 实体测试类"""
    
    def test_creation_with_defaults(self):
        """测试使用默认值创建 LyricLine"""
        line = LyricLine(
            singer_id="singer_1",
            text="测试歌词"
        )
        
        assert line.singer_id == "singer_1"
        assert line.text == "测试歌词"
        assert line.chars == ["测", "试", "歌", "词"]
        assert line.id is not None
        assert len(line.timetags) == 0
        assert len(line.checkpoints) == 4
    
    def test_creation_with_custom_chars(self):
        """测试使用自定义 chars 创建"""
        line = LyricLine(
            singer_id="singer_1",
            text="测试",
            chars=["测", "试"]
        )
        
        assert line.chars == ["测", "试"]
    
    def test_default_checkpoints_created(self):
        """测试默认节奏点配置自动创建"""
        line = LyricLine(
            singer_id="singer_1",
            text="ABC"
        )
        
        assert len(line.checkpoints) == 3
        
        # 检查每个字符的配置
        for i, config in enumerate(line.checkpoints):
            assert config.char_idx == i
            assert config.check_count == 1
            # 最后一个字符标记为句尾
            assert config.is_line_end == (i == 2)
    
    def test_add_timetag(self):
        """测试添加时间标签"""
        line = LyricLine(singer_id="s1", text="测试")
        
        tag = TimeTag(
            timestamp_ms=1000,
            singer_id="s1",
            char_idx=0,
            checkpoint_idx=0
        )
        line.add_timetag(tag)
        
        assert len(line.timetags) == 1
        assert line.timetags[0].timestamp_ms == 1000
    
    def test_add_timetag_sorted(self):
        """测试添加时间标签后自动排序"""
        line = LyricLine(singer_id="s1", text="测试")
        
        # 按非顺序添加
        tag2 = TimeTag(timestamp_ms=2000, singer_id="s1", char_idx=1)
        tag1 = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0)
        
        line.add_timetag(tag2)
        line.add_timetag(tag1)
        
        # 验证按时间排序
        assert line.timetags[0].timestamp_ms == 1000
        assert line.timetags[1].timestamp_ms == 2000
    
    def test_add_timetag_invalid_char_idx(self):
        """测试添加时间标签到无效字符索引应该抛出 ValidationError"""
        line = LyricLine(singer_id="s1", text="测试")  # 2 个字符
        
        with pytest.raises(ValidationError):
            tag = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=5)
            line.add_timetag(tag)
    
    def test_remove_timetag(self):
        """测试移除时间标签"""
        line = LyricLine(singer_id="s1", text="测试")
        
        tag = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0)
        line.add_timetag(tag)
        
        line.remove_timetag(tag)
        assert len(line.timetags) == 0
    
    def test_remove_nonexistent_timetag_raises_error(self):
        """测试移除非存在的时间标签应该抛出 DomainError"""
        line = LyricLine(singer_id="s1", text="测试")
        
        tag = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0)
        # 不添加到 line
        
        with pytest.raises(DomainError):
            line.remove_timetag(tag)
    
    def test_get_checkpoint_config(self):
        """测试获取节奏点配置"""
        line = LyricLine(singer_id="s1", text="ABC")
        
        config = line.get_checkpoint_config(1)
        
        assert config.char_idx == 1
        assert config.check_count == 1
    
    def test_set_checkpoint_config(self):
        """测试设置节奏点配置"""
        line = LyricLine(singer_id="s1", text="ABC")
        
        new_config = CheckpointConfig(char_idx=0, check_count=2)
        line.set_checkpoint_config(new_config)
        
        assert line.checkpoints[0].check_count == 2
    
    def test_add_ruby(self):
        """测试添加注音"""
        line = LyricLine(singer_id="s1", text="赤い花")
        
        ruby = Ruby(text="あか", start_idx=0, end_idx=1)
        line.add_ruby(ruby)
        
        assert len(line.rubies) == 1
    
    def test_get_ruby_for_char(self):
        """测试获取字符的注音"""
        line = LyricLine(singer_id="s1", text="赤い花")
        
        ruby = Ruby(text="あか", start_idx=0, end_idx=1)
        line.add_ruby(ruby)
        
        assert line.get_ruby_for_char(0) == ruby
        assert line.get_ruby_for_char(1) is None
    
    def test_add_overlapping_ruby_raises_error(self):
        """测试添加重叠的注音应该抛出 ValidationError"""
        line = LyricLine(singer_id="s1", text="赤い花")
        
        ruby1 = Ruby(text="あか", start_idx=0, end_idx=2)
        line.add_ruby(ruby1)
        
        # 重叠的注音
        ruby2 = Ruby(text="かい", start_idx=1, end_idx=3)
        with pytest.raises(ValidationError):
            line.add_ruby(ruby2)
    
    def test_is_fully_timed(self):
        """测试检查是否完全打轴"""
        line = LyricLine(singer_id="s1", text="ABC")
        
        # 初始状态：未打轴
        assert line.is_fully_timed() is False
        
        # 添加所有需要的时间标签
        for i in range(3):
            tag = TimeTag(timestamp_ms=1000 + i * 500, singer_id="s1", char_idx=i)
            line.add_timetag(tag)
        
        assert line.is_fully_timed() is True
    
    def test_get_timing_progress(self):
        """测试获取打轴进度"""
        line = LyricLine(singer_id="s1", text="ABC")
        
        completed, total = line.get_timing_progress()
        assert completed == 0
        assert total == 3  # 3 个字符，每个 1 个节奏点
        
        # 添加一个时间标签
        tag = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0)
        line.add_timetag(tag)
        
        completed, total = line.get_timing_progress()
        assert completed == 1
        assert total == 3
    
    def test_get_timetags_for_char(self):
        """测试获取字符的所有时间标签"""
        line = LyricLine(singer_id="s1", text="AB")
        
        # 为第一个字符设置 2 个节奏点（连打场景）
        line.set_checkpoint_config(CheckpointConfig(char_idx=0, check_count=2))
        
        # 为第一个字符添加两个时间标签
        tag1 = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0, checkpoint_idx=0)
        tag2 = TimeTag(timestamp_ms=1500, singer_id="s1", char_idx=0, checkpoint_idx=1)
        line.add_timetag(tag1)
        line.add_timetag(tag2)
        
        tags = line.get_timetags_for_char(0)
        assert len(tags) == 2
        assert tags[0].checkpoint_idx == 0
        assert tags[1].checkpoint_idx == 1
    
    def test_invalid_empty_text(self):
        """测试创建时文本为空应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            LyricLine(singer_id="s1", text="")
    
    def test_invalid_empty_singer_id(self):
        """测试创建时 singer_id 为空应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            LyricLine(singer_id="", text="测试")
    
    def test_invalid_chars_text_mismatch(self):
        """测试 chars 和 text 不一致应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            LyricLine(
                singer_id="s1",
                text="测试",
                chars=["测", "试", "不", "一", "致"]  # 5 个字符
            )
