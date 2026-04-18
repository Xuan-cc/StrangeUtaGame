"""AutoCheckService 测试。"""

import pytest
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.domain import Sentence
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import DummyAnalyzer


class TestAutoCheckService:
    """测试自动检查服务"""

    def test_analyze_sentence_simple(self):
        """测试分析简单日文"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("赤い花", "s1")

        results = service.analyze_sentence(sentence)

        assert len(results) == 3
        assert results[0].char == "赤"
        assert results[1].char == "い"
        assert results[2].char == "花"

    def test_apply_to_sentence(self):
        """测试应用自动检查结果"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("赤い花", "s1")

        service.apply_to_sentence(sentence)

        # 验证 chars 被设置
        assert sentence.chars == ["赤", "い", "花"]

        # 验证 characters 被创建
        assert len(sentence.characters) == 3

        # 验证最后一个字符标记为句尾
        assert sentence.characters[2].is_line_end

    def test_estimate_check_count(self):
        """测试估算节奏点数量"""
        service = AutoCheckService(DummyAnalyzer())

        count = service.estimate_check_count("赤い花")

        # DummyAnalyzer 直接返回字符数
        assert count == 3

    def test_keep_existing_timetags(self):
        """测试保留现有时间标签"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("赤い花", "s1")
        sentence.characters[0].add_timestamp(1000)

        # 应用自动检查（保留时间标签）
        service.apply_to_sentence(sentence, keep_existing_timetags=True)

        # 验证时间标签保留
        assert len(sentence.characters[0].timestamps) == 1

    def test_clear_existing_timetags(self):
        """测试清除现有时间标签"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("赤い花", "s1")
        sentence.characters[0].add_timestamp(1000)

        # 应用自动检查（清除时间标签）
        service.apply_to_sentence(sentence, keep_existing_timetags=False)

        # 验证时间标签被清除
        assert len(sentence.characters[0].timestamps) == 0

    def test_flags_disable_hiragana(self):
        """测试关闭平假名打勾"""
        flags = {"hiragana": False, "kanji": True}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("赤い花", "s1")

        results = service.analyze_sentence(sentence)

        assert results[1].check_count == 0

    def test_flags_kanji_single_check(self):
        """测试汉字节奏点限定为1"""
        flags = {"kanji_single_check": True}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("赤い花", "s1")

        results = service.analyze_sentence(sentence)

        for result in results:
            if result.char in ("赤", "花"):
                assert result.check_count <= 1

    def test_flags_check_line_end_disabled(self):
        """测试关闭行尾打勾"""
        flags = {"check_line_end": False}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("赤い花", "s1")

        service.apply_to_sentence(sentence)

        assert not sentence.characters[-1].is_line_end
