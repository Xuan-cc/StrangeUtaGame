"""inline_format 模块测试。"""

import pytest
from strange_uta_game.backend.domain.models import (
    Character,
    Ruby,
    TimeTagType,
)
from strange_uta_game.backend.domain.entities import Sentence
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    format_timestamp,
    parse_timestamp,
    encode_check_n,
    decode_check_n,
    split_into_moras,
    split_ruby_for_checkpoints,
    to_inline_text,
    from_inline_text,
    sentences_to_inline_text,
    sentences_from_inline_text,
)


# ──────────────────────────────────────────────
# 时间戳
# ──────────────────────────────────────────────


class TestTimestamp:
    def test_format_zero(self):
        assert format_timestamp(0) == "00:00:00"

    def test_format_basic(self):
        assert format_timestamp(14640) == "00:14:64"

    def test_format_minutes(self):
        # 3 min 25 sec 80 centis = 205800 ms
        assert format_timestamp(205800) == "03:25:80"

    def test_parse_basic(self):
        assert parse_timestamp("00:14:64") == 14640

    def test_parse_zero(self):
        assert parse_timestamp("00:00:00") == 0

    def test_parse_minutes(self):
        assert parse_timestamp("03:25:80") == 205800

    def test_roundtrip(self):
        for ms in [0, 10, 100, 1000, 14640, 15610, 60000, 205800]:
            assert parse_timestamp(format_timestamp(ms)) == ms

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            parse_timestamp("invalid")


# ──────────────────────────────────────────────
# N 编码
# ──────────────────────────────────────────────


class TestCheckN:
    def test_encode_normal(self):
        assert encode_check_n(1, False) == "1"
        assert encode_check_n(2, False) == "2"

    def test_encode_line_end(self):
        assert encode_check_n(1, True) == "10"
        assert encode_check_n(2, True) == "20"

    def test_encode_sentence_end(self):
        assert encode_check_n(1, False, True) == "1e"
        assert encode_check_n(2, False, True) == "2e"

    def test_encode_line_end_and_sentence_end(self):
        assert encode_check_n(1, True, True) == "10e"

    def test_decode_normal(self):
        assert decode_check_n("1") == (1, False, False)
        assert decode_check_n("2") == (2, False, False)

    def test_decode_line_end(self):
        assert decode_check_n("10") == (1, True, False)
        assert decode_check_n("20") == (2, True, False)

    def test_decode_sentence_end(self):
        assert decode_check_n("1e") == (1, False, True)
        assert decode_check_n("2e") == (2, False, True)

    def test_decode_line_end_and_sentence_end(self):
        assert decode_check_n("10e") == (1, True, True)

    def test_roundtrip(self):
        for count in [1, 2, 3]:
            for le in [True, False]:
                for se in [True, False]:
                    encoded = encode_check_n(count, le, se)
                    assert decode_check_n(encoded) == (count, le, se)


# ──────────────────────────────────────────────
# Mora 分割
# ──────────────────────────────────────────────


class TestMoraSplit:
    def test_basic_hiragana(self):
        assert split_into_moras("やわ") == ["や", "わ"]

    def test_small_kana(self):
        assert split_into_moras("しゃ") == ["しゃ"]

    def test_mixed(self):
        assert split_into_moras("てい") == ["て", "い"]

    def test_long_vowel(self):
        assert split_into_moras("かー") == ["かー"]

    def test_empty(self):
        assert split_into_moras("") == []

    def test_complex(self):
        # しゃてい → [しゃ, て, い]
        assert split_into_moras("しゃてい") == ["しゃ", "て", "い"]


class TestSplitRubyForCheckpoints:
    def test_single_cp(self):
        assert split_ruby_for_checkpoints("やわ", 1) == ["やわ"]

    def test_matching_moras(self):
        assert split_ruby_for_checkpoints("やわ", 2) == ["や", "わ"]

    def test_complex_moras(self):
        # しゃてい → 3 moras → 3 cps matches
        assert split_ruby_for_checkpoints("しゃてい", 3) == ["しゃ", "て", "い"]

    def test_uneven_split(self):
        # 4 chars, 2 cps → ["ab", "cd"]
        result = split_ruby_for_checkpoints("abcd", 2)
        assert result == ["ab", "cd"]


# ──────────────────────────────────────────────
# 序列化 (to_inline_text)
# ──────────────────────────────────────────────


def _make_sentence(text, singer_id="s1", characters=None):
    """辅助函数: 创建测试用 Sentence。"""
    if characters is None:
        characters = [
            Character(char=ch, check_count=1, singer_id=singer_id) for ch in text
        ]
    return Sentence(singer_id=singer_id, characters=characters)


class TestToInlineText:
    def test_simple_chars_no_timetags(self):
        sentence = _make_sentence("abc")
        result = to_inline_text(sentence)
        # 无 timetag → 时间戳默认 00:00:00
        assert "[1|00:00:00]a" in result
        assert "[1|00:00:00]b" in result
        assert "[1|00:00:00]c" in result

    def test_char_with_timetag(self):
        sentence = _make_sentence(
            "な",
            characters=[
                Character(char="な", check_count=1, timestamps=[15760], singer_id="s1")
            ],
        )
        result = to_inline_text(sentence)
        assert result == "[1|00:15:76]な"

    def test_line_end_char(self):
        sentence = _make_sentence(
            "x",
            characters=[
                Character(
                    char="x",
                    check_count=1,
                    timestamps=[10000],
                    is_line_end=True,
                    singer_id="s1",
                )
            ],
        )
        result = to_inline_text(sentence)
        assert result == "[10|00:10:00]x"

    def test_sentence_end_char(self):
        sentence = _make_sentence(
            "x",
            characters=[
                Character(
                    char="x",
                    check_count=1,
                    timestamps=[10000],
                    sentence_end_ts=12000,
                    is_sentence_end=True,
                    singer_id="s1",
                )
            ],
        )
        result = to_inline_text(sentence)
        assert result == "[1e|00:10:00][00:12:00]x"

    def test_multi_checkpoint_char(self):
        sentence = _make_sentence(
            "x",
            characters=[
                Character(
                    char="x", check_count=2, timestamps=[1000, 2000], singer_id="s1"
                )
            ],
        )
        result = to_inline_text(sentence)
        assert "[2|00:01:00]" in result
        assert "[00:02:00]" in result

    def test_ruby_single_char(self):
        sentence = _make_sentence(
            "柔",
            characters=[
                Character(
                    char="柔",
                    check_count=2,
                    timestamps=[14640, 15610],
                    ruby=Ruby(text="やわ"),
                    singer_id="s1",
                )
            ],
        )
        result = to_inline_text(sentence)
        assert result == "{柔|[2|00:14:64]や[00:15:61]わ}"

    def test_rest_char(self):
        sentence = _make_sentence(
            "▨",
            characters=[
                Character(
                    char="▨",
                    check_count=1,
                    timestamps=[16500],
                    is_line_end=True,
                    is_rest=True,
                    singer_id="s1",
                )
            ],
        )
        result = to_inline_text(sentence)
        assert result == "[10|00:16:50]▨"


# ──────────────────────────────────────────────
# 反序列化 (from_inline_text)
# ──────────────────────────────────────────────


class TestFromInlineText:
    def test_simple_char(self):
        sentence = from_inline_text("[1|00:15:76]な", singer_id="s1")
        assert sentence.chars == ["な"]
        assert sentence.text == "な"
        assert len(sentence.characters) == 1
        assert sentence.characters[0].check_count == 1
        assert sentence.characters[0].is_line_end is False
        assert len(sentence.characters[0].timestamps) == 1
        assert sentence.characters[0].timestamps[0] == 15760

    def test_line_end(self):
        sentence = from_inline_text("[10|00:10:00]x", singer_id="s1")
        assert sentence.characters[0].is_line_end is True
        assert sentence.characters[0].is_sentence_end is False
        assert sentence.characters[0].check_count == 1

    def test_sentence_end(self):
        sentence = from_inline_text("[1e|00:10:00][00:12:00]x", singer_id="s1")
        assert sentence.characters[0].is_sentence_end is True
        assert sentence.characters[0].is_line_end is False
        assert sentence.characters[0].check_count == 1
        assert sentence.characters[0].timestamps == [10000]
        assert sentence.characters[0].sentence_end_ts == 12000

    def test_rest_char(self):
        sentence = from_inline_text("[10|00:16:50]▨", singer_id="s1")
        assert sentence.chars == ["▨"]
        assert sentence.characters[0].is_rest is True
        assert sentence.characters[0].is_line_end is True

    def test_ruby_group(self):
        sentence = from_inline_text("{柔|[2|00:14:64]や[00:15:61]わ}", singer_id="s1")
        assert sentence.chars == ["柔"]
        assert sentence.text == "柔"
        assert len(sentence.rubies) == 1
        assert sentence.rubies[0].text == "やわ"
        # sentence.rubies is computed from characters, so we check text and presence
        assert sentence.characters[0].ruby.text == "やわ"
        assert sentence.characters[0].check_count == 2
        assert len(sentence.characters[0].timestamps) == 2
        assert sentence.characters[0].timestamps[0] == 14640
        assert sentence.characters[0].timestamps[1] == 15610

    def test_multi_char_ruby(self):
        text = "{射程|[1|00:16:76]しゃ＋[2|00:16:89]て[00:17:19]い}"
        sentence = from_inline_text(text, singer_id="s1")
        assert sentence.chars == ["射", "程"]
        assert sentence.text == "射程"
        # 逐字模型中，"{射程|...}" 组会被拆分为逐字的 Ruby
        assert len(sentence.rubies) == 2
        assert sentence.rubies[0].text == "しゃ"
        assert sentence.rubies[1].text == "てい"
        # 射: 1 cp, 程: 2 cps
        assert sentence.characters[0].check_count == 1
        assert sentence.characters[1].check_count == 2
        # 3 timestamps total
        assert sum(len(c.all_timestamps) for c in sentence.characters) == 3

    def test_mixed_line(self):
        """测试用户给出的完整示例格式。"""
        text = (
            "{柔|[2|00:14:64]や[00:15:61]わ}"
            "[1|00:15:76]な"
            "[10|00:16:50]▨"
            "{射程|[1|00:16:76]しゃ＋[2|00:16:89]て[00:17:19]い}"
        )
        sentence = from_inline_text(text, singer_id="s1")
        assert sentence.chars == ["柔", "な", "▨", "射", "程"]
        # 柔(1), 射(1), 程(1) -> 3 rubies total
        assert len(sentence.rubies) == 3
        assert sentence.rubies[0].text == "やわ"
        assert sentence.rubies[1].text == "しゃ"
        assert sentence.rubies[2].text == "てい"
        # check_counts: 柔(2), な(1), ▨(1,le), 射(1), 程(2)
        assert [c.check_count for c in sentence.characters] == [2, 1, 1, 1, 2]
        assert sentence.characters[2].is_line_end is True
        assert sentence.characters[2].is_rest is True
        # 7 timestamps total
        assert sum(len(c.all_timestamps) for c in sentence.characters) == 7


# ──────────────────────────────────────────────
# 往返 (roundtrip)
# ──────────────────────────────────────────────


class TestRoundtrip:
    def test_simple_roundtrip(self):
        original = _make_sentence(
            "なは",
            characters=[
                Character(char="な", check_count=1, timestamps=[1000], singer_id="s1"),
                Character(
                    char="は",
                    check_count=1,
                    timestamps=[2000],
                    is_line_end=True,
                    singer_id="s1",
                ),
            ],
        )
        text = to_inline_text(original)
        restored = from_inline_text(text, singer_id="s1")
        assert restored.chars == original.chars
        assert len(restored.characters) == len(original.characters)
        for r, o in zip(restored.characters, original.characters):
            assert r.check_count == o.check_count
            assert r.is_line_end == o.is_line_end
            assert r.is_sentence_end == o.is_sentence_end
            assert r.timestamps == o.timestamps

    def test_sentence_end_roundtrip(self):
        original = _make_sentence(
            "あい",
            characters=[
                Character(
                    char="あ",
                    check_count=1,
                    timestamps=[1000],
                    sentence_end_ts=1500,
                    is_sentence_end=True,
                    singer_id="s1",
                ),
                Character(
                    char="い",
                    check_count=1,
                    timestamps=[2000],
                    is_line_end=True,
                    singer_id="s1",
                ),
            ],
        )
        text = to_inline_text(original)
        restored = from_inline_text(text, singer_id="s1")
        assert restored.characters[0].is_sentence_end is True
        assert restored.characters[0].is_line_end is False
        assert restored.characters[0].sentence_end_ts == 1500
        assert restored.characters[1].is_line_end is True

    def test_ruby_roundtrip(self):
        original = _make_sentence(
            "柔な",
            characters=[
                Character(
                    char="柔",
                    check_count=2,
                    timestamps=[14640, 15610],
                    ruby=Ruby(text="やわ"),
                    singer_id="s1",
                ),
                Character(
                    char="な",
                    check_count=1,
                    timestamps=[15760],
                    is_line_end=True,
                    singer_id="s1",
                ),
            ],
        )
        text = to_inline_text(original)
        restored = from_inline_text(text, singer_id="s1")
        assert restored.chars == original.chars
        assert len(restored.rubies) == 1
        assert restored.rubies[0].text == "やわ"
        assert sum(len(c.all_timestamps) for c in restored.characters) == 3

    def test_multiline_roundtrip(self):
        sentences = [
            _make_sentence(
                "あ",
                characters=[
                    Character(
                        char="あ", check_count=1, timestamps=[1000], singer_id="s1"
                    )
                ],
            ),
            _make_sentence(
                "い",
                characters=[
                    Character(
                        char="い", check_count=1, timestamps=[2000], singer_id="s1"
                    )
                ],
            ),
        ]
        text = sentences_to_inline_text(sentences)
        restored = sentences_from_inline_text(text, singer_id="s1")
        assert len(restored) == 2
        assert restored[0].chars == ["あ"]
        assert restored[1].chars == ["い"]
