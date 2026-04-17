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
    CheckpointConfig,
    TimeTag,
    TimeTagType,
    Ruby,
)
from strange_uta_game.backend.domain.entities import LyricLine


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


def encode_check_n(check_count: int, is_line_end: bool) -> str:
    """编码 check_count 和 is_line_end 到 N 字符串。

    规则: 末尾 "0" 表示 line_end。
    """
    if is_line_end:
        return f"{check_count}0"
    return str(check_count)


def decode_check_n(n_str: str) -> Tuple[int, bool]:
    """解码 N 字符串到 (check_count, is_line_end)。"""
    if len(n_str) >= 2 and n_str.endswith("0"):
        return int(n_str[:-1]), True
    return int(n_str), False


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
# 序列化: LyricLine → 内联文本
# ──────────────────────────────────────────────

REST_CHAR = "▨"
RUBY_SEP = "＋"  # 全角加号


def to_inline_text(line: LyricLine) -> str:
    """将一个 LyricLine 序列化为 RhythmicaLyrics 风格内联文本。"""
    parts: List[str] = []
    cp_map = {cp.char_idx: cp for cp in line.checkpoints}
    tag_map: dict[int, list[TimeTag]] = {}
    for t in line.timetags:
        tag_map.setdefault(t.char_idx, []).append(t)

    i = 0
    while i < len(line.chars):
        ruby = line.get_ruby_for_char(i)

        if ruby and ruby.start_idx == i:
            # Ruby 组: {display_chars|...per_char_portions...}
            display = "".join(line.chars[ruby.start_idx : ruby.end_idx])
            ruby_portions: List[str] = []

            # 计算每个字符的 cp 数量以确定 ruby 文本拆分
            char_cp_counts: List[int] = []
            for ci in range(ruby.start_idx, ruby.end_idx):
                cp = cp_map.get(ci)
                char_cp_counts.append(cp.check_count if cp else 1)

            total_cps = sum(char_cp_counts)
            segments = split_ruby_for_checkpoints(ruby.text, total_cps)

            # 分配 segments 到各字符
            seg_idx = 0
            for ci_offset, ci in enumerate(range(ruby.start_idx, ruby.end_idx)):
                cp = cp_map.get(ci)
                check_count = cp.check_count if cp else 1
                is_line_end = cp.is_line_end if cp else False
                tags = sorted(tag_map.get(ci, []), key=lambda t: t.checkpoint_idx)

                portion_parts: List[str] = []
                for cp_idx in range(check_count):
                    # 查找对应 timetag
                    tag = next((t for t in tags if t.checkpoint_idx == cp_idx), None)
                    ts = format_timestamp(tag.timestamp_ms) if tag else "00:00:00"

                    if cp_idx == 0:
                        n = encode_check_n(check_count, is_line_end)
                        portion_parts.append(f"[{n}|{ts}]")
                    else:
                        portion_parts.append(f"[{ts}]")

                    if seg_idx < len(segments):
                        portion_parts.append(segments[seg_idx])
                        seg_idx += 1

                ruby_portions.append("".join(portion_parts))

            inner = RUBY_SEP.join(ruby_portions)
            parts.append(f"{{{display}|{inner}}}")
            i = ruby.end_idx
        else:
            # 普通字符
            ch = line.chars[i]
            cp = cp_map.get(i)
            check_count = cp.check_count if cp else 1
            is_line_end = cp.is_line_end if cp else False
            is_rest = cp.is_rest if cp else False
            tags = sorted(tag_map.get(i, []), key=lambda t: t.checkpoint_idx)

            display_char = REST_CHAR if is_rest else ch
            char_parts: List[str] = []

            for cp_idx in range(check_count):
                tag = next((t for t in tags if t.checkpoint_idx == cp_idx), None)
                ts = format_timestamp(tag.timestamp_ms) if tag else "00:00:00"

                if cp_idx == 0:
                    n = encode_check_n(check_count, is_line_end)
                    char_parts.append(f"[{n}|{ts}]")
                else:
                    char_parts.append(f"[{ts}]")

            char_parts.append(display_char)
            parts.append("".join(char_parts))
            i += 1

    return "".join(parts)


def lines_to_inline_text(lines: List[LyricLine]) -> str:
    """多行序列化，空行分隔。"""
    return "\n".join(to_inline_text(line) for line in lines)


# ──────────────────────────────────────────────
# 反序列化: 内联文本 → LyricLine
# ──────────────────────────────────────────────

# 正则: 匹配 [N|MM:SS:cc] 或 [MM:SS:cc]
_TAG_RE = re.compile(r"\[(?:(\d+)\|)?(\d{2}:\d{2}:\d{2})\]")


def _parse_char_tokens(segment: str) -> List[Tuple[Optional[str], int, str]]:
    """解析一段文本中的 (n_str|None, timestamp_ms, following_text) 三元组。

    segment 形如 "[2|00:14:64]や[00:15:61]わ" → [(2,14640,"や"), (None,15610,"わ")]
    """
    tokens: List[Tuple[Optional[str], int, str]] = []
    pos = 0
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


def from_inline_text(text: str, singer_id: str) -> LyricLine:
    """解析一行内联文本为 LyricLine。"""
    chars: List[str] = []
    checkpoints: List[CheckpointConfig] = []
    timetags: List[TimeTag] = []
    rubies: List[Ruby] = []

    # 提取 ruby 组和普通段
    # 整行扫描，区分 {...} 组和非组段
    segments = _split_ruby_groups(text)

    for seg_type, seg_content in segments:
        if seg_type == "ruby":
            _parse_ruby_group(
                seg_content, chars, checkpoints, timetags, rubies, singer_id
            )
        else:
            _parse_plain_segment(seg_content, chars, checkpoints, timetags, singer_id)

    line_text = "".join(chars)
    return LyricLine(
        singer_id=singer_id,
        text=line_text,
        chars=chars,
        checkpoints=checkpoints,
        timetags=timetags,
        rubies=rubies,
    )


def lines_from_inline_text(text: str, singer_id: str) -> List[LyricLine]:
    """多行解析。"""
    result = []
    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue
        result.append(from_inline_text(stripped, singer_id))
    return result


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
    chars: List[str],
    checkpoints: List[CheckpointConfig],
    timetags: List[TimeTag],
    rubies: List[Ruby],
    singer_id: str,
) -> None:
    """解析 ruby 组内容 (不含花括号)。

    格式: "漢字|[N|ts]ruby[ts]ruby＋[N|ts]ruby"
    """
    pipe_pos = content.index("|")
    display_text = content[:pipe_pos]
    ruby_body = content[pipe_pos + 1 :]

    start_idx = len(chars)
    display_chars = list(display_text)
    chars.extend(display_chars)
    end_idx = len(chars)

    # 按 ＋ 分割各字符的 ruby 部分
    portions = ruby_body.split(RUBY_SEP)

    ruby_text_parts: List[str] = []

    for portion_idx, portion in enumerate(portions):
        char_idx = start_idx + portion_idx
        tokens = _parse_char_tokens(portion)

        if not tokens:
            # 无 checkpoint 信息
            checkpoints.append(CheckpointConfig(char_idx=char_idx, check_count=1))
            ruby_text_parts.append(portion.strip())
            continue

        # 第一个 token 确定 N
        first_n_str = tokens[0][0]
        if first_n_str is not None:
            check_count, is_line_end = decode_check_n(first_n_str)
        else:
            check_count = len(tokens)
            is_line_end = False

        checkpoints.append(
            CheckpointConfig(
                char_idx=char_idx,
                check_count=check_count,
                is_line_end=is_line_end,
            )
        )

        for cp_idx, (_, ts_ms, seg_text) in enumerate(tokens):
            timetags.append(
                TimeTag(
                    timestamp_ms=ts_ms,
                    singer_id=singer_id,
                    char_idx=char_idx,
                    checkpoint_idx=cp_idx,
                )
            )
            ruby_text_parts.append(seg_text)

    # 合并 ruby 文本
    full_ruby_text = "".join(ruby_text_parts)
    if full_ruby_text:
        rubies.append(Ruby(text=full_ruby_text, start_idx=start_idx, end_idx=end_idx))


def _parse_plain_segment(
    content: str,
    chars: List[str],
    checkpoints: List[CheckpointConfig],
    timetags: List[TimeTag],
    singer_id: str,
) -> None:
    """解析普通段 (非 ruby 组)。

    格式: "[N|ts]char[ts]..." 可能包含多个字符。
    """
    # 找所有 tag+text 对
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
                        _flush_pending(
                            pending_tags, chars, checkpoints, timetags, singer_id
                        )
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
                    char_idx = len(chars)
                    chars.append(ch)
                    first_n = pending_tags[0][0]
                    if first_n is not None:
                        check_count, is_line_end = decode_check_n(first_n)
                    else:
                        check_count = len(pending_tags)
                        is_line_end = False
                    checkpoints.append(
                        CheckpointConfig(
                            char_idx=char_idx,
                            check_count=check_count,
                            is_line_end=is_line_end,
                            is_rest=is_rest,
                        )
                    )
                    for cp_idx, (_, ts) in enumerate(pending_tags):
                        timetags.append(
                            TimeTag(
                                timestamp_ms=ts,
                                singer_id=singer_id,
                                char_idx=char_idx,
                                checkpoint_idx=cp_idx,
                            )
                        )
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
        _flush_pending(pending_tags, chars, checkpoints, timetags, singer_id)


def _flush_pending(
    pending_tags: List[Tuple[Optional[str], int]],
    chars: List[str],
    checkpoints: List[CheckpointConfig],
    timetags: List[TimeTag],
    singer_id: str,
) -> None:
    """将未消费的 pending_tags 作为无字符 checkpoint 刷出。"""
    # 这种情况不应常见，作为安全回退
    if not pending_tags:
        return
    char_idx = len(chars)
    chars.append("?")
    first_n = pending_tags[0][0]
    if first_n is not None:
        check_count, is_line_end = decode_check_n(first_n)
    else:
        check_count = len(pending_tags)
        is_line_end = False
    checkpoints.append(
        CheckpointConfig(
            char_idx=char_idx, check_count=check_count, is_line_end=is_line_end
        )
    )
    for cp_idx, (_, ts) in enumerate(pending_tags):
        timetags.append(
            TimeTag(
                timestamp_ms=ts,
                singer_id=singer_id,
                char_idx=char_idx,
                checkpoint_idx=cp_idx,
            )
        )
