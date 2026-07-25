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
        # Modern/common Hepburn-style spelling: っち/っちゃ → cchi/ccha.
        return "c"
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
            if prefix and index + 1 < len(flat):
                # A sokuon and its following mora form one pronunciation unit.
                # Keep that unit on the sokuon's part and leave the consumed
                # following part empty, so serializers produce e.g. ``tte``
                # instead of separate ``t`` / ``te`` annotations.
                result[part_idx] += prefix + next_romaji
                prev_vowel = _last_vowel(next_romaji)
                index += 2
                if index < len(flat):
                    _, maybe_small = flat[index]
                    pair = (
                        _kata_to_hira_char(flat[index - 1][1])
                        + _kata_to_hira_char(maybe_small)
                    )
                    if pair in _DIGRAPHS:
                        index += 1
            else:
                result[part_idx] += "xtsu"
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
                # A digraph is one mora.  Store it on its leading part even
                # when the small kana lives in the next RubyPart.
                result[part_idx] += digraph
                prev_vowel = _last_vowel(digraph)
                index += 2
                continue

        romaji = _KANA.get(hira, ch)
        result[part_idx] += romaji
        prev_vowel = _last_vowel(romaji) or prev_vowel
        index += 1

    return result


# ──────────────────────────────────────────────
# 句子/项目级罗马音转换（纯逻辑，独立于注音分析流程）
#
# 以下函数被 AutoCheckService（受 romanize_ruby 设置开关）与工具栏的
# 「全部转为罗马字注音」一次性操作共同复用。本层不调用注音引擎、不更新节奏点、
# 不删除注音——仅就地把假名 ruby 文本转为罗马音，并给无 ruby 的单假名补自注音。
# 依赖（domain / text_splitter）按需延迟导入，保持模块加载期零依赖。
# ──────────────────────────────────────────────


def is_self_romanizable_kana(char: str) -> bool:
    """单字符是否为可自注音的假名（平假名/片假名/促音/长音）。"""
    if len(char) != 1:
        return False
    from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
        CharType,
        get_char_type,
    )

    return get_char_type(char) in (
        CharType.HIRAGANA,
        CharType.KATAKANA,
        CharType.SOKUON,
        CharType.LONG_VOWEL,
    )


def detect_particle_part_indices(sentence) -> set:
    """检测句中作为助词的 は/へ/を 所在的 ruby part 全局索引（罗马音读音覆盖用）。

    part 索引在「按字符、按 part」顺序展开的扁平序列中计数，与
    :func:`romanize_sentence_in_place` 收集 texts 的顺序一致。

    判定规则（保守）：
      - を：恒为助词。
      - は/へ：需前一字符为日文字符；前字为汉字/片假名时直接判为助词，
        前字为假名时仅当后字非假名（或位于句末）才判为助词。
    """
    from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
        CharType,
        get_char_type,
    )

    particle_indices: set = set()
    part_idx = 0
    for char_idx, ch in enumerate(sentence.characters):
        if not ch.ruby:
            continue
        for part in ch.ruby.parts:
            text = part.text
            if len(text) != 1 or text != ch.char:
                part_idx += 1
                continue
            if text == "を":  # を
                particle_indices.add(part_idx)
                part_idx += 1
                continue
            if text not in ("は", "へ"):  # は / へ
                part_idx += 1
                continue
            if char_idx == 0:
                part_idx += 1
                continue
            prev = sentence.characters[char_idx - 1]
            prev_ct = get_char_type(prev.char) if len(prev.char) == 1 else CharType.OTHER
            if prev_ct not in (
                CharType.KANJI,
                CharType.HIRAGANA,
                CharType.KATAKANA,
                CharType.SOKUON,
                CharType.LONG_VOWEL,
            ):
                part_idx += 1
                continue
            # 前一字符是汉字/片假名 → 词边界信号强，直接判定为助词。
            if prev_ct in (CharType.KANJI, CharType.KATAKANA):
                particle_indices.add(part_idx)
                part_idx += 1
                continue
            # 前一字符是假名：后字非假名（或句末）才判为助词，否则保守不判。
            if char_idx + 1 >= len(sentence.characters):
                particle_indices.add(part_idx)
                part_idx += 1
                continue
            next_ch = sentence.characters[char_idx + 1]
            next_ct = (
                get_char_type(next_ch.char) if len(next_ch.char) == 1 else CharType.OTHER
            )
            if next_ct not in (
                CharType.HIRAGANA,
                CharType.KATAKANA,
                CharType.SOKUON,
                CharType.LONG_VOWEL,
            ):
                particle_indices.add(part_idx)
            part_idx += 1
    return particle_indices


def romanize_sentence_in_place(sentence) -> None:
    """将句中所有假名 ruby part 就地转为赫本式罗马音（含助词/促音上下文）。

    保持每个 ruby 的 part 数量不变，仅改写 part.text；非假名（已是罗马音/英文
    读音）原样保留。
    """
    refs = []
    texts = []
    for ch in sentence.characters:
        if not ch.ruby:
            continue
        for part in ch.ruby.parts:
            refs.append(part)
            texts.append(part.text)
    if not refs:
        return
    particle_indices = detect_particle_part_indices(sentence)
    converted = romanize_ruby_parts(texts, particle_indices=particle_indices)
    for part, text in zip(refs, converted):
        part.text = text


def romanize_sentence_to_self_ruby(sentence) -> bool:
    """Apply the editor's one-click romaji conversion to one sentence.

    Bare single kana first receive self-ruby, then every ruby part is converted
    together so cross-character sokuon/youon/long-vowel context is preserved.
    Returns whether any self-ruby was added.
    """
    from strange_uta_game.backend.domain.models import Ruby, RubyPart

    added_self_ruby = False
    for ch in sentence.characters:
        if ch.ruby or not is_self_romanizable_kana(ch.char):
            continue
        ch.ruby = Ruby(parts=[RubyPart(text=ch.char)])
        ch.set_check_count(max(ch.check_count, 1), force=True)
        added_self_ruby = True

    romanize_sentence_in_place(sentence)
    return added_self_ruby


def romanize_project_to_self_ruby(project, progress_callback=None) -> int:
    """「全部转为罗马字注音」一次性操作。

    两步：
      1. 给无 ruby 的单假名（平假名/片假名/促音/长音）创建自注音
         （假名本身作为 ruby 文本，保持 check_count 不变、至少为 1）；
      2. 整句假名 ruby 就地转罗马音（含助词/促音上下文）。

    不调用注音引擎、不更新节奏点、不删除注音。对已是罗马音的 part 幂等。

    Args:
        project: 目标项目（就地修改）。
        progress_callback: ``(phase, current, total)`` 进度回调，可选。

    Returns:
        发生变化的句数。
    """
    changed = 0
    sentences = project.sentences
    total = len(sentences)
    for i, sentence in enumerate(sentences):
        def _join(sent) -> str:
            return "".join(
                p.text for c in sent.characters if c.ruby for p in c.ruby.parts
            )

        before = _join(sentence)
        touched = romanize_sentence_to_self_ruby(sentence)
        if touched or before != _join(sentence):
            changed += 1
        if progress_callback is not None:
            progress_callback("罗马音转换", i + 1, total)
    return changed
