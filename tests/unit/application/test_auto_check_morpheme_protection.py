"""Phase 5 用户词典「连词组保护」回归测试。

历史 bug：用户词典里若有单字汉字条目（如 `日 → にち`），Phase 5 会以子串严格匹配
方式覆盖句子里每一个 `日`，包括 `一日 / 日々 / ある日 / 毎日 / 日付` 等多字 morpheme
里的 `日`，结果把 WinRT 正确分析出来的 `ついたち / ひび / あるひ / まいにち / ひづけ`
打成 `ついにち / にちび / あるにち / まいにち（恰好不变）/ にちづけ` —— 严重劣化。

Bug 路径有两条：
A. Step 3 mora 均分（4 mora ÷ 2 字 整除时）会把 fallback 块的 char_to_block 抹掉，
   导致 Phase 5 看不到连词组。
B. clean per-char split（`日々→ひ+び`）压根不写 char_to_block。

修复办法：分析器返回的 ``RubyResult.morpheme_span`` 永久保留同一 (surface, reading)
pair 派生出的所有 RubyResult 的共同 morpheme 范围；analyze_sentence 把该 span 翻成
``char_to_morpheme``；Phase 5 用它做 partial-overlap 判定，而不再依赖会被 Step 3
擦掉的 linked_to_next。

这些用例使用固定形态素分析结果，避免把单元测试绑定到系统 IME 状态。
"""

from __future__ import annotations

import pytest

from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.domain import Sentence
from strange_uta_game.backend.domain.models import Character
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    KanaDistributingAnalyzer,
)


class _ContextReadingAnalyzer(KanaDistributingAnalyzer):
    """为保护逻辑提供稳定的上下文读音和形态素边界。"""

    _PAIRS = {
        "一日": [("一日", "ついたち")],
        "日々": [("日々", "ひび")],
        "ある日": [("ある日", "あるひ")],
        "毎日": [("毎日", "まいにち")],
        "日付": [("日付", "ひづけ")],
        "一日中": [("一日", "ついたち"), ("中", "じゅう")],
        "日々頑張る": [("日々", "ひび"), ("頑張る", "がんばる")],
    }

    def _get_pairs(self, text):
        return self._PAIRS[text]

    def get_reading(self, text):
        return "".join(reading for _, reading in self._get_pairs(text))


def _make_sentence(text: str) -> Sentence:
    return Sentence(
        singer_id="default",
        characters=[Character(char=c) for c in text],
    )


def _char_readings(sentence: Sentence) -> list[tuple[str, str]]:
    """返回 [(char, ruby_text), ...]，ruby_text 为各 part.text 拼接。"""
    out = []
    for c in sentence.characters:
        r = "".join(p.text for p in c.ruby.parts) if c.ruby else ""
        out.append((c.char, r))
    return out


# 用户词典里只有一条 `日 → にち`，模拟 RL 迁移残留场景。
_USER_DICT_HI_NICHI = [
    {"enabled": True, "word": "日", "reading": "{日||にち}"},
]


class TestUserDictSingleKanjiDoesNotBreakMorpheme:
    """单字词条 ``日→にち`` 不应污染多字 morpheme 里的 ``日``。"""

    @pytest.fixture
    def svc(self):
        return AutoCheckService(
            ruby_analyzer=_ContextReadingAnalyzer(), auto_check_flags={},
            user_dictionary=_USER_DICT_HI_NICHI,
        )

    @pytest.mark.parametrize(
        "text,expected_kanji_readings",
        [
            # 一日：WinRT 给 ついたち → 拆 一[つい]日[たち]；
            # 单字词条 日 不得把 日 改成 にち。
            ("一日", {0: "つい", 1: "たち"}),
            # 日々：WinRT 给 ひび → 拆 日[ひ]々[び]；同样不得污染。
            ("日々", {0: "ひ", 1: "び"}),
            # ある日：WinRT 给 あるひ 整段 morpheme，日 受 morpheme 保护。
            ("ある日", {2: "ひ"}),
            # 毎日：WinRT 给 まいにち（恰好 日=にち 一致，但保护逻辑须生效，
            # 否则 mora 均分被破坏会再拆错）。
            ("毎日", {0: "まい", 1: "にち"}),
            # 日付：WinRT 给 ひづけ，日=ひ 受保护。
            ("日付", {0: "ひ", 1: "づけ"}),
            # 一日中：嵌套场景 —— 一日 morpheme 内 日 保护；中独立。
            ("一日中", {0: "つい", 1: "たち", 2: "じゅう"}),
            # 日々頑張る：日々 morpheme 内 日 保护。
            ("日々頑張る", {0: "ひ", 1: "び"}),
        ],
    )
    def test_morpheme_protected_from_single_kanji_dict(
        self, svc, text, expected_kanji_readings
    ):
        s = _make_sentence(text)
        svc.apply_to_sentence(s, apply_user_dict=True, skip_romanize=True)
        actual = _char_readings(s)
        for idx, expected_r in expected_kanji_readings.items():
            ch, r = actual[idx]
            assert r == expected_r, (
                f"{text!r} idx={idx} char={ch!r}: got {r!r}, want {expected_r!r}\n"
                f"full: {actual}"
            )

    def test_standalone_kanji_still_applies_dict(self, svc):
        """孤立的 `日`（自身就是单字 morpheme）应正常被词典覆盖。"""
        s = _make_sentence("日")
        svc.apply_to_sentence(s, apply_user_dict=True, skip_romanize=True)
        actual = _char_readings(s)
        assert actual == [("日", "にち")]

    def test_isolated_kanji_in_kana_context_applies_dict(self, svc):
        """孤立的 `日`（前后假名、非 morpheme 内）应正常被词典覆盖。

        例：「今日の日」—— 「今日」是 morpheme，第二个 `日` 是单字 morpheme。
        """
        s = _make_sentence("今日の日")
        svc.apply_to_sentence(s, apply_user_dict=True, skip_romanize=True)
        actual = _char_readings(s)
        # 今日 (morpheme) 受保护 → 不被 日→にち 覆盖；末尾的孤立 日 被覆盖。
        assert actual[0][1] != "にち" or actual[1][1] != "にち", (
            f"今日 morpheme 不应被 日→にち 污染，actual={actual}"
        )
        assert actual[3] == ("日", "にち")
