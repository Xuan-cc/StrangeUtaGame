import pytest
from strange_uta_game.backend.domain import (
    Character,
    Ruby,
    RubyPart,
    ValidationError,
    TimeTagType,
)


class TestCharacter:
    def test_minimal_creation(self):
        ch = Character(char="赤")
        assert ch.char == "赤"
        assert ch.check_count == 1
        assert ch.timestamps == []
        assert ch.sentence_end_ts is None
        assert not ch.linked_to_next
        assert not ch.is_line_end
        assert not ch.is_rest
        assert ch.singer_id == ""

    def test_full_creation(self):
        ruby = Ruby(parts=[RubyPart(text="あか")])
        ch = Character(
            char="赤",
            ruby=ruby,
            check_count=2,
            timestamps=[1000, 1500],
            sentence_end_ts=2000,
            linked_to_next=True,
            is_line_end=True,
            is_sentence_end=True,
            is_rest=False,
            singer_id="s1",
        )
        assert ch.char == "赤"
        assert ch.ruby == ruby
        assert ch.check_count == 2
        assert ch.timestamps == [1000, 1500]
        assert ch.sentence_end_ts == 2000
        assert ch.linked_to_next is True
        assert ch.is_line_end is True
        assert ch.singer_id == "s1"

    def test_validation_empty_char(self):
        with pytest.raises(ValidationError, match="字符不能为空"):
            Character(char="")

    def test_validation_negative_check_count(self):
        with pytest.raises(ValidationError, match="节奏点数量不能为负数"):
            Character(char="a", check_count=-1)

    def test_sentence_end_allows_zero_checkpoints(self):
        """句尾字符允许 check_count=0（B7-7a：句尾无需普通节奏点）"""
        ch = Character(char="a", check_count=0, is_sentence_end=True)
        assert ch.is_sentence_end is True
        assert ch.check_count == 0

    def test_push_to_ruby(self):
        ruby = Ruby(parts=[RubyPart(text="あか")])
        ch = Character(
            char="赤",
            ruby=ruby,
            timestamps=[1000],
            sentence_end_ts=1500,
            is_sentence_end=True,
            singer_id="s1",
        )
        # Manually verify push_to_ruby (it's often called by other methods)
        ch.push_to_ruby()
        assert ruby.timestamps == [1000, 1500]
        assert ruby.singer_id == "s1"

    def test_add_timestamp_sorts(self):
        ch = Character(char="a")
        ch.add_timestamp(2000)
        ch.add_timestamp(1000)
        assert ch.timestamps == [1000, 2000]

    def test_add_timestamp_at_index(self):
        ch = Character(char="a", check_count=3)
        ch.add_timestamp(1000, checkpoint_idx=0)
        ch.add_timestamp(3000, checkpoint_idx=2)
        assert ch.timestamps == [1000, 0, 3000]

    def test_remove_timestamp_at(self):
        ch = Character(char="a", check_count=3, timestamps=[1000, 2000, 3000])
        removed = ch.remove_timestamp_at(1)
        assert removed == 2000
        assert ch.timestamps == [1000, 3000]

        assert ch.remove_timestamp_at(10) is None

    def test_clear_timestamps(self):
        ruby = Ruby(parts=[RubyPart(text="a")])
        ch = Character(
            char="a",
            ruby=ruby,
            timestamps=[1000, 2000],
            sentence_end_ts=3000,
            is_sentence_end=True,
        )
        ch.clear_timestamps()
        assert ch.timestamps == []
        assert ch.sentence_end_ts is None
        assert ruby.timestamps == []

    def test_set_ruby_pushes(self):
        ch = Character(
            char="赤",
            timestamps=[1000],
            sentence_end_ts=1500,
            is_sentence_end=True,
            singer_id="s1",
        )
        ruby = Ruby(parts=[RubyPart(text="あか")])
        ch.set_ruby(ruby)
        assert ch.ruby == ruby
        assert ruby.timestamps == [1000, 1500]
        assert ruby.singer_id == "s1"

    def test_is_fully_timed(self):
        ch = Character(char="a", check_count=2, is_sentence_end=True)
        assert not ch.is_fully_timed
        ch.add_timestamp(1000)
        assert not ch.is_fully_timed
        ch.add_timestamp(2000)
        assert not ch.is_fully_timed
        ch.set_sentence_end_ts(2500)
        assert ch.is_fully_timed

    def test_total_timing_points_and_all_timestamps(self):
        ch = Character(
            char="a",
            check_count=2,
            timestamps=[1000, 2000],
            sentence_end_ts=2500,
            is_sentence_end=True,
        )
        assert ch.total_timing_points == 3
        assert ch.all_timestamps == [1000, 2000, 2500]

    def test_set_and_clear_sentence_end_ts(self):
        ch = Character(char="a", check_count=1, is_sentence_end=True)
        ch.set_sentence_end_ts(1234)
        assert ch.sentence_end_ts == 1234
        ch.clear_sentence_end_ts()
        assert ch.sentence_end_ts is None

    def test_get_tag_type(self):
        # Normal char
        ch = Character(char="a", check_count=2, is_line_end=True)
        assert ch.get_tag_type(0) == TimeTagType.CHAR_START
        assert ch.get_tag_type(1) == TimeTagType.LINE_END

        ch2 = Character(char="b", check_count=3, is_line_end=False)
        assert ch2.get_tag_type(0) == TimeTagType.CHAR_START
        assert ch2.get_tag_type(1) == TimeTagType.CHAR_MIDDLE
        assert ch2.get_tag_type(2) == TimeTagType.CHAR_MIDDLE

        ch3 = Character(char="c", check_count=1, is_sentence_end=True)
        assert ch3.get_tag_type(0) == TimeTagType.CHAR_START
        assert ch3.get_tag_type(1) == TimeTagType.SENTENCE_END

        # Rest char
        ch_rest = Character(char=" ", is_rest=True)
        assert ch_rest.get_tag_type(0) == TimeTagType.REST
