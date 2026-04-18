"""歌词解析器测试。"""

import pytest
from pathlib import Path
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    TXTParser,
    LRCParser,
    KRAParser,
    LyricParserFactory,
    ParseError,
    parse_to_sentences,
    ParsedLine,
)


class TestTXTParser:
    """测试 TXT 解析器"""

    def test_parse_simple_text(self):
        parser = TXTParser()
        content = "第一行\n第二行\n第三行"
        result = parser.parse(content)

        assert len(result) == 3
        assert result[0].text == "第一行"
        assert result[0].timetags == []

    def test_parse_skip_empty_lines(self):
        parser = TXTParser()
        content = "第一行\n\n第三行"
        result = parser.parse(content)

        assert len(result) == 2

    def test_parse_strip_whitespace(self):
        parser = TXTParser()
        content = "  第一行  \n  第二行  "
        result = parser.parse(content)

        assert result[0].text == "第一行"
        assert result[1].text == "第二行"


class TestLRCParser:
    """测试 LRC 解析器"""

    def test_parse_simple_lrc(self):
        parser = LRCParser()
        content = "[00:10.50]第一行\n[00:15.20]第二行"
        result = parser.parse(content)

        assert len(result) == 2
        assert result[0].text == "第一行"
        assert result[0].timetags == [(0, 10500)]

        assert result[1].text == "第二行"
        assert result[1].timetags == [(0, 15200)]

    def test_parse_skip_metadata(self):
        parser = LRCParser()
        content = "[ti:Title]\n[ar:Artist]\n[00:10.00]歌词"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "歌词"

    def test_parse_milliseconds_precision(self):
        parser = LRCParser()
        content = "[00:10.123]歌词"
        result = parser.parse(content)

        assert result[0].timetags == [(0, 10123)]

    def test_parse_start_end_timestamps(self):
        """测试 [start]歌词[end] 格式 — 增强LRC常见格式"""
        parser = LRCParser()
        content = "[00:06.540]一闪一闪亮晶晶[00:09.300]"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "一闪一闪亮晶晶"
        assert result[0].timetags == [(0, 6540)]

    def test_parse_start_end_multi_lines(self):
        """测试多行 [start]歌词[end] 格式"""
        parser = LRCParser()
        content = (
            "[00:06.540]一闪一闪亮晶晶[00:09.300]\n"
            "[00:09.300]满天都是小星星[00:12.120]\n"
            "[00:12.120]挂在天上放光明[00:15.060]"
        )
        result = parser.parse(content)

        assert len(result) == 3
        assert result[0].text == "一闪一闪亮晶晶"
        assert result[0].timetags == [(0, 6540)]
        assert result[1].text == "满天都是小星星"
        assert result[1].timetags == [(0, 9300)]
        assert result[2].text == "挂在天上放光明"
        assert result[2].timetags == [(0, 12120)]

    def test_parse_colon_separator(self):
        """测试冒号分隔的时间标签 [mm:ss:cc]"""
        parser = LRCParser()
        content = "[00:06:54]一闪一闪[00:09:30]"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "一闪一闪"
        assert result[0].timetags == [(0, 6540)]


class TestLyricParserFactory:
    """测试解析器工厂"""

    def test_get_txt_parser(self):
        parser = LyricParserFactory.get_parser("test.txt")
        assert isinstance(parser, TXTParser)

    def test_get_lrc_parser(self):
        parser = LyricParserFactory.get_parser("test.lrc")
        assert isinstance(parser, LRCParser)

    def test_get_kra_parser(self):
        parser = LyricParserFactory.get_parser("test.kra")
        assert isinstance(parser, KRAParser)

    def test_unsupported_format_raises_error(self):
        with pytest.raises(ParseError):
            LyricParserFactory.get_parser("test.mp3")


class TestParseToSentences:
    """测试转换为 Sentence"""

    def test_convert_with_timetags(self):
        parsed_lines = [
            ParsedLine(text="测试", timetags=[(0, 1000)]),
        ]

        sentences = parse_to_sentences(parsed_lines, "singer_1")

        assert len(sentences) == 1
        assert sentences[0].text == "测试"
        # 逐字模型中，(0, 1000) 对应第0个字符的第0个时间戳
        assert len(sentences[0].characters[0].timestamps) == 1
        assert sentences[0].characters[0].timestamps[0] == 1000
