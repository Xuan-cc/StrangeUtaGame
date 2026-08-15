# -*- coding: utf-8 -*-
"""对齐转写：拼音→表音（FA-Kara 口径）、英文 e2k→片假名→按拍罗马字。"""

import pytest

from strange_uta_game.backend.application.ai_timing import transcription


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """封闭环境：不加载真词典、不依赖本机 pyphen。"""
    monkeypatch.setattr(transcription, "_E2K_LOOKUP_CACHE", lambda w: None)
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


class TestEnglishE2K:
    def test_e2k_route_per_mora_romaji(self, monkeypatch):
        monkeypatch.setattr(
            transcription,
            "_E2K_LOOKUP_CACHE",
            lambda w: {"take": "テイク", "beautiful": "ビューティファル"}.get(w),
        )
        # テ|イ|ク（拗音/长音附前拍）
        assert transcription.english_word_syllables("take") == [
            "te", "i", "ku",
        ]
        # ビュー|ティ|ファ|ル
        assert transcription.english_word_syllables("beautiful") == [
            "byuu", "ti", "fa", "ru",
        ]

    def test_fallback_without_dicts(self):
        # e2k 未收录且 pyphen 缺席：整词小写
        assert transcription.english_word_syllables("ZZZ") == ["zzz"]
        assert transcription.english_word_syllables("") == []

    def test_cache_reuse(self, monkeypatch):
        calls = []

        def _fake(w):
            calls.append(w)
            return "テイク"

        monkeypatch.setattr(transcription, "_E2K_LOOKUP_CACHE", _fake)
        first = transcription.english_word_syllables("take")
        assert transcription.english_word_syllables("take") == first
        assert calls == ["take"]  # 第二次命中缓存，不再查词典

    def test_real_bundled_dictionary(self, monkeypatch):
        """e2k.txt 随仓库分发：真实词典可用且转换链路稳定。"""
        monkeypatch.setattr(transcription, "_E2K_LOOKUP_CACHE", None)
        monkeypatch.setattr(transcription, "_ENGLISH_CACHE", {})
        assert transcription.english_word_syllables("take") == [
            "te", "i", "ku",
        ]
