"""ASS 字幕格式解析器

支持 ASS/SSA 字幕文件的解析。提取卡拉OK时间标签（\\k/\\kf/\\ko/\\K/\\kF/\\kO）、
Aegisub 注音 (`{\\k...}汉字|<かな`)，以及末尾 \\k 的尾部时长（绑为句尾释放点）。

设计原则（与 entities.py 对齐）：
1. 末尾 \\k 的时长不再丢弃，作为 ParsedLine.line_end_ts 输出，
   parse_to_sentences 会把它绑给末字符的 sentence_end_ts。
2. Aegisub 注音 `{\\k...}汉字|<かな` 不再被无差别去掉，而是按段提取，
   写入 ParsedLine.ruby_map。
3. 仅保留卡拉OK相关的 `\\k/\\kf/\\ko/\\K/\\kF/\\kO` 计算；其他 ASS 标签
   （`\\b`, `\\r`, `\\c` 等）剥除时不连带删掉注音文本。
"""

import re
from typing import Dict, List, Optional, Tuple

from .lyric_parser import LyricParser, ParsedLine


class ASSParser(LyricParser):
    """ASS 字幕格式解析器"""

    # ASS 时间戳格式: H:MM:SS.cc
    ASS_TIME_PATTERN = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")

    # 卡拉OK标签: {\kf32}, {\k50}, {\ko10}, {\K30}, {\kF20}, {\kO15}
    KARAOKE_TAG_PATTERN = re.compile(r"\{\\[kK][oOfF]?(\d+)\}")

    # Dialogue 行（10 个字段）
    DIALOGUE_PATTERN = re.compile(
        r"^Dialogue:\s*\d+,"  # Layer
        r"([^,]+),"  # Start time
        r"([^,]+),"  # End time
        r"[^,]*,"  # Style
        r"[^,]*,"  # Name
        r"[^,]*,"  # MarginL
        r"[^,]*,"  # MarginR
        r"[^,]*,"  # MarginV
        r"[^,]*,"  # Effect
        r"(.*)$"  # Text
    )

    def parse(self, content: str) -> List[ParsedLine]:
        """解析 ASS 格式内容"""
        lines: List[ParsedLine] = []
        in_events = False

        for raw_line in content.split("\n"):
            raw_line = raw_line.strip()

            if raw_line.lower() == "[events]":
                in_events = True
                continue

            if raw_line.startswith("[") and raw_line.endswith("]") and in_events:
                if raw_line.lower() != "[events]":
                    in_events = False
                    continue

            if not in_events:
                continue

            if raw_line.startswith("Format:") or raw_line.startswith(";"):
                continue

            match = self.DIALOGUE_PATTERN.match(raw_line)
            if not match:
                continue

            start_time_str = match.group(1).strip()
            text_field = match.group(3)

            start_ms = self._parse_ass_timestamp(start_time_str)
            if start_ms is None:
                continue

            parsed_line = self._parse_karaoke_text(text_field, start_ms)
            if parsed_line and parsed_line.text.strip():
                lines.append(parsed_line)

        return lines

    def _parse_ass_timestamp(self, time_str: str) -> Optional[int]:
        """解析 ASS 时间戳 H:MM:SS.cc → 毫秒"""
        match = self.ASS_TIME_PATTERN.match(time_str.strip())
        if not match:
            return None

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        centis = int(match.group(4))

        return ((hours * 3600 + minutes * 60 + seconds) * 1000) + (centis * 10)

    # ──────────────────────────────────────────────
    # 段内文本处理
    # ──────────────────────────────────────────────

    @staticmethod
    def _strip_non_karaoke_tags(text: str) -> str:
        """剥除非卡拉OK的 ASS 标签（如 {\\r}, {\\b1}, {\\c&HFFFFFF&}）。

        卡拉OK标签 {\\k.../\\kf.../\\ko...} 在外部已经先抽走，这里只用
        去除残留装饰性标签。注音文本（| 后内容）保持原样。
        """
        return re.sub(r"\{[^}]*\}", "", text)

    @staticmethod
    def _classify_segment(segment: str) -> Tuple[str, str, str, str]:
        """识别 \\k 段的语义类型并拆分。

        Aegisub 注音三类段：
        - "continuation"  `#|<かな>` 或 `#|かな`: 续段，无主文，假名归属上一汉字
        - "with_ruby"     `<汉字>|<<かな>` 或 `<汉字>|かな`: 首段，主文+首 part 假名
        - "plain"         无 `|`: 普通段，仅主文（也可能空）

        Returns:
            (kind, main_text, ruby_text, raw_segment)
            kind ∈ {"continuation", "with_ruby", "plain"}
        """
        # 续段标记：`#|` 开头（# 之前可能有别的字符吗？Aegisub 规范不会，
        # 但用户工具可能在 #| 前放其他字符。保守起见仅匹配净 # 起始）
        stripped = segment.lstrip()
        if stripped.startswith("#|"):
            rest = stripped[2:]
            # `#|<かな` 也可能（极少见），剥掉 `<`
            ruby_text = rest.lstrip("<")
            return "continuation", "", ruby_text, segment

        if "|" in segment:
            main, _, ruby = segment.partition("|")
            ruby = ruby.lstrip("<")
            return "with_ruby", main, ruby, segment

        return "plain", segment, "", segment

    def _parse_karaoke_text(
        self, text: str, start_ms: int
    ) -> Optional[ParsedLine]:
        """解析含卡拉OK标签的文本。

        Aegisub karaoke-template 注音真实语法（三类段）：
        - `{\\k<d>}<字>`            普通段
        - `{\\k<d>}<汉字>|<<かな>`   带 ruby 首段（新建字符 + ruby.parts[0]）
        - `{\\k<d>}#|<かな>`         续段（不新建字符；ts 与 part 追加给前一字）

        策略：
        - 把整段按 `\\k...` 切片，每片用 `_classify_segment` 区分种类。
        - "with_ruby"/"plain"：产生新字符 + 一条 timetag。
        - "continuation"：把 (上一字的 char_idx → ts) 写入 extra_checkpoints_map，
          把假名追加到该字 ruby_map 的 parts_list。
        - 累加 duration → 下一片的起始时间。
        - 末尾片的 duration 不丢弃，作为 `line_end_ts`。
        """
        karaoke_tags = list(self.KARAOKE_TAG_PATTERN.finditer(text))

        if not karaoke_tags:
            clean_text = self._strip_non_karaoke_tags(text)
            # 兼容无 \k 但带 `|<` 注音
            if "|" in clean_text:
                main_text, _, _ = clean_text.partition("|")
                main_text = main_text.strip()
            else:
                main_text = clean_text.strip()
            if main_text:
                return ParsedLine(text=main_text, timetags=[(0, start_ms)])
            return None

        lyric_chars: List[str] = []
        timetags: List[Tuple[int, int]] = []
        # 新签名：char_idx → (parts_list, span_length)
        ruby_map: Dict[int, Tuple[List[str], int]] = {}
        # ASS `#|` 续段给同字追加的额外 checkpoint
        extra_checkpoints_map: Dict[int, List[int]] = {}
        current_ms = start_ms
        char_idx = 0
        last_duration_ms = 0
        # 续段归属：最近一次产生新字符的起始 char_idx
        last_char_idx_for_ruby: Optional[int] = None

        for i, tag_match in enumerate(karaoke_tags):
            duration_cs = int(tag_match.group(1))
            duration_ms = duration_cs * 10
            last_duration_ms = duration_ms

            text_start = tag_match.end()
            text_end = (
                karaoke_tags[i + 1].start()
                if i + 1 < len(karaoke_tags)
                else len(text)
            )

            raw_segment = text[text_start:text_end]
            # 先剥掉装饰性 ASS 标签
            cleaned = self._strip_non_karaoke_tags(raw_segment)

            kind, main_text, ruby_text, _ = self._classify_segment(cleaned)

            if kind == "continuation":
                # 续段：本段 ts 归属上一字符，假名追加到其 ruby.parts
                if last_char_idx_for_ruby is not None and ruby_text:
                    extra_checkpoints_map.setdefault(
                        last_char_idx_for_ruby, []
                    ).append(current_ms)
                    # 追加 part 到既有 ruby_map 项
                    if last_char_idx_for_ruby in ruby_map:
                        parts_list, span = ruby_map[last_char_idx_for_ruby]
                        parts_list.append(ruby_text)
                        ruby_map[last_char_idx_for_ruby] = (parts_list, span)
                    else:
                        # 极少见：上一字没有首 part 注音却有续段。建一条新项。
                        ruby_map[last_char_idx_for_ruby] = ([ruby_text], 1)
                # 续段不新建字符，不追加 lyric_chars
                current_ms += duration_ms
                continue

            # with_ruby / plain：产生新字符（可能多字）
            if main_text:
                first_char_idx_in_segment = char_idx
                timetags.append((first_char_idx_in_segment, current_ms))
                if ruby_text:
                    # span = main_text 字符数。
                    # - 单字 main_text + ruby_text：span=1, parts=[ruby_text]（之后续段会 append part）
                    # - 多字 main_text + ruby_text：span=N, parts=[ruby_text]（旧整段共享 reading 语法）
                    ruby_map[first_char_idx_in_segment] = (
                        [ruby_text],
                        len(main_text),
                    )
                last_char_idx_for_ruby = first_char_idx_in_segment

                for ch in main_text:
                    lyric_chars.append(ch)
                    char_idx += 1

            current_ms += duration_ms

        lyric_text = "".join(lyric_chars).strip()
        if not lyric_text:
            return None

        # 去除前导空白带来的 char_idx 偏移
        full_text = "".join(lyric_chars)
        leading = len(full_text) - len(full_text.lstrip())
        if leading > 0:
            timetags = [
                (ci - leading, ts) for ci, ts in timetags if ci >= leading
            ]
            ruby_map = {
                ci - leading: rb for ci, rb in ruby_map.items() if ci >= leading
            }
            extra_checkpoints_map = {
                ci - leading: ts_list
                for ci, ts_list in extra_checkpoints_map.items()
                if ci >= leading
            }

        # 句尾释放：最后一片 \k 的 duration_ms 不丢弃
        line_end_ts: Optional[int] = None
        if timetags or extra_checkpoints_map:
            line_end_ts = current_ms
            # 防御：若解析过程异常导致 line_end_ts 反而小于等于末 ts，
            # 退化为「末 ts + 末片 duration」
            all_ts = [ts for _, ts in timetags]
            for extras in extra_checkpoints_map.values():
                all_ts.extend(extras)
            if all_ts:
                last_ts = max(all_ts)
                if line_end_ts <= last_ts:
                    line_end_ts = last_ts + max(0, last_duration_ms)

        return ParsedLine(
            text=lyric_text,
            timetags=timetags,
            line_end_ts=line_end_ts,
            ruby_map=ruby_map,
            extra_checkpoints_map=extra_checkpoints_map,
        )
