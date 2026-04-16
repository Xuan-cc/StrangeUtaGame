"""Ruby 值对象测试。"""

import pytest
from strange_uta_game.backend.domain import Ruby, ValidationError


class TestRuby:
    """Ruby 值对象测试类"""
    
    def test_creation_with_valid_values(self):
        """测试使用有效值创建 Ruby"""
        ruby = Ruby(text="あか", start_idx=0, end_idx=1)
        
        assert ruby.text == "あか"
        assert ruby.start_idx == 0
        assert ruby.end_idx == 1
    
    def test_multi_char_ruby(self):
        """测试多字符注音（如「昨日」→「きのう」）"""
        ruby = Ruby(text="きのう", start_idx=0, end_idx=2)
        
        assert ruby.text == "きのう"
        assert ruby.start_idx == 0
        assert ruby.end_idx == 2
    
    def test_equality_same_values(self):
        """测试相同值的 Ruby 相等"""
        ruby1 = Ruby(text="あか", start_idx=0, end_idx=1)
        ruby2 = Ruby(text="あか", start_idx=0, end_idx=1)
        
        assert ruby1 == ruby2
    
    def test_covers_char_single(self):
        """测试覆盖单个字符"""
        ruby = Ruby(text="あか", start_idx=0, end_idx=1)
        
        assert ruby.covers_char(0)
        assert not ruby.covers_char(1)
        assert not ruby.covers_char(-1)
    
    def test_covers_char_range(self):
        """测试覆盖字符范围"""
        ruby = Ruby(text="きのう", start_idx=0, end_idx=3)
        
        assert ruby.covers_char(0)
        assert ruby.covers_char(1)
        assert ruby.covers_char(2)
        assert not ruby.covers_char(3)
    
    def test_invalid_empty_text(self):
        """测试空注音文本应该抛出 ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            Ruby(text="", start_idx=0, end_idx=1)
        
        assert "不能为空" in str(exc_info.value)
    
    def test_invalid_negative_start(self):
        """测试负起始索引应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            Ruby(text="あか", start_idx=-1, end_idx=1)
    
    def test_invalid_negative_end(self):
        """测试负结束索引应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            Ruby(text="あか", start_idx=0, end_idx=-1)
    
    def test_invalid_start_equals_end(self):
        """测试起始等于结束索引应该抛出 ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            Ruby(text="あか", start_idx=1, end_idx=1)
        
        assert "必须小于" in str(exc_info.value)
    
    def test_invalid_start_greater_than_end(self):
        """测试起始大于结束索引应该抛出 ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            Ruby(text="あか", start_idx=2, end_idx=1)
        
        assert "必须小于" in str(exc_info.value)
    
    def test_immutability(self):
        """测试 Ruby 不可变性"""
        ruby = Ruby(text="あか", start_idx=0, end_idx=1)
        
        with pytest.raises(AttributeError):
            ruby.text = "はな"
