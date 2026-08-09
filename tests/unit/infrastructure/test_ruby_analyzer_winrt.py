"""WinRT 注音分析器的原文位置映射测试。"""

from types import SimpleNamespace

from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    WinRTAnalyzer,
)


class _WhitespaceNormalizingJPA:
    """模拟 WinRT：若收到连续空白，会将其折叠为一个字符。"""

    calls = []

    @classmethod
    def get_words(cls, text):
        cls.calls.append(text)
        normalized = " ".join(text.split())
        reading = {
            "君に届くよぅに": "きみにとどくよぅに",
        }.get(normalized, normalized)
        return [SimpleNamespace(display_text=normalized, yomi_text=reading)]


def _make_analyzer():
    analyzer = WinRTAnalyzer.__new__(WinRTAnalyzer)
    analyzer._jpa = _WhitespaceNormalizingJPA
    analyzer._pykakasi_conv = None
    _WhitespaceNormalizingJPA.calls = []
    return analyzer


def test_get_pairs_preserves_consecutive_half_and_full_width_spaces():
    analyzer = _make_analyzer()
    text = "magic magic! 　君に届くよぅに"

    pairs = analyzer._get_pairs(text)

    assert "".join(surface for surface, _ in pairs) == text
    assert all(not any(char.isspace() for char in call) for call in analyzer._jpa.calls)
    assert (" ", " ") in pairs
    assert ("　", "　") in pairs


def test_analyze_keeps_reading_at_original_index_after_consecutive_spaces():
    analyzer = _make_analyzer()
    text = "magic magic! 　君に届くよぅに"

    results = analyzer.analyze(text)

    kimi_index = text.index("君")
    kimi = next(result for result in results if result.text == "君")
    assert kimi.start_idx == kimi_index
    assert kimi.end_idx == kimi_index + 1
    assert kimi.reading == "きみ"
