"""TimeTag 值对象测试。"""

import pytest
from strange_uta_game.backend.domain import TimeTag, TimeTagType, ValidationError


class TestTimeTag:
    """TimeTag 值对象测试类"""
    
    def test_creation_with_valid_values(self):
        """测试使用有效值创建 TimeTag"""
        tag = TimeTag(
            timestamp_ms=10000,
            singer_id="singer_1",
            char_idx=0,
            checkpoint_idx=0,
            tag_type=TimeTagType.CHAR_START
        )
        
        assert tag.timestamp_ms == 10000
        assert tag.singer_id == "singer_1"
        assert tag.char_idx == 0
        assert tag.checkpoint_idx == 0
        assert tag.tag_type == TimeTagType.CHAR_START
    
    def test_default_values(self):
        """测试默认值"""
        tag = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0)
        
        assert tag.checkpoint_idx == 0
        assert tag.tag_type == TimeTagType.CHAR_START
    
    def test_equality_same_values(self):
        """测试相同值的 TimeTag 相等"""
        tag1 = TimeTag(
            timestamp_ms=1000,
            singer_id="s1",
            char_idx=0,
            checkpoint_idx=0
        )
        tag2 = TimeTag(
            timestamp_ms=1000,
            singer_id="s1",
            char_idx=0,
            checkpoint_idx=0
        )
        
        assert tag1 == tag2
    
    def test_inequality_different_singer(self):
        """测试不同演唱者的 TimeTag 不相等"""
        tag1 = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0)
        tag2 = TimeTag(timestamp_ms=1000, singer_id="s2", char_idx=0)
        
        assert tag1 != tag2
    
    def test_invalid_empty_singer_id(self):
        """测试空 singer_id 应该抛出 ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            TimeTag(timestamp_ms=1000, singer_id="", char_idx=0)
        
        assert "不能为空" in str(exc_info.value)
    
    def test_invalid_negative_timestamp(self):
        """测试负时间戳应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            TimeTag(timestamp_ms=-100, singer_id="s1", char_idx=0)
    
    def test_invalid_negative_char_idx(self):
        """测试负字符索引应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=-1)
    
    def test_all_tag_types(self):
        """测试所有时间标签类型"""
        types = [
            TimeTagType.CHAR_START,
            TimeTagType.CHAR_MIDDLE,
            TimeTagType.LINE_END,
            TimeTagType.REST,
        ]
        
        for tag_type in types:
            tag = TimeTag(
                timestamp_ms=1000,
                singer_id="s1",
                char_idx=0,
                tag_type=tag_type
            )
            assert tag.tag_type == tag_type
    
    def test_immutability(self):
        """测试 TimeTag 不可变性"""
        tag = TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0)
        
        with pytest.raises(AttributeError):
            tag.timestamp_ms = 2000
