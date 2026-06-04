"""假名→赫本式罗马音转换器（纯打表，零依赖）。"""

from __future__ import annotations
from typing import Iterable, List, Optional, Sequence, Tuple

_VOWELS = frozenset("aeiou")
# \u52a9\u8bcd\u8bfb\u97f3\u8986\u76d6\u8868\uff08\u8d6b\u672c\u5f0f\uff09\uff1a\u306f\u2192wa, \u3078\u2192e, \u3092\u2192o\u3002
# \u3092 \u5728 _KANA \u8868\u4e2d\u4e5f\u5df2\u6620\u5c04\u4e3a "o"\uff0c\u56e0\u6b64 particle_indices \u5bf9 \u3092 \u7684\u5224\u5b9a\u7ed3\u679c\u4e0d\u5f71\u54cd
# \u6700\u7ec8\u8f93\u51fa\uff1b\u6b64\u5904\u4fdd\u7559\u662f\u4e3a\u4e86\u8bed\u4e49\u5b8c\u6574\u6027\uff0c\u82e5\u5c06\u6765 _KANA["\u3092"] \u6539\u56de "wo" \u65f6\u903b\u8f91\u4ecd\u6b63\u786e\u3002
_PARTICLE_ROMAJI = {"\u306f": "wa", "\u3078": "e", "\u3092": "o"}

_KANA: dict[str, str] = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o",
    "ん": "n", "ゔ": "vu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
}

_DIGRAPHS: dict[str, str] = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "ぢゃ": "ja", "ぢゅ": "ju", "ぢょ": "jo",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "しぇ": "she", "じぇ": "je", "ちぇ": "che",
    "つぁ": "tsa", "つぃ": "tsi", "つぇ": "tse", "つぉ": "tso",
    "くぁ": "kwa", "ぐぁ": "gwa",
}

_DIGRAPH_SPLIT: dict[str, Tuple[str, str]] = {
    "kya": ("ky", "a"), "kyu": ("ky", "u"), "kyo": ("ky", "o"),
    "gya": ("gy", "a"), "gyu": ("gy", "u"), "gyo": ("gy", "o"),
    "sha": ("sh", "a"), "shu": ("sh", "u"), "sho": ("sh", "o"),
    "ja": ("j", "a"), "ju": ("j", "u"), "jo": ("j", "o"),
    "cha": ("ch", "a"), "chu": ("ch", "u"), "cho": ("ch", "o"),
    "nya": ("ny", "a"), "nyu": ("ny", "u"), "nyo": ("ny", "o"),
    "hya": ("hy", "a"), "hyu": ("hy", "u"), "hyo": ("hy", "o"),
    "bya": ("by", "a"), "byu": ("by", "u"), "byo": ("by", "o"),
    "pya": ("py", "a"), "pyu": ("py", "u"), "pyo": ("py", "o"),
    "mya": ("my", "a"), "myu": ("my", "u"), "myo": ("my", "o"),
    "rya": ("ry", "a"), "ryu": ("ry", "u"), "ryo": ("ry", "o"),
}


def _kata_to_hira_char(ch: str) -> str:
    code = ord(ch)
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    return ch


def _last_vowel(text: str) -> str:
    for ch in reversed(text.lower()):
        if ch in _VOWELS:
            return ch
    return ""


def _starts_with_vowel_or_y(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return lowered[0] in _VOWELS or lowered.startswith("y")


def _geminate_prefix(next_romaji: str) -> str:
    lowered = next_romaji.lower()
    if not lowered:
        return ""
    if lowered.startswith("ch"):
        return "t"
    first = lowered[0]
    if first in _VOWELS or first == "n":
        return ""
    return first if first.isalpha() else ""


def _split_digraph(romaji: str) -> Tuple[str, str]:
    if romaji in _DIGRAPH_SPLIT:
        return _DIGRAPH_SPLIT[romaji]
    vowel = _last_vowel(romaji)
    if vowel and romaji.endswith(vowel):
        return romaji[:-1], vowel
    return romaji, ""


def _flat_chars(parts: Sequence[str]) -> List[Tuple[int, str]]:
    return [(part_idx, ch) for part_idx, part in enumerate(parts) for ch in part]


def _romaji_mora_at(flat: Sequence[Tuple[int, str]], index: int) -> str:
    if index >= len(flat):
        return ""
    _, ch = flat[index]
    hira = _kata_to_hira_char(ch)
    if hira in ("っ", "ー"):
        return ""
    if hira == "ん":
        return "n"
    if index + 1 < len(flat):
        _, next_ch = flat[index + 1]
        pair = hira + _kata_to_hira_char(next_ch)
        if pair in _DIGRAPHS:
            return _DIGRAPHS[pair]
    return _KANA.get(hira, ch)


def romanize_ruby_parts(
    parts: Iterable[str],
    particle_indices: Optional[Iterable[int]] = None,
) -> List[str]:
    """将假名 RubyPart 文本逐一转为赫本式罗马音，保持 part 数量不变。"""
    source = list(parts)
    result = ["" for _ in source]
    flat = _flat_chars(source)
    particle_set = set(particle_indices or ())
    prev_vowel = ""
    index = 0

    while index < len(flat):
        part_idx, ch = flat[index]
        hira = _kata_to_hira_char(ch)

        if (
            part_idx in particle_set
            and len(source[part_idx]) == 1
            and hira in _PARTICLE_ROMAJI
        ):
            romaji = _PARTICLE_ROMAJI[hira]
            result[part_idx] += romaji
            prev_vowel = _last_vowel(romaji)
            index += 1
            continue

        if hira == "っ":
            next_romaji = _romaji_mora_at(flat, index + 1)
            prefix = _geminate_prefix(next_romaji)
            result[part_idx] += prefix if prefix else "xtsu"
            index += 1
            continue

        if hira == "ー":
            repeated = prev_vowel or "-"
            result[part_idx] += repeated
            prev_vowel = repeated if repeated in _VOWELS else ""
            index += 1
            continue

        if hira == "ん":
            next_romaji = _romaji_mora_at(flat, index + 1)
            romaji = "n'" if _starts_with_vowel_or_y(next_romaji) else "n"
            result[part_idx] += romaji
            prev_vowel = ""
            index += 1
            continue

        if index + 1 < len(flat):
            next_part_idx, next_ch = flat[index + 1]
            pair = hira + _kata_to_hira_char(next_ch)
            digraph = _DIGRAPHS.get(pair)
            if digraph is not None:
                if next_part_idx == part_idx:
                    result[part_idx] += digraph
                else:
                    head, tail = _split_digraph(digraph)
                    result[part_idx] += head
                    result[next_part_idx] += tail
                prev_vowel = _last_vowel(digraph)
                index += 2
                continue

        romaji = _KANA.get(hira, ch)
        result[part_idx] += romaji
        prev_vowel = _last_vowel(romaji) or prev_vowel
        index += 1

    return result
