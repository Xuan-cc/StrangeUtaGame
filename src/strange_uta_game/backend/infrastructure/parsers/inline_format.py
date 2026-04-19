"""RhythmicaLyrics 风格内联文本格式 序列化/反序列化。

格式规则:
  {漢字|[N|MM:SS:cc]ruby[MM:SS:cc]ruby}  — Ruby 注音组
  [N|MM:SS:cc]char                         — 普通字符 + checkpoint
  ＋                                       — 多汉字 Ruby 内分隔
  ▨                                        — 休止标记 (is_rest=True)
  [10|...]                                 — 行尾标记 (10 = 1cp + line_end)

N 编码: 数字末尾为 "0" 表示 is_line_end=True，前面的部分为 check_count。
例: "2" → check_count=2, line_end=False
    "10" → check_count=1, line_end=True

时间戳格式: MM:SS:cc (分:秒:厘秒)  例: 00:14:64 = 14640ms
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from strange_uta_game.backend.domain.models import (
    Character,
    Ruby,
    TimeTagType,
)
from strange_uta_game.backend.domain.entities import Sentence


# ──────────────────────────────────────────────
# 时间戳
# ──────────────────────────────────────────────


def format_timestamp(ms: int) -> str:
    """毫秒 → MM:SS:cc"""
    total_s = ms // 1000
    centis = (ms % 1000) // 10
    minutes = total_s // 60
    seconds = total_s % 60
    return f"{minutes:02d}:{seconds:02d}:{centis:02d}"


def parse_timestamp(s: str) -> int:
    """MM:SS:cc → 毫秒"""
    parts = s.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"时间戳格式无效: {s!r} (应为 MM:SS:cc)")
    minutes = int(parts[0])
    seconds = int(parts[1])
    centis = int(parts[2])
    return (minutes * 60 + seconds) * 1000 + centis * 10


# ──────────────────────────────────────────────
# N 编码 (checkpoint count + line_end flag)
# ──────────────────────────────────────────────


def encode_check_n(
    check_count: int, is_line_end: bool, is_sentence_end: bool = False
) -> str:
    """编码 check_count、is_line_end 和 is_sentence_end 到 N 字符串。

    规则: 末尾 "0" 表示 line_end，末尾 "e" 表示 sentence_end。
    """
    suffix = ""
    if is_line_end:
        suffix += "0"
    if is_sentence_end:
        suffix += "e"
    return f"{check_count}{suffix}"


def decode_check_n(n_str: str) -> Tuple[int, bool, bool]:
    """解码 N 字符串到 (check_count, is_line_end, is_sentence_end)。"""
    is_sentence_end = False
    if n_str.endswith("e"):
        is_sentence_end = True
        n_str = n_str[:-1]
    is_line_end = False
    if len(n_str) >= 2 and n_str.endswith("0"):
        is_line_end = True
        n_str = n_str[:-1]
    return int(n_str), is_line_end, is_sentence_end


# ──────────────────────────────────────────────
# Mora 分割 (用于 Ruby 文本拆分)
# ──────────────────────────────────────────────

_SMALL_KANA = set("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー")


def split_into_moras(text: str) -> List[str]:
    """将日语文本按拍（モーラ）拆分。

    小假名 (ゃ, ゅ, ょ, っ 等) 和长音符 (ー) 附属前一拍。
    """
    if not text:
        return []
    moras: List[str] = []
    for ch in text:
        if ch in _SMALL_KANA and moras:
            moras[-1] += ch
        else:
            moras.append(ch)
    return moras


def split_ruby_for_checkpoints(ruby_text: str, total_cps: int) -> List[str]:
    """将 ruby 文本按 checkpoint 数量拆分。

    优先按 mora 对齐；若 mora 数与 cp 数不匹配则均匀分字符。
    """
    if total_cps <= 0:
        return [ruby_text] if ruby_text else []
    if total_cps == 1:
        return [ruby_text]

    moras = split_into_moras(ruby_text)
    if len(moras) == total_cps:
        return moras

    # 均匀分字符
    chars = list(ruby_text)
    if len(chars) <= total_cps:
        # 字符数 ≤ cp 数: 每个 cp 分一个字符，多余 cp 分空串
        result = chars + [""] * (total_cps - len(chars))
        return result

    # 字符数 > cp 数: 尽量均匀
    result = []
    base = len(chars) // total_cps
    extra = len(chars) % total_cps
    pos = 0
    for i in range(total_cps):
        size = base + (1 if i < extra else 0)
        result.append("".join(chars[pos : pos + size]))
        pos += size
    return result


# ──────────────────────────────────────────────
# 序列化: Sentence → 内联文本
# ──────────────────────────────────────────────

REST_CHAR = "▨"
RUBY_SEP = "＋"  # 全角加号


def to_inline_text(sentence: Sentence) -> str:
    """将一个 Sentence 序列化为 RhythmicaLyrics 风格内联文本。"""
    parts: List[str] = []
    chars = sentence.characters
    i = 0

    while i < len(chars):
        char = chars[i]

        if char.ruby:
            # 找连续有 ruby 的字符组
            group_start = i
            while i < len(chars) and chars[i].ruby:
                i += 1
            group_end = i

            # Ruby 组: {display_chars|...per_char_portions...}
            display = "".join(c.char for c in chars[group_start:group_end])
            ruby_portions: List[str] = []

            for c in chars[group_start:group_end]:
                assert c.ruby is not None  # 由上方 while 条件保证
                # 按 checkpoint 数量拆分该字符的 ruby 文本
                ruby_segments = split_ruby_for_checkpoints(c.ruby.text, c.check_count)

                portion_parts: List[str] = []
                for cp_idx in range(c.total_timing_points):
                    if cp_idx < c.check_count:
                        ts = c.timestamps[cp_idx] if cp_idx < len(c.timestamps) else 0
                    else:
                        ts = c.sentence_end_ts or 0
                    ts_str = format_timestamp(ts)

                    if cp_idx == 0:
                        n = encode_check_n(
                            c.check_count, c.is_line_end, c.is_sentence_end
                        )
                        portion_parts.append(f"[{n}|{ts_str}]")
                    else:
                        portion_parts.append(f"[{ts_str}]")

                    if cp_idx < c.check_count and cp_idx < len(ruby_segments):
                        portion_parts.append(ruby_segments[cp_idx])

                ruby_portions.append("".join(portion_parts))

            inner = RUBY_SEP.join(ruby_portions)
            parts.append(f"{{{display}|{inner}}}")
        else:
            # 普通字符
            display_char = REST_CHAR if char.is_rest else char.char
            char_parts: List[str] = []

            for cp_idx in range(char.total_timing_points):
                if cp_idx < char.check_count:
                    ts = char.timestamps[cp_idx] if cp_idx < len(char.timestamps) else 0
                else:
                    ts = char.sentence_end_ts or 0
                ts_str = format_timestamp(ts)

                if cp_idx == 0:
                    n = encode_check_n(
                        char.check_count, char.is_line_end, char.is_sentence_end
                    )
                    char_parts.append(f"[{n}|{ts_str}]")
                else:
                    char_parts.append(f"[{ts_str}]")

            char_parts.append(display_char)
            parts.append("".join(char_parts))
            i += 1

    return "".join(parts)


def sentences_to_inline_text(sentences: List[Sentence]) -> str:
    """多行序列化，换行分隔。"""
    return "\n".join(to_inline_text(s) for s in sentences)


# 兼容别名
lines_to_inline_text = sentences_to_inline_text


# ──────────────────────────────────────────────
# 反序列化: 内联文本 → Sentence
# ──────────────────────────────────────────────

# 正则: 匹配 [N|MM:SS:cc] 或 [MM:SS:cc]（N 可含 "e" 后缀表示 sentence_end）
_TAG_RE = re.compile(r"\[(?:(\d+e?)\|)?(\d{2}:\d{2}:\d{2})\]")


def _parse_char_tokens(segment: str) -> List[Tuple[Optional[str], int, str]]:
    """解析一段文本中的 (n_str|None, timestamp_ms, following_text) 三元组。

    segment 形如 "[2|00:14:64]や[00:15:61]わ" → [(2,14640,"や"), (None,15610,"わ")]
    """
    tokens: List[Tuple[Optional[str], int, str]] = []
    for m in _TAG_RE.finditer(segment):
        n_str = m.group(1)  # None if no N|
        ts_ms = parse_timestamp(m.group(2))
        # text between end of this tag and start of next tag (or end of segment)
        text_start = m.end()
        # find next tag or end
        next_m = _TAG_RE.search(segment, text_start)
        sep_pos = segment.find(RUBY_SEP, text_start)

        if next_m:
            end = next_m.start()
        else:
            end = len(segment)

        # Trim at ＋ separator if it comes before the next tag
        if sep_pos != -1 and sep_pos < end:
            end = sep_pos

        text = segment[text_start:end]
        tokens.append((n_str, ts_ms, text))
    return tokens


def from_inline_text(text: str, singer_id: str) -> Sentence:
    """解析一行内联文本为 Sentence。"""
    characters: List[Character] = []

    # 提取 ruby 组和普通段
    segments = _split_ruby_groups(text)

    for seg_type, seg_content in segments:
        if seg_type == "ruby":
            _parse_ruby_group(seg_content, characters, singer_id)
        else:
            _parse_plain_segment(seg_content, characters, singer_id)

    # 设置 linked_to_next: 当下一个字符 check_count==0 时
    for i in range(len(characters) - 1):
        if characters[i + 1].check_count == 0 and not characters[i].linked_to_next:
            characters[i].linked_to_next = True

    return Sentence(
        singer_id=singer_id,
        characters=characters,
    )


def sentences_from_inline_text(text: str, singer_id: str) -> List[Sentence]:
    """多行解析。"""
    result = []
    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue
        result.append(from_inline_text(stripped, singer_id))
    return result


# 兼容别名
lines_from_inline_text = sentences_from_inline_text


def _split_ruby_groups(text: str) -> List[Tuple[str, str]]:
    """将内联文本拆分为 ("ruby", content) 和 ("plain", content) 段。"""
    result: List[Tuple[str, str]] = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            # 找匹配的 }
            end = text.index("}", i + 1)
            result.append(("ruby", text[i + 1 : end]))
            i = end + 1
        else:
            # 找下一个 { 或结尾
            next_brace = text.find("{", i)
            if next_brace == -1:
                result.append(("plain", text[i:]))
                break
            else:
                if next_brace > i:
                    result.append(("plain", text[i:next_brace]))
                i = next_brace
    return result


def _parse_ruby_group(
    content: str,
    characters: List[Character],
    singer_id: str,
) -> None:
    """解析 ruby 组内容 (不含花括号)。

    格式: "漢字|[N|ts]ruby[ts]ruby＋[N|ts]ruby"
    每个字符直接创建 Character 对象，附带 per-char Ruby。
    """
    pipe_pos = content.index("|")
    display_text = content[:pipe_pos]
    ruby_body = content[pipe_pos + 1 :]

    display_chars = list(display_text)

    # 按 ＋ 分割各字符的 ruby 部分
    portions = ruby_body.split(RUBY_SEP)

    for portion_idx, portion in enumerate(portions):
        # 确定对应的显示字符
        char_text = (
            display_chars[portion_idx] if portion_idx < len(display_chars) else "?"
        )
        tokens = _parse_char_tokens(portion)

        if not tokens:
            # 无 checkpoint 信息
            ruby_text = portion.strip()
            ruby_obj = Ruby(text=ruby_text) if ruby_text else None
            character = Character(
                char=char_text,
                ruby=ruby_obj,
                check_count=1,
                singer_id=singer_id,
            )
            character.push_to_ruby()
            characters.append(character)
            continue

        # 第一个 token 确定 N
        first_n_str = tokens[0][0]
        if first_n_str is not None:
            check_count, is_line_end, is_sentence_end = decode_check_n(first_n_str)
        else:
            check_count = len(tokens)
            is_line_end = False
            is_sentence_end = False

        # 收集时间戳和 ruby 文本
        all_timestamps: List[int] = []
        ruby_text_parts: List[str] = []
        for _, ts_ms, seg_text in tokens:
            all_timestamps.append(ts_ms)
            ruby_text_parts.append(seg_text)

        timestamps = all_timestamps[:check_count]
        sentence_end_ts = None
        if is_sentence_end and len(all_timestamps) > check_count:
            sentence_end_ts = all_timestamps[check_count]

        # per-char ruby 文本
        per_char_ruby = "".join(ruby_text_parts)
        ruby_obj = Ruby(text=per_char_ruby) if per_char_ruby else None

        character = Character(
            char=char_text,
            ruby=ruby_obj,
            check_count=check_count,
            timestamps=timestamps,
            sentence_end_ts=sentence_end_ts,
            is_line_end=is_line_end,
            is_sentence_end=is_sentence_end,
            singer_id=singer_id,
        )
        character.push_to_ruby()
        characters.append(character)


def _parse_plain_segment(
    content: str,
    characters: List[Character],
    singer_id: str,
) -> None:
    """解析普通段 (非 ruby 组)。

    格式: "[N|ts]char[ts]..." 可能包含多个字符。
    """
    pos = 0
    pending_tags: List[Tuple[Optional[str], int]] = []

    while pos < len(content):
        m = _TAG_RE.match(content, pos)
        if m:
            n_str = m.group(1)
            ts_ms = parse_timestamp(m.group(2))
            pos = m.end()

            # 查看紧跟的文本字符
            if pos < len(content) and content[pos] not in "[{":
                ch = content[pos]
                pos += 1

                if n_str is not None:
                    # 新字符起始
                    if pending_tags:
                        _flush_pending(pending_tags, characters, singer_id)
                        pending_tags = []
                    pending_tags.append((n_str, ts_ms))
                    # 检查该字符是否还有后续 checkpoint tag (无 N 前缀)
                    while pos < len(content):
                        m2 = _TAG_RE.match(content, pos)
                        if m2 and m2.group(1) is None:
                            ts2 = parse_timestamp(m2.group(2))
                            pending_tags.append((None, ts2))
                            pos = m2.end()
                            # 吃掉可能的文本 (不应该有，但安全处理)
                            if pos < len(content) and content[pos] not in "[{":
                                pos += 1
                        else:
                            break

                    is_rest = ch == REST_CHAR
                    first_n = pending_tags[0][0]
                    if first_n is not None:
                        check_count, is_line_end, is_sentence_end = decode_check_n(
                            first_n
                        )
                    else:
                        check_count = len(pending_tags)
                        is_line_end = False
                        is_sentence_end = False

                    all_timestamps = [ts for _, ts in pending_tags]
                    timestamps = all_timestamps[:check_count]
                    sentence_end_ts = None
                    if is_sentence_end and len(all_timestamps) > check_count:
                        sentence_end_ts = all_timestamps[check_count]

                    character = Character(
                        char=ch,
                        check_count=check_count,
                        timestamps=timestamps,
                        sentence_end_ts=sentence_end_ts,
                        is_line_end=is_line_end,
                        is_sentence_end=is_sentence_end,
                        is_rest=is_rest,
                        singer_id=singer_id,
                    )
                    characters.append(character)
                    pending_tags = []
                else:
                    # 后续 checkpoint（归属前一个字符）
                    pending_tags.append((None, ts_ms))
            else:
                pending_tags.append((n_str, ts_ms))
        else:
            # 非 tag 文本 — 不应该出现，跳过
            pos += 1

    if pending_tags:
        _flush_pending(pending_tags, characters, singer_id)


def _flush_pending(
    pending_tags: List[Tuple[Optional[str], int]],
    characters: List[Character],
    singer_id: str,
) -> None:
    """将未消费的 pending_tags 作为无字符 checkpoint 刷出。"""
    if not pending_tags:
        return
    first_n = pending_tags[0][0]
    if first_n is not None:
        check_count, is_line_end, is_sentence_end = decode_check_n(first_n)
    else:
        check_count = len(pending_tags)
        is_line_end = False
        is_sentence_end = False

    all_timestamps = [ts for _, ts in pending_tags]
    timestamps = all_timestamps[:check_count]
    sentence_end_ts = None
    if is_sentence_end and len(all_timestamps) > check_count:
        sentence_end_ts = all_timestamps[check_count]

    character = Character(
        char="?",
        check_count=check_count,
        timestamps=timestamps,
        sentence_end_ts=sentence_end_ts,
        is_line_end=is_line_end,
        is_sentence_end=is_sentence_end,
        singer_id=singer_id,
    )
    characters.append(character)
