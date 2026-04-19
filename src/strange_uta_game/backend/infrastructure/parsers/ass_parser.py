"""ASS 字幕格式解析器

支持 ASS/SSA 字幕文件的解析，提取卡拉OK时间标签（\\k/\\kf/\\ko/\\K/\\kF/\\kO 标签）。
"""

import re
from typing import List, Optional, Tuple

from .lyric_parser import LyricParser, ParsedLine


class ASSParser(LyricParser):
    """ASS 字幕格式解析器

    解析 ASS 字幕文件的 [Events] 区段中的 Dialogue 行，
    提取卡拉OK标签（\\kf, \\k, \\ko 等）的持续时间，
    转换为逐字时间标签。
    """

    # ASS 时间戳格式: H:MM:SS.cc
    ASS_TIME_PATTERN = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")

    # 卡拉OK标签: {\kf32}, {\k50}, {\ko10}, {\K30}, {\kF20}, {\kO15}
    KARAOKE_TAG_PATTERN = re.compile(r"\{\\[kK][oOfF]?(\d+)\}")

    # Dialogue 行格式（10 个逗号分隔字段）
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

            # 检测 [Events] 区段
            if raw_line.lower() == "[events]":
                in_events = True
                continue

            # 检测其他区段开始（退出 Events）
            if raw_line.startswith("[") and raw_line.endswith("]") and in_events:
                if raw_line.lower() != "[events]":
                    in_events = False
                    continue

            if not in_events:
                continue

            # 跳过 Format 行和注释
            if raw_line.startswith("Format:") or raw_line.startswith(";"):
                continue

            # 解析 Dialogue 行
            match = self.DIALOGUE_PATTERN.match(raw_line)
            if not match:
                continue

            start_time_str = match.group(1).strip()
            text_field = match.group(3)

            # 解析起始时间
            start_ms = self._parse_ass_timestamp(start_time_str)
            if start_ms is None:
                continue

            # 解析卡拉OK标签
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

    def _parse_karaoke_text(self, text: str, start_ms: int) -> Optional[ParsedLine]:
        """解析含卡拉OK标签的文本

        {\\kf32}翼{\\kf32}を → 逐字时间标签
        每个 \\k 标签的值为持续时间（厘秒），乘以 10 得到毫秒。
        """
        # 查找所有卡拉OK标签
        karaoke_tags = list(self.KARAOKE_TAG_PATTERN.finditer(text))

        if not karaoke_tags:
            # 没有卡拉OK标签，去除其他 ASS 标签后返回纯文本
            clean_text = re.sub(r"\{[^}]*\}", "", text).strip()
            if clean_text:
                return ParsedLine(text=clean_text, timetags=[(0, start_ms)])
            return None

        # 逐段提取字符和时间戳
        lyric_chars: List[str] = []
        timetags: List[Tuple[int, int]] = []
        current_ms = start_ms
        char_idx = 0

        for i, tag_match in enumerate(karaoke_tags):
            duration_cs = int(tag_match.group(1))  # 厘秒
            duration_ms = duration_cs * 10  # → 毫秒

            # 提取标签后的文本（到下一个标签或行尾）
            text_start = tag_match.end()
            if i + 1 < len(karaoke_tags):
                text_end = karaoke_tags[i + 1].start()
            else:
                text_end = len(text)

            segment_text = text[text_start:text_end]
            # 移除非卡拉OK的 ASS 标签（如 {\r} 等）
            segment_text = re.sub(r"\{[^}]*\}", "", segment_text)

            if segment_text:
                # 记录第一个字符的时间标签
                timetags.append((char_idx, current_ms))

                for ch in segment_text:
                    lyric_chars.append(ch)
                    char_idx += 1

            # 累加持续时间
            current_ms += duration_ms

        lyric_text = "".join(lyric_chars).strip()
        if not lyric_text:
            return None

        # 重新计算索引（去除前导字符偏移）
        full_text = "".join(lyric_chars)
        leading = len(full_text) - len(full_text.lstrip())
        if leading > 0:
            timetags = [(ci - leading, ts) for ci, ts in timetags if ci >= leading]

        return ParsedLine(text=lyric_text, timetags=timetags)
