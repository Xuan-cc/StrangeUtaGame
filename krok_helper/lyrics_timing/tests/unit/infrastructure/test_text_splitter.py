"""文本拆分器测试。"""

import pytest
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    CharType,
    get_char_type,
    JapaneseSplitter,
    EnglishSplitter,
    AutoSplitter,
    split_text,
    SplitConfig,
)


class TestGetCharType:
    """测试字符类型识别"""

    def test_kanji(self):
        assert get_char_type("赤") == CharType.KANJI
        assert get_char_type("日") == CharType.KANJI
        assert get_char_type("本") == CharType.KANJI

    def test_hiragana(self):
        assert get_char_type("あ") == CharType.HIRAGANA
        assert get_char_type("い") == CharType.HIRAGANA

    def test_katakana(self):
        assert get_char_type("ア") == CharType.KATAKANA
        assert get_char_type("イ") == CharType.KATAKANA

    def test_long_vowel(self):
        assert get_char_type("ー") == CharType.LONG_VOWEL

    def test_sokuon(self):
        assert get_char_type("っ") == CharType.SOKUON
        assert get_char_type("ッ") == CharType.SOKUON

    def test_alphabet(self):
        assert get_char_type("A") == CharType.ALPHABET
        assert get_char_type("z") == CharType.ALPHABET

    def test_number(self):
        assert get_char_type("1") == CharType.NUMBER


class TestJapaneseSplitter:
    """测试日文拆分器"""

    def test_split_simple_japanese(self):
        splitter = JapaneseSplitter()
        result = splitter.split("赤い花")
        assert result == ["赤", "い", "花"]

    def test_split_with_long_vowel(self):
        splitter = JapaneseSplitter(split_long_vowel=True)
        result = splitter.split("さよーなら")
        assert "ー" in result

    def test_split_with_sokuon(self):
        splitter = JapaneseSplitter(split_sokuon=True)
        result = splitter.split("こっち")
        assert "っ" in result

    def test_merge_spaces(self):
        splitter = JapaneseSplitter(merge_spaces=True)
        result = splitter.split("赤い  花")  # 两个空格
        assert result == ["赤", "い", " ", "花"]


class TestEnglishSplitter:
    """测试英文拆分器"""

    def test_split_simple_english(self):
        splitter = EnglishSplitter()
        result = splitter.split("Hello")
        assert result == ["H", "e", "l", "l", "o"]

    def test_merge_spaces(self):
        splitter = EnglishSplitter(merge_spaces=True)
        result = splitter.split("Hello  World")
        assert "  " not in result
        assert " " in result


class TestAutoSplitter:
    """测试自动拆分器"""

    def test_detect_japanese(self):
        splitter = AutoSplitter()
        lang = splitter.detect_language("赤い花")
        assert lang == "ja"

    def test_detect_english(self):
        splitter = AutoSplitter()
        lang = splitter.detect_language("Hello World")
        assert lang == "en"

    def test_split_japanese(self):
        splitter = AutoSplitter()
        result = splitter.split("赤い花")
        assert result == ["赤", "い", "花"]

    def test_split_english(self):
        splitter = AutoSplitter()
        result = splitter.split("Hello")
        assert result == ["H", "e", "l", "l", "o"]


class TestSplitText:
    """测试 split_text 函数"""

    def test_split_with_check_count(self):
        config = SplitConfig()
        chars, counts = split_text("赤い花", config)
        assert len(chars) == len(counts)
        assert chars == ["赤", "い", "花"]
