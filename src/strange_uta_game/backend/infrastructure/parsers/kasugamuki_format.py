"""春日向注音格式（单注音 / 双注音带罗马音）序列化。

格式一（单注音）:
  [MM:SS:cc]char[MM:SS:cc]char{kanji|[MM:SS:cc]kana[MM:SS:cc]kana}[MM:SS:cc]char

格式二（双注音带罗马音）:
  {kanji|[MM:SS:cc]kana[MM:SS:cc]kana>[MM:SS:cc]romaji[MM:SS:cc]romaji}
  {kana|>[MM:SS:cc]romaji}          （纯假名，无假名注音层）
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from strange_uta_game.backend.domain.entities import Sentence
from strange_uta_game.backend.domain.models import Character, RubyPart
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    format_timestamp,
    split_into_moras,
)
from strange_uta_game.backend.infrastructure.parsers.romaji import (
    detect_particle_part_indices,
    romanize_ruby_parts,
)


def _get_ts_list(char: Character) -> List[int]:
    if char.global_timestamps and len(char.global_timestamps) == len(char.timestamps):
        return char.global_timestamps
    return char.timestamps


def _get_sentence_end_ts(char: Character) -> Optional[int]:
    if char.sentence_end_ts is None:
        return None
    if char.global_sentence_end_ts is not None:
        return char.global_sentence_end_ts
    return char.sentence_end_ts


def _format_sentence_end(char: Character) -> str:
    """如果字符是句尾，返回句尾时间标签字符串；否则返回空串。"""
    if not char.is_sentence_end:
        return ""
    se_ts = _get_sentence_end_ts(char)
    if se_ts is not None:
        return f"[{format_timestamp(se_ts)}]"
    return ""


def _is_kana_text(text: str) -> bool:
    if not text:
        return False
    from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
        CharType,
        get_char_type,
    )
    for ch in text:
        if get_char_type(ch) not in (
            CharType.HIRAGANA,
            CharType.KATAKANA,
            CharType.SOKUON,
            CharType.LONG_VOWEL,
        ):
            return False
    return True


# ── 全局 part 映射（用于罗马音粒子检测） ──


def _build_global_part_index(
    chars: List[Character],
) -> Tuple[List[Tuple[int, int, str]], Dict[int, int]]:
    """构建全局 part 列表和每个字符的起始 part 索引。

    Returns:
        part_list: [(char_idx, part_local_idx, text), ...]
        char_start: {char_idx: global_part_start_idx}
    """
    part_list: List[Tuple[int, int, str]] = []
    char_start: Dict[int, int] = {}
    gi = 0
    for ci, ch in enumerate(chars):
        if ch.ruby:
            char_start[ci] = gi
            for pi, part in enumerate(ch.ruby.parts):
                part_list.append((ci, pi, part.text))
                gi += 1
    return part_list, char_start


def _char_particle_indices(
    char_idx: int,
    parts: List[RubyPart],
    char_start: Dict[int, int],
    particle_indices: set,
) -> List[int]:
    """将全局 particle_indices 映射回指定字符的 local part 索引。"""
    start = char_start.get(char_idx)
    if start is None:
        return []
    result: List[int] = []
    for pi in range(len(parts)):
        if (start + pi) in particle_indices:
            result.append(pi)
    return result


# ── 时间标签 + 文本拼接 ──


def _build_tagged_parts(
    parts: List[str], ts_list: List[int]
) -> str:
    """构造带时间标签的文本串。

    格式: [ts0]text0[ts1]text1[ts2]text2...
    """
    result: List[str] = []
    for i, text in enumerate(parts):
        if not text:
            continue
        if i < len(ts_list):
            result.append(f"[{format_timestamp(ts_list[i])}]")
        result.append(text)
    return "".join(result)


# ── 单注音格式 ──


def sentence_to_kasugamuki(sentence: Sentence) -> str:
    """一行 → 春日向单注音格式。"""
    segments: List[str] = []
    chars = sentence.characters
    i = 0
    while i < len(chars):
        char = chars[i]
        if char.linked_to_next and i + 1 < len(chars):
            group, i = _collect_linked(chars, i)
            segments.append(_linked_group_kana(group))
        elif char.ruby:
            segments.append(_char_ruby_kana(char))
            i += 1
        else:
            segments.append(_char_plain(char))
            i += 1
    return "".join(segments)


def sentences_to_kasugamuki(sentences: List[Sentence]) -> str:
    return "\n".join(sentence_to_kasugamuki(s) for s in sentences)


def _char_plain(char: Character) -> str:
    ts_list = _get_ts_list(char)
    base = ""
    if ts_list:
        base = f"[{format_timestamp(ts_list[0])}]{char.char}"
    else:
        base = char.char
    return base + _format_sentence_end(char)


def _char_ruby_kana(char: Character) -> str:
    assert char.ruby is not None
    kana_texts = [p.text for p in char.ruby.parts]
    ts_list = _get_ts_list(char)
    tagged = _build_tagged_parts(kana_texts, ts_list)
    return f"{{{char.char}|{tagged}}}{_format_sentence_end(char)}"


def _linked_group_kana(group: List[Character]) -> str:
    result: List[str] = []
    for ch in group:
        if ch.ruby:
            result.append(_char_ruby_kana(ch))
        else:
            result.append(_char_plain(ch))
    return "".join(result)


# ── 双注音格式（带罗马音） ──


def sentence_to_kasugamuki_romaji(sentence: Sentence) -> str:
    """一行 → 春日向双注音格式（假名 + 罗马音）。"""
    chars = sentence.characters
    particle_indices = detect_particle_part_indices(sentence)
    part_map, char_start = _build_global_part_index(chars)

    segments: List[str] = []
    i = 0
    while i < len(chars):
        char = chars[i]
        if char.linked_to_next and i + 1 < len(chars):
            group, i = _collect_linked(chars, i)
            segments.append(
                _linked_group_romaji(group, char_start, particle_indices)
            )
        elif char.ruby:
            segments.append(
                _char_ruby_romaji(char, i, char_start, particle_indices)
            )
            i += 1
        elif _is_kana_text(char.char):
            segments.append(_char_self_ruby_romaji(char))
            i += 1
        else:
            # 非假名无注音 → 回退到单注音格式
            segments.append(_char_plain(char))
            i += 1
    return "".join(segments)


def sentences_to_kasugamuki_romaji(sentences: List[Sentence]) -> str:
    return "\n".join(sentence_to_kasugamuki_romaji(s) for s in sentences)


def _char_ruby_romaji(
    char: Character,
    char_idx: int,
    char_start: Dict[int, int],
    particle_indices: set,
) -> str:
    assert char.ruby is not None
    ts_list = _get_ts_list(char)
    parts = char.ruby.parts
    kana_texts = [p.text for p in parts]
    kana_tagged = _build_tagged_parts(kana_texts, ts_list)

    local_pi = _char_particle_indices(char_idx, parts, char_start, particle_indices)
    romaji_list = romanize_ruby_parts(kana_texts, particle_indices=local_pi)
    romaji_tagged = _build_tagged_parts(romaji_list, ts_list)

    return f"{{{char.char}|{kana_tagged}>{romaji_tagged}}}{_format_sentence_end(char)}"


def _char_self_ruby_romaji(char: Character) -> str:
    ts_list = _get_ts_list(char)
    moras = split_into_moras(char.char)
    romaji_list = romanize_ruby_parts(moras)
    romaji_tagged = _build_tagged_parts(romaji_list, ts_list)
    return f"{{{char.char}|>{romaji_tagged}}}{_format_sentence_end(char)}"


def _linked_group_romaji(
    group: List[Character],
    char_start: Dict[int, int],
    particle_indices: set,
) -> str:
    result: List[str] = []
    for idx, ch in enumerate(group):
        if ch.ruby:
            result.append(
                _char_ruby_romaji(ch, idx, char_start, particle_indices)
            )
        elif _is_kana_text(ch.char):
            result.append(_char_self_ruby_romaji(ch))
        else:
            result.append(_char_plain(ch))
    return "".join(result)


def _collect_linked(
    chars: List[Character], start: int
) -> Tuple[List[Character], int]:
    group = [chars[start]]
    i = start + 1
    while i < len(chars) and chars[i - 1].linked_to_next:
        group.append(chars[i])
        i += 1
    return group, i
