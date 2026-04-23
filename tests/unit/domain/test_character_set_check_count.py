"""Character.set_check_count 与 push_to_ruby 残留修复的测试。

覆盖：
- 缩小 check_count 时同步 trim timestamps
- 缩小 check_count 时合并 ruby.parts 尾段（保数据，不丢失）
- 增大 check_count 不动 timestamps / 不动 ruby
- check_count == 0 且 ruby 非空且 !force → 抛 RubyMoraDegradeError
- force=True 时退化为 Nicokara 无 mora 格式（保留 ruby.parts）
- push_to_ruby 清除超出 timestamps 长度的残留 offset_ms
"""

import pytest

from strange_uta_game.backend.domain import (
    Character,
    Ruby,
    RubyPart,
    RubyMoraDegradeError,
)


class TestSetCheckCountShrink:
    def test_shrink_trims_timestamps(self):
        ch = Character(char="春", check_count=3, timestamps=[1000, 1200, 1400])
        ch.set_check_count(2)
        assert ch.check_count == 2
        assert ch.timestamps == [1000, 1200]

    def test_shrink_merges_ruby_parts_tail(self):
        """缩小时尾段 parts 应合并到最后一个保留的 part 上，文本不丢。"""
        ruby = Ruby(
            parts=[
                RubyPart(text="は"),
                RubyPart(text="る"),
                RubyPart(text="の"),
            ]
        )
        ch = Character(
            char="春の",
            check_count=3,
            timestamps=[1000, 1200, 1400],
            ruby=ruby,
        )
        ch.set_check_count(2)
        assert ch.check_count == 2
        assert ch.ruby is not None
        assert len(ch.ruby.parts) == 2
        # 尾段 "の" 合并到第二个 part：は / るの
        assert ch.ruby.parts[0].text == "は"
        assert ch.ruby.parts[1].text == "るの"

    def test_shrink_to_one_merges_all_tail(self):
        ruby = Ruby(parts=[RubyPart(text="あ"), RubyPart(text="か"), RubyPart(text="い")])
        ch = Character(char="赤い", check_count=3, ruby=ruby)
        ch.set_check_count(1)
        assert ch.check_count == 1
        assert ch.ruby is not None
        assert len(ch.ruby.parts) == 1
        assert ch.ruby.parts[0].text == "あかい"


class TestSetCheckCountGrow:
    def test_grow_keeps_timestamps_unchanged(self):
        ch = Character(char="春", check_count=1, timestamps=[1000])
        ch.set_check_count(3)
        assert ch.check_count == 3
        # timestamps 不主动扩，由后续打轴/auto_check 填充
        assert ch.timestamps == [1000]

    def test_grow_keeps_ruby_unchanged(self):
        ruby = Ruby(parts=[RubyPart(text="はる")])
        ch = Character(char="春", check_count=1, ruby=ruby)
        ch.set_check_count(3)
        assert ch.check_count == 3
        assert ch.ruby is not None
        # ruby 在增大时不主动改写，由调用方负责后续 set_ruby
        assert len(ch.ruby.parts) == 1
        assert ch.ruby.parts[0].text == "はる"


class TestSetCheckCountZero:
    def test_zero_with_ruby_raises_without_force(self):
        ruby = Ruby(parts=[RubyPart(text="はる")])
        ch = Character(char="春", check_count=1, timestamps=[1000], ruby=ruby)
        with pytest.raises(RubyMoraDegradeError):
            ch.set_check_count(0)
        # 失败时状态不变
        assert ch.check_count == 1
        assert ch.timestamps == [1000]
        assert ch.ruby is not None

    def test_zero_with_ruby_force_degrades_to_nicokara(self):
        """force=True：退化为 Nicokara 无 mora 格式，ruby.parts 完整保留。"""
        ruby = Ruby(
            parts=[RubyPart(text="は", offset_ms=0), RubyPart(text="る", offset_ms=200)]
        )
        ch = Character(char="春", check_count=2, timestamps=[1000, 1200], ruby=ruby)
        ch.set_check_count(0, force=True)
        assert ch.check_count == 0
        assert ch.timestamps == []
        # Nicokara 无 mora 格式：ruby 文本保留，但 parts 长度可与 check_count 不匹配
        assert ch.ruby is not None
        assert "".join(p.text for p in ch.ruby.parts) == "はる"

    def test_zero_without_ruby_no_error(self):
        ch = Character(char="あ", check_count=1, timestamps=[1000])
        ch.set_check_count(0)
        assert ch.check_count == 0
        assert ch.timestamps == []
        assert ch.ruby is None


class TestPushToRubyResidualClear:
    def test_push_to_ruby_clears_orphan_offsets(self):
        """timestamps 缩短后，push_to_ruby 必须清掉超出范围 part 的 offset_ms 残留。"""
        ruby = Ruby(
            parts=[
                RubyPart(text="は", offset_ms=0),
                RubyPart(text="る", offset_ms=200),
                RubyPart(text="な", offset_ms=400),  # 残留：将超出 timestamps
            ]
        )
        ch = Character(char="春な", check_count=2, timestamps=[1000, 1200], ruby=ruby)
        # 模拟先 trim 再 push：手动调 push_to_ruby 检验残留清理
        ch.push_to_ruby()
        assert ch.ruby is not None
        # 前两 part offset 由 timestamps 推导
        assert ch.ruby.parts[0].offset_ms == 0
        assert ch.ruby.parts[1].offset_ms == 200
        # 第三个 part 没有对应 timestamp → offset 必须清零，不残留旧值 400
        assert ch.ruby.parts[2].offset_ms == 0

    def test_push_to_ruby_no_timestamps_all_zero(self):
        ruby = Ruby(parts=[RubyPart(text="は", offset_ms=999), RubyPart(text="る", offset_ms=888)])
        ch = Character(char="春", check_count=2, timestamps=[], ruby=ruby)
        ch.push_to_ruby()
        assert ch.ruby is not None
        assert all(p.offset_ms == 0 for p in ch.ruby.parts)
