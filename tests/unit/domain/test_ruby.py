"""Ruby 数据结构测试。"""

import pytest
from strange_uta_game.backend.domain import Ruby, RubyPart, ValidationError


class TestRuby:
    """Ruby 数据结构测试类"""

    def test_creation_with_valid_values(self):
        """测试使用有效值创建 Ruby"""
        ruby = Ruby(parts=[RubyPart(text="あか")])

        assert ruby.text == "あか"
        assert ruby.timestamps == []
        assert ruby.singer_id == ""

    def test_full_creation(self):
        """测试使用完整参数创建 Ruby"""
        timestamps = [1000, 1500]
        ruby = Ruby(
            parts=[RubyPart(text="あか")], timestamps=timestamps, singer_id="s1"
        )

        assert ruby.text == "あか"
        assert ruby.timestamps == timestamps
        assert ruby.singer_id == "s1"

    def test_mutability(self):
        """测试 Ruby 是可变的"""
        ruby = Ruby(parts=[RubyPart(text="あか")])
        ruby.parts = [RubyPart(text="あお")]
        ruby.timestamps = [2000]
        ruby.singer_id = "s2"

        assert ruby.text == "あお"
        assert ruby.timestamps == [2000]
        assert ruby.singer_id == "s2"

    def test_invalid_empty_parts(self):
        """测试空 parts 应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            Ruby(parts=[])

    def test_multi_parts(self):
        """测试多段 parts"""
        ruby = Ruby(parts=[RubyPart(text="わ"), RubyPart(text="た"), RubyPart(text="し")])
        assert ruby.text == "わたし"
        assert [p.text for p in ruby.parts] == ["わ", "た", "し"]
