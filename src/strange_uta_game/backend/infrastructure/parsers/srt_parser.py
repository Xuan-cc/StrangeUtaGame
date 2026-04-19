"""SRT 字幕格式解析器

支持标准 SRT 字幕文件的解析，提取行级时间标签。
"""

import re
from typing import List

from .lyric_parser import LyricParser, ParsedLine


class SRTParser(LyricParser):
    """SRT 字幕格式解析器

    解析标准 SRT 格式：
    1
    00:00:00,000 --> 00:00:04,580
    字幕文本

    每个字幕块提取起始时间戳作为行级时间标签。
    """

    # SRT 时间戳行: HH:MM:SS,mmm --> HH:MM:SS,mmm
    TIMESTAMP_PATTERN = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
    )

    def parse(self, content: str) -> List[ParsedLine]:
        """解析 SRT 格式内容"""
        lines: List[ParsedLine] = []

        # 按空行分割为字幕块
        blocks = re.split(r"\n\s*\n", content.strip())

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            block_lines = block.split("\n")

            # 查找时间戳行和文本行
            timestamp_ms = None
            text_lines: List[str] = []

            for line in block_lines:
                line = line.strip()

                # 跳过序号行（纯数字）
                if line.isdigit():
                    continue

                # 检查时间戳行
                ts_match = self.TIMESTAMP_PATTERN.search(line)
                if ts_match:
                    timestamp_ms = self._parse_srt_timestamp(ts_match, start=True)
                    continue

                # 其余为文本行
                if line:
                    # 移除 HTML 标签（SRT 可能含 <b>, <i> 等）
                    clean_line = re.sub(r"<[^>]+>", "", line)
                    if clean_line.strip():
                        text_lines.append(clean_line.strip())

            if not text_lines:
                continue

            # 将多行文本合并
            text = " ".join(text_lines)

            if timestamp_ms is not None:
                lines.append(ParsedLine(text=text, timetags=[(0, timestamp_ms)]))
            else:
                lines.append(ParsedLine(text=text, timetags=[]))

        return lines

    def _parse_srt_timestamp(self, match: re.Match, start: bool = True) -> int:
        """解析 SRT 时间戳 → 毫秒

        Args:
            match: 正则匹配对象
            start: True=起始时间, False=结束时间
        """
        offset = 0 if start else 4

        hours = int(match.group(1 + offset))
        minutes = int(match.group(2 + offset))
        seconds = int(match.group(3 + offset))
        millis = int(match.group(4 + offset))

        return (hours * 3600 + minutes * 60 + seconds) * 1000 + millis
