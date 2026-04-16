"""Checkpoint 值对象测试。"""

import pytest
from strange_uta_game.backend.domain import Checkpoint, ValidationError


class TestCheckpoint:
    """Checkpoint 值对象测试类"""
    
    def test_creation_with_valid_values(self):
        """测试使用有效值创建 Checkpoint"""
        cp = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        
        assert cp.timestamp_ms == 1000
        assert cp.char_idx == 0
        assert cp.checkpoint_idx == 0
    
    def test_default_checkpoint_idx(self):
        """测试 checkpoint_idx 默认值"""
        cp = Checkpoint(timestamp_ms=1000, char_idx=0)
        
        assert cp.checkpoint_idx == 0
    
    def test_equality_same_values(self):
        """测试相同值的 Checkpoint 相等"""
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        
        assert cp1 == cp2
        assert hash(cp1) == hash(cp2)
    
    def test_inequality_different_timestamp(self):
        """测试不同时间戳的 Checkpoint 不相等"""
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=2000, char_idx=0, checkpoint_idx=0)
        
        assert cp1 != cp2
    
    def test_inequality_different_char_idx(self):
        """测试不同字符索引的 Checkpoint 不相等"""
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=1000, char_idx=1, checkpoint_idx=0)
        
        assert cp1 != cp2
    
    def test_inequality_different_checkpoint_idx(self):
        """测试不同节奏点索引的 Checkpoint 不相等"""
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=1)
        
        assert cp1 != cp2
    
    def test_invalid_negative_timestamp(self):
        """测试负时间戳应该抛出 ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            Checkpoint(timestamp_ms=-100, char_idx=0, checkpoint_idx=0)
        
        assert "不能为负数" in str(exc_info.value)
    
    def test_invalid_negative_char_idx(self):
        """测试负字符索引应该抛出 ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            Checkpoint(timestamp_ms=1000, char_idx=-1, checkpoint_idx=0)
        
        assert "不能为负数" in str(exc_info.value)
    
    def test_invalid_negative_checkpoint_idx(self):
        """测试负节奏点索引应该抛出 ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=-1)
        
        assert "不能为负数" in str(exc_info.value)
    
    def test_immutability(self):
        """测试 Checkpoint 不可变性"""
        cp = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        
        # 尝试修改应该失败
        with pytest.raises(AttributeError):
            cp.timestamp_ms = 2000
    
    def test_hash_consistency(self):
        """测试相同值的 Checkpoint 有相同的哈希值"""
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        
        # 相同值应该有相同哈希
        assert hash(cp1) == hash(cp2)
        
        # 可以用作 dict key
        d = {cp1: "test"}
        assert d[cp2] == "test"
    
    def test_use_as_dict_key(self):
        """测试 Checkpoint 可以作为字典键"""
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=2000, char_idx=1, checkpoint_idx=1)
        
        d = {cp1: "first", cp2: "second"}
        
        assert d[cp1] == "first"
        assert d[cp2] == "second"
