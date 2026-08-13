"""文本拆分器测试。"""

from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    AutoSplitter,
    CharType,
    EnglishSplitter,
    JapaneseSplitter,
    SplitConfig,
    get_char_type,
    split_text,
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

    def test_katakana_block_separators_are_symbols(self):
        # 「・」U+30FB、「゠」U+30A0 落在片假名 Unicode 块内但不表音，
        # 必须归为符号，否则补全时间戳会把 "シンフォニック・ラブ" 的中点也补轴。
        assert get_char_type("・") == CharType.SYMBOL
        assert get_char_type("゠") == CharType.SYMBOL

    def test_katakana_iteration_marks_stay_katakana(self):
        # 片假名迭字「ヽヾ」表音，仍按片假名处理（不被上面的符号特判误吞）。
        assert get_char_type("ヽ") == CharType.KATAKANA
        assert get_char_type("ヾ") == CharType.KATAKANA

    def test_sokuon(self):
        assert get_char_type("っ") == CharType.SOKUON
        assert get_char_type("ッ") == CharType.SOKUON

    def test_alphabet(self):
        assert get_char_type("A") == CharType.ALPHABET
        assert get_char_type("z") == CharType.ALPHABET

    def test_number(self):
        assert get_char_type("1") == CharType.NUMBER

    def test_full_width_space_has_distinct_type(self):
        assert get_char_type(" ") == CharType.SPACE
        assert get_char_type("\u3000") == CharType.FULL_SPACE

    def test_decorative_and_operator_symbols(self):
        # 装饰符号（♪♥★）与运算符（+ - * & ^ = ~ |）均归为符号；
        # 注音删除分类与节奏点生成无关，装饰符号一并算符号。
        for c in "♪♫♩♬♡♥★☆+-*&^=~|<>《》〈〉—·•":
            assert get_char_type(c) == CharType.SYMBOL, c


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

    def test_merge_spaces_preserves_full_width_space(self):
        splitter = JapaneseSplitter(merge_spaces=True)

        assert splitter.split("赤い\u3000花") == ["赤", "い", "\u3000", "花"]
        assert splitter.split("赤い \u3000 花") == ["赤", "い", "\u3000", "花"]


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

    def test_merge_spaces_preserves_full_width_space(self):
        splitter = EnglishSplitter(merge_spaces=True)

        assert splitter.split("Hello \u3000 World") == [*"Hello", "\u3000", *"World"]


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

    def test_mixed_consecutive_spaces_merge_to_one_full_width_space(self):
        chars, counts = split_text("magic magic! \u3000君に届くよぅに")

        assert "".join(chars) == "magic magic!\u3000君に届くよぅに"
        assert len(counts) == len(chars)
