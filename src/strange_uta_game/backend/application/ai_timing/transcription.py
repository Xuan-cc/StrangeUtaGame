# -*- coding: utf-8 -*-
"""读音 → 罗马字风格表音转写（对齐 token 预处理，2026-08）。

默认对齐模型在日文罗马字分布上微调：拼音/英文按原样透传与模型分布
不匹配，是纯中文/纯英文歌曲对齐精度低的主因。本模块按参考项目口径
转写（转写发生在主进程构建对齐请求时，worker 无需额外依赖）：

- 中文（FA-Kara 口径）：拼音 → 表音拼写（声母/韵母映射表 + 整体
  认读特例），如 zhong→jong、xiao→shyao、lü→ryu、zhi→jru；
- 英文（SUG 自有技术优先）：e2k.txt 词典（CMU 加工的英単語→片假名，
  ``EnglishRubyLookup``）查片假名 → 按拍切分（拗音/长音附前拍）→
  走与日文路径同一个罗马字转换器（take→テイク→te/i/ku）。词典
  未收录回退 pyphen 表面切分，再回退整词小写——pyphen 缺席时
  仍不阻断对齐。
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

# ── 中文：拼音 → 表音（FA-Kara PINYIN_TO_PHONETIC 移植）──

_PINYIN_INITIALS = {
    'b': 'b', 'p': 'p', 'm': 'm', 'f': 'f',
    'd': 'd', 't': 't', 'n': 'n', 'l': 'r',
    'g': 'g', 'k': 'k', 'h': 'h',
    'j': 'j', 'q': 'ch', 'x': 'sh',
    'zh': 'j', 'ch': 'ch', 'sh': 'sh', 'r': 'r',
    'z': 'z', 'c': 'ts', 's': 's',
    'y': 'y', 'w': 'w',
}

_PINYIN_FINALS = {
    'a': 'a', 'o': 'o', 'e': 'e', 'i': 'i', 'u': 'u', 'ü': 'yu',
    'ai': 'ai', 'ei': 'ei', 'ao': 'ao', 'ou': 'ou',
    'an': 'an', 'en': 'en', 'ang': 'ang', 'eng': 'eng', 'ong': 'ong',
    'ia': 'ya', 'ie': 'ye', 'iao': 'yao', 'iu': 'yu',
    'ian': 'yan', 'in': 'in', 'iang': 'yang', 'ing': 'ing', 'iong': 'yong',
    'ua': 'wa', 'uo': 'wo', 'uai': 'wai', 'ui': 'wei',
    'uan': 'wan', 'un': 'wen', 'uang': 'wang', 'ueng': 'weng',
    'üe': 'yue', 'üan': 'yuan', 'ün': 'yun',
    'er': 'a', 'io': 'yo',
}

_PINYIN_SPECIAL = {
    'zhi': 'jru', 'chi': 'chu', 'shi': 'shu', 'ri': 'ru',
    'zi': 'zu', 'ci': 'tsu', 'si': 'su',
    'yi': 'i', 'wu': 'u', 'yu': 'yu',
    'ye': 'ye', 'yue': 'yue', 'yuan': 'yuen',
    'yin': 'in', 'yun': 'yun', 'ying': 'ing',
}

# 声调符号 → 基字母（Style.TONE 形式 "lǜ"/"zhōng" 先归一化）
_TONE_MARKS = str.maketrans(
    "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ",
    "aaaaeeeeiiiioooouuuuüüüü",
)


def pinyin_to_phonetic(pinyin: str) -> str:
    """拼音音节 → 表音拼写（FA-Kara convert_pinyin_to_phonetic 口径）。

    接受带声调符号/声调数字/v 代 ü 的输入；无法分解时原样返回小写。
    """
    text = pinyin.strip().translate(_TONE_MARKS)
    text = re.sub(r"[1-5]", "", text).replace("v", "ü").lower()
    if not text:
        return text
    if text in _PINYIN_SPECIAL:
        return _PINYIN_SPECIAL[text]

    initial = ""
    for cand in sorted(_PINYIN_INITIALS, key=len, reverse=True):
        if text.startswith(cand):
            initial = cand
            break
    final = text[len(initial):] if initial else text

    phonetic_initial = _PINYIN_INITIALS.get(initial, initial)
    phonetic_final = _PINYIN_FINALS.get(final, final)

    if not initial:
        # 零声母 i/u/ü 开头补 y/w
        if final.startswith("i"):
            phonetic_final = "y" + phonetic_final
        elif final.startswith("u"):
            phonetic_final = "w" + phonetic_final
        elif final.startswith("ü"):
            phonetic_final = (
                "yu" + phonetic_final[2:] if len(phonetic_final) > 2 else "yu"
            )
    if initial in ("j", "q", "x") and final.startswith("ü"):
        # j/q/x 后的 ü 写作 u
        phonetic_final = (
            "u" + phonetic_final[2:] if len(phonetic_final) > 2 else "u"
        )
    return phonetic_initial + phonetic_final


# ── 英文：e2k 词典 → 片假名 → 按拍罗马字（SUG 自有数据）──

_E2K_LOOKUP_CACHE: Optional[Callable[[str], Optional[str]]] = None
"""惰性持有的 e2k 查询函数；测试可整体替换（封闭环境）。"""

_PYPhen_CACHE: Optional[object] = None
_ENGLISH_CACHE: dict = {}

# 拗音/合拗音的小假名与长音符：附到前一拍
_ATTACH_TO_PREV = set("ァィゥェォャュョヮー")


def _e2k_lookup(word: str) -> Optional[str]:
    """e2k.txt 词表查询（EnglishRubyLookup 单例，首次调用加载）。"""
    global _E2K_LOOKUP_CACHE
    if _E2K_LOOKUP_CACHE is None:
        try:
            from strange_uta_game.backend.infrastructure.parsers.english_ruby import (
                EnglishRubyLookup,
            )

            inst = EnglishRubyLookup.instance()
            if inst.has():
                _E2K_LOOKUP_CACHE = inst.lookup
            else:
                _E2K_LOOKUP_CACHE = lambda w: None
        except Exception:
            _E2K_LOOKUP_CACHE = lambda w: None
    return _E2K_LOOKUP_CACHE(word)


def _katakana_mora(text: str) -> List[str]:
    """片假名切拍：拗音小假名与长音符附到前一拍。"""
    moras: List[str] = []
    for ch in text:
        if moras and ch in _ATTACH_TO_PREV:
            moras[-1] += ch
        else:
            moras.append(ch)
    return moras


def _pyphen_dic():
    global _PYPhen_CACHE
    if _PYPhen_CACHE is None:
        try:
            import pyphen

            _PYPhen_CACHE = pyphen.Pyphen(lang="en_US")
        except Exception:
            _PYPhen_CACHE = False
    return _PYPhen_CACHE or None


def english_word_syllables(word: str) -> List[str]:
    """英文词 → 每拍罗马字读音列表（e2k → pyphen → 整词小写逐级回退）。

    e2k 命中：片假名按拍切分后走与日文路径相同的罗马字转换器
    （take→テイク→["te","i","ku"]）。结果按词缓存。
    """
    word = word.strip()
    if not word:
        return []
    key = word.lower()
    if key in _ENGLISH_CACHE:
        return list(_ENGLISH_CACHE[key])

    result: List[str] = []
    kana = _e2k_lookup(key)
    if kana:
        try:
            from strange_uta_game.backend.application.ai_timing.alignment import (
                romanize_ruby_parts,
            )

            roms = romanize_ruby_parts(_katakana_mora(kana))
            result = [r for r in roms if r and r.strip()]
        except Exception:
            result = []
    if not result:
        dic = _pyphen_dic()
        if dic is not None:
            surface = [s.lower() for s in dic.inserted(key).split("-")]
            result = [s for s in surface if s]
    if not result:
        result = [key]

    _ENGLISH_CACHE[key] = list(result)
    return list(result)


__all__ = [
    "pinyin_to_phonetic",
    "english_word_syllables",
]
