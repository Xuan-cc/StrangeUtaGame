"""Ruby 数据结构测试。"""

import pytest
from strange_uta_game.backend.domain import Ruby, ValidationError


class TestRuby:
    """Ruby 数据结构测试类"""

    def test_creation_with_valid_values(self):
        """测试使用有效值创建 Ruby"""
        ruby = Ruby(text="あか")

        assert ruby.text == "あか"
        assert ruby.timestamps == []
        assert ruby.singer_id == ""

    def test_full_creation(self):
        """测试使用完整参数创建 Ruby"""
        timestamps = [1000, 1500]
        ruby = Ruby(text="あか", timestamps=timestamps, singer_id="s1")

        assert ruby.text == "あか"
        assert ruby.timestamps == timestamps
        assert ruby.singer_id == "s1"

    def test_mutability(self):
        """测试 Ruby 是可变的"""
        ruby = Ruby(text="あか")
        ruby.text = "あお"
        ruby.timestamps = [2000]
        ruby.singer_id = "s2"

        assert ruby.text == "あお"
        assert ruby.timestamps == [2000]
        assert ruby.singer_id == "s2"

    def test_invalid_empty_text(self):
        """测试空注音文本应该抛出 ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            Ruby(text="")

        assert "注音文本不能为空" in str(exc_info.value)
