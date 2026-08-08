"""春日向注音格式（单注音 / 双注音带罗马音）序列化。

格式一（单注音）:
  [MM:SS:cc]char[MM:SS:cc]char{kanji|[MM:SS:cc]kana[MM:SS:cc]kana}[MM:SS:cc]char

格式二（双注音带罗马音）:
  {kanji|[MM:SS:cc]kana[MM:SS:cc]kana>[MM:SS:cc]romaji[MM:SS:cc]romaji}
  {kana|>[MM:SS:cc]romaji}          （纯假名，无假名注音层）
"""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Optional, Tuple

from strange_uta_game.backend.domain.entities import Sentence
from strange_uta_game.backend.domain.models import Character, RubyPart
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    format_timestamp,
)
from strange_uta_game.backend.infrastructure.parsers.romaji import (
    romanize_sentence_to_self_ruby,
)

# 小假名中，拗音（ゃゅょ等）合并到前一拍；促音（っ）和长音（ー）各为一拍
_YOUON = set("ゃゅょャュョ")
_SOKUON = set("っッ")
_LONG_VOWEL = set("ー")


def _split_kana_moras(text: str) -> List[str]:
    """将假名文本按拍（モーラ）拆分。"""
    if not text:
        return []
    moras: List[str] = []
    for ch in text:
        if ch in _YOUON and moras:
            moras[-1] += ch
        elif ch in _SOKUON or ch in _LONG_VOWEL:
            moras.append(ch)
        else:
            moras.append(ch)
    return moras


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
    base = "".join(ch.char for ch in group)
    tagged: List[str] = []
    for ch in group:
        if ch.ruby:
            tagged.append(
                _build_tagged_parts([part.text for part in ch.ruby.parts], _get_ts_list(ch))
            )
    annotation = "".join(tagged)
    if not annotation:
        return "".join(_char_plain(ch) for ch in group)
    return f"{{{base}|{annotation}}}{_format_sentence_end(group[-1])}"


# ── 双注音格式（带罗马音） ──


def sentence_to_kasugamuki_romaji(sentence: Sentence) -> str:
    """一行 → 春日向双注音格式（假名 + 罗马音）。"""
    chars = sentence.characters
    # Use the exact same sentence-level path as 注音管理 -> 转罗马音.  Work on
    # a copy because exporting must not modify the open project.
    romanized_sentence = deepcopy(sentence)
    romanize_sentence_to_self_ruby(romanized_sentence)
    romaji_by_char = {
        i: [part.text for part in ch.ruby.parts]
        for i, ch in enumerate(romanized_sentence.characters)
        if ch.ruby
    }

    segments: List[str] = []
    i = 0
    while i < len(chars):
        char = chars[i]
        if char.linked_to_next and i + 1 < len(chars):
            group_start = i
            group, i = _collect_linked(chars, i)
            segments.append(
                _linked_group_romaji(group, group_start, romaji_by_char)
            )
        elif char.ruby:
            segments.append(
                _char_ruby_romaji(char, romaji_by_char.get(i, []))
            )
            i += 1
        elif _is_kana_text(char.char):
            if not any(romaji_by_char.get(i, [])):
                # Romanization stores a sokuon's combined pronunciation on the
                # affected following kana.  It is still one pronunciation unit,
                # so keep the sokuon and that mora in the same export block.
                if (
                    char.char in ("っ", "ッ")
                    and i + 1 < len(chars)
                    and not chars[i + 1].ruby
                    and _is_kana_text(chars[i + 1].char)
                    and any(romaji_by_char.get(i + 1, []))
                ):
                    mora_end = i + 2
                    while (
                        mora_end < len(chars)
                        and not chars[mora_end].ruby
                        and _is_kana_text(chars[mora_end].char)
                        and not any(romaji_by_char.get(mora_end, []))
                    ):
                        mora_end += 1
                    segments.append(
                        _self_kana_group_romaji(
                            chars[i:mora_end],
                            romaji_by_char.get(i + 1, []),
                            romaji_char_index=1,
                        )
                    )
                    i = mora_end
                    continue

                # Other consumed/unsupported kana has no annotation of its own.
                segments.append(_char_plain(char))
                i += 1
                continue
            mora_end = i + 1
            # The shared converter stores a cross-character digraph/sokuon on
            # its leading part and leaves consumed following parts empty.
            while (
                mora_end < len(chars)
                and not chars[mora_end].ruby
                and _is_kana_text(chars[mora_end].char)
                and chars[mora_end].char not in ("っ", "ッ")
                and not any(romaji_by_char.get(mora_end, []))
            ):
                mora_end += 1
            if mora_end > i + 1:
                segments.append(
                    _self_kana_group_romaji(
                        chars[i:mora_end], romaji_by_char.get(i, [])
                    )
                )
                i = mora_end
            else:
                segments.append(
                    _char_self_ruby_romaji(char, romaji_by_char.get(i, []))
                )
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
    romaji_list: List[str],
) -> str:
    assert char.ruby is not None
    ts_list = _get_ts_list(char)
    parts = char.ruby.parts
    kana_texts = [p.text for p in parts]
    kana_tagged = _build_tagged_parts(kana_texts, ts_list)

    romaji_tagged = _build_tagged_parts(romaji_list, ts_list)

    return f"{{{char.char}|{kana_tagged}>{romaji_tagged}}}{_format_sentence_end(char)}"


def _char_self_ruby_romaji(char: Character, romaji_list: List[str]) -> str:
    ts_list = _get_ts_list(char)
    romaji_tagged = _build_tagged_parts(romaji_list, ts_list)
    return f"{{{char.char}|>{romaji_tagged}}}{_format_sentence_end(char)}"


def _self_kana_group_romaji(
    group: List[Character],
    romaji_list: List[str],
    *,
    romaji_char_index: int = 0,
) -> str:
    base = "".join(ch.char for ch in group)
    romaji_tagged = _build_tagged_parts(
        romaji_list, _get_ts_list(group[romaji_char_index])
    )
    return f"{{{base}|>{romaji_tagged}}}{_format_sentence_end(group[-1])}"


def _linked_group_romaji(
    group: List[Character],
    group_start: int,
    romaji_by_char: Dict[int, List[str]],
) -> str:
    base = "".join(ch.char for ch in group)
    kana_tagged: List[str] = []
    romaji_tagged: List[str] = []
    for local_idx, ch in enumerate(group):
        ts_list = _get_ts_list(ch)
        if ch.ruby:
            kana_tagged.append(
                _build_tagged_parts([part.text for part in ch.ruby.parts], ts_list)
            )
        romaji = romaji_by_char.get(group_start + local_idx, [])
        if romaji:
            romaji_tagged.append(_build_tagged_parts(romaji, ts_list))
    kana_annotation = "".join(kana_tagged)
    romaji_annotation = "".join(romaji_tagged)
    if not kana_annotation and not romaji_annotation:
        return "".join(_char_plain(ch) for ch in group)
    return (
        f"{{{base}|{kana_annotation}>{romaji_annotation}}}"
        f"{_format_sentence_end(group[-1])}"
    )


def _linked_group_common(
    group: List[Character],
    *,
    with_romaji: bool,
    char_start: Dict[int, int],
    particle_indices: set,
    group_start: int,
    romaji_by_char: Optional[Dict[int, List[str]]] = None,
) -> str:
    """处理连词组，合并连续假名字符的罗马音。"""
    # 先把 group 分成若干批次：连续的无 ruby 假名字符合并，其他各成一个批次
    batches: List[Tuple[int, List[Character]]] = []  # (start_in_group, chars)
    i = 0
    while i < len(group):
        ch = group[i]
        if not ch.ruby and _is_kana_text(ch.char):
            batch_start = i
            batch_chars = []
            while i < len(group) and not group[i].ruby and _is_kana_text(group[i].char):
                batch_chars.append(group[i])
                i += 1
            batches.append((batch_start, batch_chars))
        else:
            batches.append((i, [ch]))
            i += 1

    result_parts: List[Optional[str]] = [None] * len(group)
    for batch_start, batch_chars in batches:
        ch = batch_chars[0]
        if len(batch_chars) == 1 or ch.ruby or not _is_kana_text(ch.char):
            # 单人批次，走原逻辑
            char_idx = group_start + batch_start
            if ch.ruby:
                if with_romaji:
                    result_parts[batch_start] = _char_ruby_romaji(
                        ch, (romaji_by_char or {}).get(char_idx, []),
                    )
                else:
                    result_parts[batch_start] = _char_ruby_kana(ch)
            elif with_romaji and _is_kana_text(ch.char):
                result_parts[batch_start] = _char_self_ruby_romaji(
                    ch, (romaji_by_char or {}).get(char_idx, []),
                )
            else:
                result_parts[batch_start] = _char_plain(ch)
        else:
            # 连续假名批次：合并全部文本后统一 mora 拆分和罗马化
            _process_kana_batch(
                batch_chars, batch_start, result_parts,
                with_romaji=with_romaji,
                group_start=group_start,
                romaji_by_char=romaji_by_char,
            )

    return "".join(p for p in result_parts if p is not None)


def _process_kana_batch(
    batch: List[Character],
    batch_start: int,
    result_parts: List[Optional[str]],
    *,
    with_romaji: bool,
    group_start: int,
    romaji_by_char: Optional[Dict[int, List[str]]],
) -> None:
    """连续假名字符：按字符粒度传入 romanize_ruby_parts，让其自行处理跨字符拗音拆分。
    然后每个字符各自拿到自己那部分 romaji。"""
    # 收集每个字符的假名文本（按字符粒度，不做 mora 合并）
    char_kana_list = [ch.char for ch in batch]

    if with_romaji:
        char_romaji_list = [
            "".join((romaji_by_char or {}).get(group_start + batch_start + i, []))
            for i in range(len(batch))
        ]
    else:
        char_romaji_list = char_kana_list

    for bi, ch in enumerate(batch):
        ts_list = _get_ts_list(ch)
        rm = char_romaji_list[bi] if bi < len(char_romaji_list) else ""
        km = char_kana_list[bi]

        if not rm:
            result_parts[batch_start + bi] = ""
            continue

        if with_romaji:
            romaji_tagged = _build_tagged_parts([rm], ts_list)
            result_parts[batch_start + bi] = (
                f"{{{ch.char}|>{romaji_tagged}}}{_format_sentence_end(ch)}"
            )
        else:
            kana_tagged = _build_tagged_parts([km], ts_list)
            result_parts[batch_start + bi] = (
                f"{{{ch.char}|{kana_tagged}}}{_format_sentence_end(ch)}"
            )


def _collect_linked(
    chars: List[Character], start: int
) -> Tuple[List[Character], int]:
    group = [chars[start]]
    i = start + 1
    while i < len(chars) and chars[i - 1].linked_to_next:
        group.append(chars[i])
        i += 1
    return group, i
