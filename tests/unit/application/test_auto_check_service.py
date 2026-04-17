"""AutoCheckService 测试。"""

import pytest
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.domain import LyricLine
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import DummyAnalyzer


class TestAutoCheckService:
    """测试自动检查服务"""

    def test_analyze_line_simple(self):
        """测试分析简单日文"""
        service = AutoCheckService(DummyAnalyzer())
        line = LyricLine(singer_id="s1", text="赤い花")

        results = service.analyze_line(line)

        assert len(results) == 3
        assert results[0].char == "赤"
        assert results[1].char == "い"
        assert results[2].char == "花"

    def test_apply_to_line(self):
        """测试应用自动检查结果"""
        service = AutoCheckService(DummyAnalyzer())
        line = LyricLine(singer_id="s1", text="赤い花")

        service.apply_to_line(line)

        # 验证 chars 被设置
        assert line.chars == ["赤", "い", "花"]

        # 验证 checkpoints 被创建
        assert len(line.checkpoints) == 3

        # 验证最后一个字符标记为句尾
        assert line.checkpoints[2].is_line_end

    def test_estimate_check_count(self):
        """测试估算节奏点数量"""
        service = AutoCheckService(DummyAnalyzer())

        count = service.estimate_check_count("赤い花")

        # DummyAnalyzer 直接返回字符数
        assert count == 3

    def test_keep_existing_timetags(self):
        """测试保留现有时间标签"""
        from strange_uta_game.backend.domain import TimeTag

        service = AutoCheckService(DummyAnalyzer())
        line = LyricLine(singer_id="s1", text="赤い花")
        line.add_timetag(TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0))

        # 应用自动检查（保留时间标签）
        service.apply_to_line(line, keep_existing_timetags=True)

        # 验证时间标签保留
        assert len(line.timetags) == 1

    def test_clear_existing_timetags(self):
        """测试清除现有时间标签"""
        from strange_uta_game.backend.domain import TimeTag

        service = AutoCheckService(DummyAnalyzer())
        line = LyricLine(singer_id="s1", text="赤い花")
        line.add_timetag(TimeTag(timestamp_ms=1000, singer_id="s1", char_idx=0))

        # 应用自动检查（清除时间标签）
        service.apply_to_line(line, keep_existing_timetags=False)

        # 验证时间标签被清除
        assert len(line.timetags) == 0

    def test_flags_disable_hiragana(self):
        """测试关闭平假名打勾"""
        flags = {"hiragana": False, "kanji": True}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        line = LyricLine(singer_id="s1", text="赤い花")

        results = service.analyze_line(line)

        assert results[1].check_count == 0

    def test_flags_kanji_single_check(self):
        """测试汉字节奏点限定为1"""
        flags = {"kanji_single_check": True}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        line = LyricLine(singer_id="s1", text="赤い花")

        results = service.analyze_line(line)

        for result in results:
            if result.char in ("赤", "花"):
                assert result.check_count <= 1

    def test_flags_check_line_end_disabled(self):
        """测试关闭行尾打勾"""
        flags = {"check_line_end": False}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        line = LyricLine(singer_id="s1", text="赤い花")

        service.apply_to_line(line)

        assert not line.checkpoints[-1].is_line_end
