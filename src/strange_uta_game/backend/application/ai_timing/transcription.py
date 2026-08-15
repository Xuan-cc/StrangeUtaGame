# -*- coding: utf-8 -*-
"""读音 → 罗马字风格表音转写（对齐 token 预处理，2026-08）。

默认对齐模型在日文罗马字分布上微调：拼音/英文按原样透传与模型分布
不匹配，是纯中文/纯英文歌曲对齐精度低的主因。本模块按 FA-Kara
（MIT，haruraw2norm.py）的口径转写：

- 中文：拼音 → 表音拼写（声母/韵母映射表 + 整体认读特例），
  如 zhong→jong、xiao→shyao、lü→ryu、zhi→jru；
- 英文：CMU 词典音素 → 按元音切音节 → ARPAbet→罗马字映射
  （如 take→teik、beautiful→bjutafur）；词典外回退 pyphen 表面
  切分，再回退整词小写。

重型依赖（nltk/pyphen）惰性导入：运行环境未装或词典缺失时静默
回退，绝不阻断对齐。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

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


# ── 英文：CMU 音素 → 罗马字（FA-Kara phoneme_map 移植）──

_ARPABET_TO_ROMAJI = {
    'AA': 'a', 'AE': 'a', 'AH': 'a', 'AO': 'o', 'AW': 'au', 'AY': 'ai',
    'B': 'b', 'CH': 'ch', 'D': 'd', 'DH': 'z', 'EH': 'e', 'ER': 'a',
    'EY': 'ei', 'F': 'f', 'G': 'g', 'HH': 'h', 'IH': 'i', 'IY': 'i',
    'JH': 'j', 'K': 'k', 'L': 'r', 'M': 'm', 'N': 'n', 'NG': 'ng',
    'OW': 'o', 'OY': 'oi', 'P': 'p', 'R': 'r', 'S': 's', 'SH': 'sh',
    'T': 't', 'TH': 's', 'UH': 'u', 'UW': 'u', 'V': 'v', 'W': 'w',
    'Y': 'y', 'Z': 'z', 'ZH': 'j',
}

_ARPABET_VOWELS = frozenset(
    {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY",
     "IH", "IY", "OW", "OY", "UH", "UW"}
)

_CMU_CACHE: Optional[Dict[str, list]] = None
_PYPhen_CACHE: Optional[object] = None
_ENGLISH_CACHE: Dict[str, List[str]] = {}


def _cmu_dict() -> Optional[Dict[str, list]]:
    global _CMU_CACHE
    if _CMU_CACHE is not None:
        return _CMU_CACHE
    try:
        from nltk.corpus import cmudict

        _CMU_CACHE = cmudict.dict()
    except LookupError:
        try:
            import nltk

            nltk.download("cmudict", quiet=True)
            from nltk.corpus import cmudict

            _CMU_CACHE = cmudict.dict()
        except Exception:
            _CMU_CACHE = {}
    except Exception:
        _CMU_CACHE = {}
    return _CMU_CACHE


def _pyphen_dic():
    global _PYPhen_CACHE
    if _PYPhen_CACHE is None:
        try:
            import pyphen

            _PYPhen_CACHE = pyphen.Pyphen(lang="en_US")
        except Exception:
            _PYPhen_CACHE = False
    return _PYPhen_CACHE or None


def _split_phonemes_syllables(phonemes: List[str]) -> List[List[str]]:
    """按元音位置切音节（最大节首辅音：辅音串>1 时第一个归前音节）。"""
    positions = [
        i for i, ph in enumerate(phonemes) if ph.rstrip("012") in _ARPABET_VOWELS
    ]
    if not positions:
        return [list(phonemes)]

    syllables: List[List[str]] = []
    prev = -1
    for idx_no, vowel_idx in enumerate(positions):
        if idx_no == 0:
            syllables.append(list(phonemes[: vowel_idx + 1]))
        else:
            consonants = phonemes[prev + 1: vowel_idx]
            if consonants:
                onset_start = 0
                if len(consonants) > 1:
                    syllables[-1].append(consonants[0])
                    onset_start = 1
                syllables.append(
                    list(consonants[onset_start:]) + [phonemes[vowel_idx]]
                )
            else:
                syllables.append([phonemes[vowel_idx]])
        prev = vowel_idx
    if prev < len(phonemes) - 1:
        syllables[-1].extend(phonemes[prev + 1:])
    return syllables


def english_word_syllables(word: str) -> List[str]:
    """英文词 → 每音节的罗马字风格读音列表。

    CMU 词典命中：音素切音节后逐音素映射（FA-Kara 口径）；
    未命中：pyphen 表面切分小写；连 pyphen 都没有：整词小写。
    结果缓存（词级），无读音可推导时返回空列表。
    """
    word = word.strip()
    if not word:
        return []
    key = word.lower()
    if key in _ENGLISH_CACHE:
        return list(_ENGLISH_CACHE[key])

    result: List[str] = []
    cmu = _cmu_dict()
    entry = cmu.get(key) if cmu else None
    if entry:
        phonemes = entry[0]
        for syllable in _split_phonemes_syllables(phonemes):
            romaji = "".join(
                _ARPABET_TO_ROMAJI.get(ph.rstrip("012"), "")
                for ph in syllable
            ).strip()
            if romaji:
                result.append(romaji)
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
