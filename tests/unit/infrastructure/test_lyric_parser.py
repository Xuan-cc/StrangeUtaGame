"""歌词解析器测试。"""

import pytest
from pathlib import Path
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    TXTParser,
    LRCParser,
    KRAParser,
    LyricParserFactory,
    ParseError,
    parse_to_lyric_lines,
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


class TestParseToLyricLines:
    """测试转换为 LyricLine"""

    def test_convert_with_timetags(self):
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            ParsedLine,
        )

        parsed_lines = [
            ParsedLine(text="测试", timetags=[(0, 1000)]),
        ]

        lines = parse_to_lyric_lines(parsed_lines, "singer_1")

        assert len(lines) == 1
        assert lines[0].text == "测试"
        assert len(lines[0].timetags) == 1
        assert lines[0].timetags[0].timestamp_ms == 1000
