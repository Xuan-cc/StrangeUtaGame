# -*- coding: utf-8 -*-
"""对齐转写（FA-Kara 口径移植）：拼音→表音、英文 CMU→罗马字音节。"""

import pytest

from strange_uta_game.backend.application.ai_timing import transcription


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """封闭环境：不触网、不依赖本机 nltk/pyphen。"""
    monkeypatch.setattr(transcription, "_CMU_CACHE", {})
    monkeypatch.setattr(transcription, "_PYPhen_CACHE", False)
    monkeypatch.setattr(transcription, "_ENGLISH_CACHE", {})
    yield


class TestPinyinToPhonetic:
    def test_initials_and_finals(self):
        # zh→j + ong→ong；x→sh + iao→yao；l→r + ü→yu
        assert transcription.pinyin_to_phonetic("zhōng") == "jong"
        assert transcription.pinyin_to_phonetic("zhong1") == "jong"
        assert transcription.pinyin_to_phonetic("xiǎo") == "shyao"
        assert transcription.pinyin_to_phonetic("lǜ") == "ryu"
        assert transcription.pinyin_to_phonetic("lv4") == "ryu"  # v 代 ü

    def test_special_whole_syllables(self):
        assert transcription.pinyin_to_phonetic("zhī") == "jru"
        assert transcription.pinyin_to_phonetic("yuàn") == "yuen"
        assert transcription.pinyin_to_phonetic("wǔ") == "u"

    def test_zero_initial_glides(self):
        # 零声母 i/u 开头补 y/w
        assert transcription.pinyin_to_phonetic("ài") == "ai"
        assert transcription.pinyin_to_phonetic("ān") == "an"

    def test_jqx_umlaut(self):
        # j/q/x 后的 ü 写作 u
        assert transcription.pinyin_to_phonetic("jū") == "ju"
        assert transcription.pinyin_to_phonetic("qù") == "chu"  # q→ch + u

    def test_undecomposable_passthrough(self):
        assert transcription.pinyin_to_phonetic("zzz") == "zzz"


class TestEnglishWordSyllables:
    def test_cmu_route(self, monkeypatch):
        monkeypatch.setattr(
            transcription,
            "_CMU_CACHE",
            {
                "take": [["T", "EY1", "K"]],
                "beautiful": [["B", "Y", "UW1", "T", "AH0", "F", "UH0", "L"]],
            },
        )
        # T→t EY→ei K→k
        assert transcription.english_word_syllables("take") == ["teik"]
        # B,Y,UW | T,AH | F,UH,L（辅音串>1 时第一个归前音节）
        parts = transcription.english_word_syllables("beautiful")
        assert len(parts) == 3
        assert "".join(parts) == "byutafur"

    def test_fallback_without_dicts(self):
        # CMU/pyphen 均不可用：整词小写
        assert transcription.english_word_syllables("ZZZ") == ["zzz"]
        assert transcription.english_word_syllables("") == []

    def test_cache_reuse(self, monkeypatch):
        monkeypatch.setattr(
            transcription, "_CMU_CACHE", {"take": [["T", "EY1", "K"]]}
        )
        first = transcription.english_word_syllables("take")
        monkeypatch.setattr(transcription, "_CMU_CACHE", {})
        # 第二次命中缓存，不再查词典
        assert transcription.english_word_syllables("take") == first == ["teik"]
