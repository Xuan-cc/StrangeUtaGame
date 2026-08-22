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

    音素路径（``english_word_phoneme_syllables``）未命中时的回退口径，
    词内 token 仍由 provider 按比例切分。
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

            roms = romanize_ruby_parts(
                _katakana_mora(kana), sokuon_standalone=True
            )
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


# ── 英文：CMU 音素 → 音节罗马字（FA-Kara 口径，MIT）──
# 与 e2k（词→片假名→按拍）不同：读音直接来自 cmudict ARPAbet 音素，
# 按「最大节首辅音原则」切成音节后逐音素映射成罗马字，再合并到
# pyphen 拼写音节数——token 粒度=拼写音节，每个音节可独立对齐
# （e2k 按拍口径词内边界靠比例切分，实测不如音素口径准）。
# 音素映射表取自 https://github.com/moriwx/FA-Kara（MIT），未内嵌其代码。

_CMU_PHONEME_ROMAJI = {
    'AA': 'a', 'AE': 'a', 'AH': 'a', 'AO': 'o', 'AW': 'au', 'AY': 'ai',
    'B': 'b', 'CH': 'ch', 'D': 'd', 'DH': 'z', 'EH': 'e', 'ER': 'a',
    'EY': 'ei', 'F': 'f', 'G': 'g', 'HH': 'h', 'IH': 'i', 'IY': 'i',
    'JH': 'j', 'K': 'k', 'L': 'r', 'M': 'm', 'N': 'n', 'NG': 'ng',
    'OW': 'o', 'OY': 'oi', 'P': 'p', 'R': 'r', 'S': 's', 'SH': 'sh',
    'T': 't', 'TH': 's', 'UH': 'u', 'UW': 'u', 'V': 'v', 'W': 'w',
    'Y': 'y', 'Z': 'z', 'ZH': 'j',
}

_CMU_VOWELS = {
    'AA', 'AE', 'AH', 'AO', 'AW', 'AY', 'EH', 'ER', 'EY',
    'IH', 'IY', 'OW', 'OY', 'UH', 'UW',
}

_CMU_LOOKUP_CACHE: Optional[Callable[[str], Optional[List[str]]]] = None
"""惰性持有的 cmudict 查询函数（word_lower → 首选音素序列）；测试可整体替换。"""

_PHONEME_CACHE: dict = {}


def _cmu_lookup(word: str) -> Optional[List[str]]:
    """cmudict-0.7b 查询（首选发音；文件缺失/解析失败返回 None）。"""
    global _CMU_LOOKUP_CACHE
    if _CMU_LOOKUP_CACHE is None:
        table: dict = {}

        def _load() -> Callable[[str], Optional[List[str]]]:
            try:
                from strange_uta_game.backend.infrastructure.parsers.e2k_engine import (
                    EnglishToKanaEngine,
                )

                path = EnglishToKanaEngine._resolve_cmudict_path()
            except Exception:
                return lambda w: None
            if path is None:
                return lambda w: None
            try:
                with open(path, "r", encoding="latin-1", errors="ignore") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) < 2:
                            continue
                        raw = parts[0]
                        # 变体 WORD(2)/… 跳过，仅取首选发音
                        if raw.endswith(")") and "(" in raw:
                            continue
                        if not raw or not raw[0].isalpha():
                            continue
                        table[raw.lower()] = parts[1:]
            except Exception:
                return lambda w: None
            return table.get

        _CMU_LOOKUP_CACHE = _load()
    return _CMU_LOOKUP_CACHE(word)


def _phoneme_syllabify(phonemes: List[str]) -> List[List[str]]:
    """音素序列 → 音节分组（元音为核，节首辅音最大化；FA-Kara 同款）。

    两个元音之间的辅音串：多于一个时第一个辅音归前一音节的节尾，
    其余归下一音节节首；词尾剩余辅音并入最后一个音节。
    """
    vowel_positions = [
        i
        for i, ph in enumerate(phonemes)
        if ph.rstrip("012") in _CMU_VOWELS
    ]
    if not vowel_positions:
        return [list(phonemes)]
    syllables: List[List[str]] = []
    prev = -1
    for vi in vowel_positions:
        if not syllables:
            syllables.append(list(phonemes[: vi + 1]))
        else:
            consonants = phonemes[prev + 1 : vi]
            if consonants:
                onset_start = 0
                if len(consonants) > 1:
                    syllables[-1].append(consonants[0])
                    onset_start = 1
                syllables.append(consonants[onset_start:] + [phonemes[vi]])
            else:
                syllables.append([phonemes[vi]])
        prev = vi
    if prev < len(phonemes) - 1:
        syllables[-1].extend(phonemes[prev + 1 :])
    return syllables


def _merge_to_count(items: List[str], count: int) -> List[str]:
    """把较长的读音音节列表均匀合并到 count 个（FA-Kara 对齐口径：
    后面的段多分一个元素），数量相符时原样返回。"""
    if len(items) <= count:
        return list(items)
    base, extra = divmod(len(items), count)
    merged: List[str] = []
    start = 0
    for i in range(count):
        size = base + (1 if i >= count - extra else 0)
        merged.append("".join(items[start : start + size]))
        start += size
    return merged


def english_word_phoneme_syllables(word: str) -> Optional[List[str]]:
    """英文词 → 音素口径的音节罗马字列表；CMU 未收录返回 None。

    FA-Kara 口径：pyphen 拼写音节数决定 token 数，读音音节多于拼写
    音节时按段合并（take→T EY K→["tei","k"]→拼写 1 音节→["teik"]）。
    命中结果逐音节独立对齐（alignment 侧不进 word_groups）。
    """
    word = word.strip()
    if not word:
        return None
    if word == "a":
        return ["a"]
    if word == "A":
        return ["ei"]
    key = word.lower()
    if key in _PHONEME_CACHE:
        return list(_PHONEME_CACHE[key]) if _PHONEME_CACHE[key] else None

    result: Optional[List[str]] = None
    phonemes = _cmu_lookup(key)
    dic = _pyphen_dic()
    if phonemes and dic is not None:
        surface_count = len(
            [s for s in dic.inserted(word).split("-") if s]
        ) or 1
        groups = _phoneme_syllabify(phonemes)
        roms = [
            "".join(
                _CMU_PHONEME_ROMAJI.get(ph.rstrip("012"), "")
                for ph in group
            )
            for group in groups
        ]
        roms = [r for r in roms if r]
        if roms:
            result = _merge_to_count(roms, surface_count)

    _PHONEME_CACHE[key] = list(result) if result else []
    return list(result) if result else None


# ── 数字 → 英文读音（FA-Kara number_to_english 移植）──

_NUM_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]
_NUM_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
]


def number_to_english(number_str: str) -> str:
    """阿拉伯数字 → 英文读法（整数与小数；解析失败返回空串）。"""
    try:
        if "." in number_str:
            num = float(number_str)
        else:
            num = int(number_str)
    except ValueError:
        return ""

    if isinstance(num, float):
        integer_part = int(num)
        decimal_part = round(num - integer_part, 3)
        words = (
            number_to_english(str(integer_part)) if integer_part > 0 else ""
        )
        decimal_str = f"{decimal_part:.3f}"[2:]
        decimal_words = " point"
        for digit in decimal_str:
            if digit == "0" and not decimal_words.endswith(" zero"):
                decimal_words += " zero"
            elif digit != "0":
                decimal_words += " " + _NUM_ONES[int(digit)]
        return (words + decimal_words).strip()

    if num < 0:
        return "minus " + number_to_english(str(abs(num)))
    if num < 20:
        return _NUM_ONES[num]
    if num < 100:
        return _NUM_TENS[num // 10] + (
            (" " + _NUM_ONES[num % 10]) if num % 10 != 0 else ""
        )
    if num < 1000:
        return _NUM_ONES[num // 100] + " hundred" + (
            (" and " + number_to_english(str(num % 100)))
            if num % 100 != 0
            else ""
        )
    for scale_value, scale_name in (
        (10**12, "trillion"),
        (10**9, "billion"),
        (10**6, "million"),
        (10**3, "thousand"),
    ):
        if num >= scale_value:
            return number_to_english(str(num // scale_value)) + " " + (
                scale_name
            ) + (
                (" " + number_to_english(str(num % scale_value)))
                if num % scale_value != 0
                else ""
            )
    return ""


def english_number_reading(number_str: str) -> str:
    """数字 → 英文读法 → 音素口径罗马字（单一 token 文本；失败返回空串）。

    数字单位通常是一个时间点（整段读唱），与 FA-Kara 的 surf=False
    同口径：全部音节读音拼成一个字符串。读音优先走 CMU 音素表，
    未收录回退 e2k 按拍口径。
    """
    words = number_to_english(number_str).split()
    if not words:
        return ""
    parts: List[str] = []
    for word in words:
        syllables = english_word_phoneme_syllables(word)
        if syllables is None:
            syllables = english_word_syllables(word)
        if syllables:
            parts.append("".join(syllables))
    return "".join(parts).strip()


__all__ = [
    "pinyin_to_phonetic",
    "english_word_syllables",
    "english_word_phoneme_syllables",
    "number_to_english",
    "english_number_reading",
]
