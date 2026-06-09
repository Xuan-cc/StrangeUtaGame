"""``kanji_reading_split`` 单元测试。

覆盖三种走向：
- 字典命中（世界/せかい → ['せ','かい']，新時代/はじまり → None，国道/こくどう → ['こく','どう']）
- 均分（ateji 兜底：4 假名 / 3 字 → 首字独大 2+1+1）
- 端到端入口 ``compute_per_kanji_readings`` 同时报告"是否 ateji"
"""

import pytest

from strange_uta_game.backend.infrastructure.parsers.kanji_reading_split import (
    compute_per_kanji_readings,
    even_distribute_kana,
    split_by_kanji_dict,
)


class TestSplitByKanjiDict:
    def test_sekai_splits_one_plus_two(self):
        # 世(セ) + 界(カイ) → ['せ','かい']
        assert split_by_kanji_dict("世界", "せかい") == ["せ", "かい"]

    def test_kokudo_splits_two_plus_two(self):
        # 国(コク) + 道(ドウ) → ['こく','どう']
        assert split_by_kanji_dict("国道", "こくどう") == ["こく", "どう"]

    def test_ateji_returns_none(self):
        # 新時代/はじまり：3 字哪个都不读 はじ/ま/り → 字典查不到 → None
        assert split_by_kanji_dict("新時代", "はじまり") is None

    def test_handles_katakana_reading_input(self):
        # reading 含片假名时归一为平假名再匹配（与字典内部一致）。
        assert split_by_kanji_dict("世界", "セカイ") == ["せ", "かい"]

    def test_unknown_kanji_returns_none(self):
        # 故意编一个不会出现在字典里的字符（PUA 区段）。
        assert split_by_kanji_dict("界", "せかい") is None

    def test_empty_inputs_return_none(self):
        assert split_by_kanji_dict("", "せかい") is None
        assert split_by_kanji_dict("世界", "") is None


class TestEvenDistributeKana:
    def test_first_segment_takes_remainder(self):
        # 4 假名 / 3 字 → 首字独大：はじ + ま + り
        assert even_distribute_kana("はじまり", 3) == ["はじ", "ま", "り"]

    def test_clean_division(self):
        # 4 假名 / 2 字 → 2 + 2
        assert even_distribute_kana("こくどう", 2) == ["こく", "どう"]

    def test_one_kanji_takes_all(self):
        assert even_distribute_kana("はじまり", 1) == ["はじまり"]

    def test_more_chars_than_kana_pads_empty(self):
        # 2 假名 / 4 字 → 首字独大 base=0,extra=2 → ['せか','','','']
        # 这种场景实际不太可能出现（注音通常多于汉字数），仅做防御性验证。
        assert even_distribute_kana("せか", 4) == ["せか", "", "", ""]

    def test_zero_chars_returns_empty(self):
        assert even_distribute_kana("せか", 0) == []


class TestComputePerKanjiReadings:
    def test_dict_hit_marks_not_ateji(self):
        readings, is_ateji = compute_per_kanji_readings("世界", "せかい")
        assert readings == ["せ", "かい"]
        assert is_ateji is False

    def test_dict_miss_falls_back_to_even_distribute(self):
        readings, is_ateji = compute_per_kanji_readings("新時代", "はじまり")
        assert readings == ["はじ", "ま", "り"]
        assert is_ateji is True

    def test_non_kanji_word_returns_whole_reading(self):
        # 含非汉字字符时不参与拆分，整段挂回首字（调用方按 fallback 处理）。
        readings, is_ateji = compute_per_kanji_readings("世界A", "せかいえー")
        assert readings == ["せかいえー"]
        assert is_ateji is True
