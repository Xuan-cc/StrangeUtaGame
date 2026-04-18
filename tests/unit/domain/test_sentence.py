import pytest
from strange_uta_game.backend.domain import Sentence, Character, Ruby, ValidationError


class TestSentence:
    def test_minimal_creation(self):
        s = Sentence(singer_id="s1")
        assert s.singer_id == "s1"
        assert s.characters == []
        assert s.id is not None

    def test_from_text(self):
        s = Sentence.from_text("测试歌词", "s1")
        assert s.text == "测试歌词"
        assert len(s.characters) == 4
        assert s.characters[0].char == "测"
        assert s.characters[0].check_count == 1
        assert not s.characters[0].is_line_end

        assert s.characters[3].char == "词"
        assert s.characters[3].check_count == 2
        assert s.characters[3].is_line_end is True

    def test_validation_empty_singer(self):
        with pytest.raises(ValidationError, match="singer_id 不能为空"):
            Sentence(singer_id="")

    def test_words_property(self):
        # "赤" (linked) + "い"
        ch1 = Character(char="赤", linked_to_next=True)
        ch2 = Character(char="い", linked_to_next=False)
        ch3 = Character(char="!")
        s = Sentence(singer_id="s1", characters=[ch1, ch2, ch3])

        words = s.words
        assert len(words) == 2
        assert words[0].text == "赤い"
        assert words[1].text == "!"

    def test_get_character(self):
        s = Sentence.from_text("abc", "s1")
        assert s.get_character(0).char == "a"
        assert s.get_character(5) is None

    def test_get_ruby_for_char(self):
        s = Sentence.from_text("abc", "s1")
        ruby = Ruby(text="test")
        s.add_ruby_to_char(1, ruby)

        assert s.get_ruby_for_char(1) == ruby
        assert s.get_ruby_for_char(0) is None

    def test_get_word_for_char(self):
        ch1 = Character(char="a", linked_to_next=True)
        ch2 = Character(char="b")
        s = Sentence(singer_id="s1", characters=[ch1, ch2])

        word = s.get_word_for_char(0)
        assert word.text == "ab"
        assert s.get_word_for_char(1) == word

    def test_timing_progress(self):
        s = Sentence.from_text("ab", "s1")
        # a: count=1, b: count=2 (total 3)
        assert s.get_timing_progress() == (0, 3)
        assert not s.is_fully_timed()

        s.characters[0].add_timestamp(1000)
        assert s.get_timing_progress() == (1, 3)

        s.characters[1].add_timestamp(2000)
        s.characters[1].add_timestamp(3000)
        assert s.get_timing_progress() == (3, 3)
        assert s.is_fully_timed()

    def test_timing_boundaries(self):
        s = Sentence.from_text("abc", "s1")
        s.characters[0].add_timestamp(1000)
        s.characters[2].add_timestamp(3000)

        assert s.timing_start_ms == 1000
        assert s.timing_end_ms == 3000
        assert s.has_timetags is True

    def test_clear_all_timestamps(self):
        s = Sentence.from_text("a", "s1")
        s.characters[0].add_timestamp(1000)
        s.clear_all_timestamps()
        assert not s.has_timetags
        assert s.characters[0].timestamps == []

    def test_ruby_management(self):
        s = Sentence.from_text("abc", "s1")
        ruby = Ruby(text="test")
        s.add_ruby_to_char(0, ruby)
        assert len(s.rubies) == 1

        removed = s.remove_ruby_from_char(0)
        assert removed == ruby
        assert len(s.rubies) == 0

        s.add_ruby_to_char(0, ruby)
        s.clear_all_rubies()
        assert len(s.rubies) == 0

    def test_add_ruby_validation(self):
        s = Sentence.from_text("a", "s1")
        s.add_ruby_to_char(0, Ruby(text="test"))
        with pytest.raises(ValidationError, match="已有注音"):
            s.add_ruby_to_char(0, Ruby(text="fail"))

        with pytest.raises(ValidationError, match="超出范围"):
            s.add_ruby_to_char(10, Ruby(text="fail"))
