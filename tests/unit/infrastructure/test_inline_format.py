"""inline_format 模块测试。"""

import pytest
from strange_uta_game.backend.domain.models import (
    CheckpointConfig,
    TimeTag,
    TimeTagType,
    Ruby,
)
from strange_uta_game.backend.domain.entities import LyricLine
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    format_timestamp,
    parse_timestamp,
    encode_check_n,
    decode_check_n,
    split_into_moras,
    split_ruby_for_checkpoints,
    to_inline_text,
    from_inline_text,
    lines_to_inline_text,
    lines_from_inline_text,
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

    def test_decode_normal(self):
        assert decode_check_n("1") == (1, False)
        assert decode_check_n("2") == (2, False)

    def test_decode_line_end(self):
        assert decode_check_n("10") == (1, True)
        assert decode_check_n("20") == (2, True)

    def test_roundtrip(self):
        for count in [1, 2, 3]:
            for le in [True, False]:
                encoded = encode_check_n(count, le)
                assert decode_check_n(encoded) == (count, le)


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


def _make_line(text, singer_id="s1", checkpoints=None, timetags=None, rubies=None):
    """辅助函数: 创建测试用 LyricLine。"""
    chars = list(text)
    if checkpoints is None:
        checkpoints = [
            CheckpointConfig(char_idx=i, check_count=1) for i in range(len(chars))
        ]
    return LyricLine(
        singer_id=singer_id,
        text=text,
        chars=chars,
        checkpoints=checkpoints,
        timetags=timetags or [],
        rubies=rubies or [],
    )


class TestToInlineText:
    def test_simple_chars_no_timetags(self):
        line = _make_line("abc")
        result = to_inline_text(line)
        # 无 timetag → 时间戳默认 00:00:00
        assert "[1|00:00:00]a" in result
        assert "[1|00:00:00]b" in result
        assert "[1|00:00:00]c" in result

    def test_char_with_timetag(self):
        line = _make_line(
            "な",
            checkpoints=[CheckpointConfig(char_idx=0, check_count=1)],
            timetags=[
                TimeTag(
                    timestamp_ms=15760, singer_id="s1", char_idx=0, checkpoint_idx=0
                )
            ],
        )
        result = to_inline_text(line)
        assert result == "[1|00:15:76]な"

    def test_line_end_char(self):
        line = _make_line(
            "x",
            checkpoints=[CheckpointConfig(char_idx=0, check_count=1, is_line_end=True)],
            timetags=[
                TimeTag(
                    timestamp_ms=10000, singer_id="s1", char_idx=0, checkpoint_idx=0
                )
            ],
        )
        result = to_inline_text(line)
        assert result == "[10|00:10:00]x"

    def test_multi_checkpoint_char(self):
        line = _make_line(
            "x",
            checkpoints=[CheckpointConfig(char_idx=0, check_count=2)],
            timetags=[
                TimeTag(
                    timestamp_ms=1000, singer_id="s1", char_idx=0, checkpoint_idx=0
                ),
                TimeTag(
                    timestamp_ms=2000, singer_id="s1", char_idx=0, checkpoint_idx=1
                ),
            ],
        )
        result = to_inline_text(line)
        assert "[2|00:01:00]" in result
        assert "[00:02:00]" in result

    def test_ruby_single_char(self):
        line = _make_line(
            "柔",
            checkpoints=[CheckpointConfig(char_idx=0, check_count=2)],
            timetags=[
                TimeTag(
                    timestamp_ms=14640, singer_id="s1", char_idx=0, checkpoint_idx=0
                ),
                TimeTag(
                    timestamp_ms=15610, singer_id="s1", char_idx=0, checkpoint_idx=1
                ),
            ],
            rubies=[Ruby(text="やわ", start_idx=0, end_idx=1)],
        )
        result = to_inline_text(line)
        assert result == "{柔|[2|00:14:64]や[00:15:61]わ}"

    def test_rest_char(self):
        line = _make_line(
            "▨",
            checkpoints=[
                CheckpointConfig(
                    char_idx=0, check_count=1, is_line_end=True, is_rest=True
                )
            ],
            timetags=[
                TimeTag(
                    timestamp_ms=16500, singer_id="s1", char_idx=0, checkpoint_idx=0
                )
            ],
        )
        result = to_inline_text(line)
        assert result == "[10|00:16:50]▨"


# ──────────────────────────────────────────────
# 反序列化 (from_inline_text)
# ──────────────────────────────────────────────


class TestFromInlineText:
    def test_simple_char(self):
        line = from_inline_text("[1|00:15:76]な", singer_id="s1")
        assert line.chars == ["な"]
        assert line.text == "な"
        assert len(line.checkpoints) == 1
        assert line.checkpoints[0].check_count == 1
        assert line.checkpoints[0].is_line_end is False
        assert len(line.timetags) == 1
        assert line.timetags[0].timestamp_ms == 15760

    def test_line_end(self):
        line = from_inline_text("[10|00:10:00]x", singer_id="s1")
        assert line.checkpoints[0].is_line_end is True
        assert line.checkpoints[0].check_count == 1

    def test_rest_char(self):
        line = from_inline_text("[10|00:16:50]▨", singer_id="s1")
        assert line.chars == ["▨"]
        assert line.checkpoints[0].is_rest is True
        assert line.checkpoints[0].is_line_end is True

    def test_ruby_group(self):
        line = from_inline_text("{柔|[2|00:14:64]や[00:15:61]わ}", singer_id="s1")
        assert line.chars == ["柔"]
        assert line.text == "柔"
        assert len(line.rubies) == 1
        assert line.rubies[0].text == "やわ"
        assert line.rubies[0].start_idx == 0
        assert line.rubies[0].end_idx == 1
        assert line.checkpoints[0].check_count == 2
        assert len(line.timetags) == 2
        assert line.timetags[0].timestamp_ms == 14640
        assert line.timetags[1].timestamp_ms == 15610

    def test_multi_char_ruby(self):
        text = "{射程|[1|00:16:76]しゃ＋[2|00:16:89]て[00:17:19]い}"
        line = from_inline_text(text, singer_id="s1")
        assert line.chars == ["射", "程"]
        assert line.text == "射程"
        assert len(line.rubies) == 1
        assert line.rubies[0].text == "しゃてい"
        assert line.rubies[0].start_idx == 0
        assert line.rubies[0].end_idx == 2
        # 射: 1 cp, 程: 2 cps
        assert line.checkpoints[0].check_count == 1
        assert line.checkpoints[1].check_count == 2
        # 3 timetags total
        assert len(line.timetags) == 3

    def test_mixed_line(self):
        """测试用户给出的完整示例格式。"""
        text = (
            "{柔|[2|00:14:64]や[00:15:61]わ}"
            "[1|00:15:76]な"
            "[10|00:16:50]▨"
            "{射程|[1|00:16:76]しゃ＋[2|00:16:89]て[00:17:19]い}"
        )
        line = from_inline_text(text, singer_id="s1")
        assert line.chars == ["柔", "な", "▨", "射", "程"]
        assert len(line.rubies) == 2
        assert line.rubies[0].text == "やわ"
        assert line.rubies[1].text == "しゃてい"
        # checkpoints: 柔(2), な(1), ▨(1,le), 射(1), 程(2)
        assert [cp.check_count for cp in line.checkpoints] == [2, 1, 1, 1, 2]
        assert line.checkpoints[2].is_line_end is True
        assert line.checkpoints[2].is_rest is True
        # 7 timetags total
        assert len(line.timetags) == 7


# ──────────────────────────────────────────────
# 往返 (roundtrip)
# ──────────────────────────────────────────────


class TestRoundtrip:
    def test_simple_roundtrip(self):
        original = _make_line(
            "なは",
            checkpoints=[
                CheckpointConfig(char_idx=0, check_count=1),
                CheckpointConfig(char_idx=1, check_count=1, is_line_end=True),
            ],
            timetags=[
                TimeTag(
                    timestamp_ms=1000, singer_id="s1", char_idx=0, checkpoint_idx=0
                ),
                TimeTag(
                    timestamp_ms=2000, singer_id="s1", char_idx=1, checkpoint_idx=0
                ),
            ],
        )
        text = to_inline_text(original)
        restored = from_inline_text(text, singer_id="s1")
        assert restored.chars == original.chars
        assert len(restored.checkpoints) == len(original.checkpoints)
        for r, o in zip(restored.checkpoints, original.checkpoints):
            assert r.check_count == o.check_count
            assert r.is_line_end == o.is_line_end
        assert len(restored.timetags) == len(original.timetags)
        for rt, ot in zip(restored.timetags, original.timetags):
            assert rt.timestamp_ms == ot.timestamp_ms

    def test_ruby_roundtrip(self):
        original = _make_line(
            "柔な",
            checkpoints=[
                CheckpointConfig(char_idx=0, check_count=2),
                CheckpointConfig(char_idx=1, check_count=1, is_line_end=True),
            ],
            timetags=[
                TimeTag(
                    timestamp_ms=14640, singer_id="s1", char_idx=0, checkpoint_idx=0
                ),
                TimeTag(
                    timestamp_ms=15610, singer_id="s1", char_idx=0, checkpoint_idx=1
                ),
                TimeTag(
                    timestamp_ms=15760, singer_id="s1", char_idx=1, checkpoint_idx=0
                ),
            ],
            rubies=[Ruby(text="やわ", start_idx=0, end_idx=1)],
        )
        text = to_inline_text(original)
        restored = from_inline_text(text, singer_id="s1")
        assert restored.chars == original.chars
        assert len(restored.rubies) == 1
        assert restored.rubies[0].text == "やわ"
        assert len(restored.timetags) == 3

    def test_multiline_roundtrip(self):
        lines = [
            _make_line(
                "あ",
                checkpoints=[CheckpointConfig(char_idx=0, check_count=1)],
                timetags=[
                    TimeTag(
                        timestamp_ms=1000, singer_id="s1", char_idx=0, checkpoint_idx=0
                    )
                ],
            ),
            _make_line(
                "い",
                checkpoints=[CheckpointConfig(char_idx=0, check_count=1)],
                timetags=[
                    TimeTag(
                        timestamp_ms=2000, singer_id="s1", char_idx=0, checkpoint_idx=0
                    )
                ],
            ),
        ]
        text = lines_to_inline_text(lines)
        restored = lines_from_inline_text(text, singer_id="s1")
        assert len(restored) == 2
        assert restored[0].chars == ["あ"]
        assert restored[1].chars == ["い"]
