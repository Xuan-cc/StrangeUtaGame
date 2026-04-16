"""歌词文件解析器

支持 TXT、LRC、KRA 格式的解析。
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple
from pathlib import Path

from strange_uta_game.backend.domain import LyricLine, TimeTag


class ParseError(Exception):
    """解析错误"""

    pass


@dataclass
class ParsedLine:
    """解析后的歌词行数据"""

    text: str
    timetags: List[Tuple[int, int]]  # (char_idx, timestamp_ms) 列表


class LyricParser(ABC):
    """歌词解析器抽象基类"""

    @abstractmethod
    def parse(self, content: str) -> List[ParsedLine]:
        """解析歌词内容

        Args:
            content: 歌词文本内容

        Returns:
            解析后的行列表
        """
        pass

    def parse_file(self, file_path: str) -> List[ParsedLine]:
        """从文件解析歌词

        Args:
            file_path: 文件路径

        Returns:
            解析后的行列表

        Raises:
            ParseError: 文件不存在或无法读取
        """
        path = Path(file_path)
        if not path.exists():
            raise ParseError(f"文件不存在: {file_path}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 尝试 Shift-JIS 编码
            try:
                content = path.read_text(encoding="shift_jis")
            except Exception as e:
                raise ParseError(f"无法解码文件: {e}")
        except Exception as e:
            raise ParseError(f"读取文件失败: {e}")

        return self.parse(content)


class TXTParser(LyricParser):
    """TXT 纯文本歌词解析器

    每行文本成为一个歌词行，没有时间标签。
    """

    def parse(self, content: str) -> List[ParsedLine]:
        """解析 TXT 格式

        支持换行符分割，自动过滤空行。
        """
        lines = []

        for line_text in content.split("\n"):
            line_text = line_text.strip()

            # 跳过空行
            if not line_text:
                continue

            lines.append(ParsedLine(text=line_text, timetags=[]))

        return lines


class LRCParser(LyricParser):
    """LRC 歌词格式解析器

    LRC 格式: [mm:ss.xx]歌词文本
    示例: [00:12.34]歌词内容

    也支持增强 LRC 格式（逐字时间标签）:
    [00:12.34]<00:13.00>字<00:14.00>字
    """

    # 标准 LRC 时间标签正则: [mm:ss.xx] 或 [mm:ss.xxx]
    TIME_TAG_PATTERN = re.compile(r"\[(\d{1,2}):(\d{2})\.(\d{2,3})\]")

    # 增强 LRC 逐字时间标签: <mm:ss.xx>
    WORD_TIME_PATTERN = re.compile(r"<(\d{1,2}):(\d{2})\.(\d{2,3})>")

    def parse(self, content: str) -> List[ParsedLine]:
        """解析 LRC 格式"""
        lines = []

        for line_text in content.split("\n"):
            line_text = line_text.strip()

            if not line_text:
                continue

            # 检查是否是元数据行（如 [ti:标题]）
            if (
                line_text.startswith("[ti:")
                or line_text.startswith("[ar:")
                or line_text.startswith("[al:")
                or line_text.startswith("[by:")
                or line_text.startswith("[offset:")
            ):
                continue

            # 提取所有时间标签
            timetags = []

            # 查找所有时间标签 [mm:ss.xx]
            matches = list(self.TIME_TAG_PATTERN.finditer(line_text))

            if not matches:
                # 没有时间标签，作为纯文本行
                lines.append(ParsedLine(text=line_text, timetags=[]))
                continue

            # 提取最后一个时间标签后的文本作为歌词
            last_match = matches[-1]
            lyric_text = line_text[last_match.end() :].strip()

            # 检查是否是增强 LRC 格式（包含逐字时间）
            if self.WORD_TIME_PATTERN.search(lyric_text):
                # 解析增强 LRC 格式
                word_timetags = self._parse_enhanced_lrc(lyric_text, last_match)
                lines.append(ParsedLine(text=lyric_text, timetags=word_timetags))
            else:
                # 标准 LRC 格式，只取第一个时间标签作为整行时间
                first_match = matches[0]
                timestamp_ms = self._parse_timestamp(first_match)

                lines.append(
                    ParsedLine(
                        text=lyric_text,
                        timetags=[(0, timestamp_ms)],  # 整行时间标签放在第一个字符
                    )
                )

        return lines

    def _parse_timestamp(self, match: re.Match) -> int:
        """从正则匹配解析时间戳（毫秒）"""
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        centis = int(match.group(3))

        # 如果是 3 位（毫秒），直接使用；如果是 2 位（百分秒），乘以 10
        if len(match.group(3)) == 2:
            centis *= 10

        return (minutes * 60 + seconds) * 1000 + centis

    def _parse_enhanced_lrc(
        self, text: str, base_match: re.Match
    ) -> List[Tuple[int, int]]:
        """解析增强 LRC 格式（逐字时间标签）"""
        timetags = []
        char_idx = 0

        # 基础时间
        base_time = self._parse_timestamp(base_match)

        # 移除所有时间标签，提取纯文本
        clean_text = self.WORD_TIME_PATTERN.sub("", text)

        # 查找所有逐字时间标签
        pos = 0
        for match in self.WORD_TIME_PATTERN.finditer(text):
            timestamp_ms = self._parse_timestamp(match)
            timetags.append((char_idx, timestamp_ms))
            char_idx += 1

        return timetags


class KRAParser(LRCParser):
    """KRA 格式解析器

    KRA 格式与 LRC 完全相同，只是文件扩展名不同。
    卡拉 OK 软件通常使用 .kra 扩展名。
    """

    def parse(self, content: str) -> List[ParsedLine]:
        """解析 KRA 格式（同 LRC）"""
        # KRA 格式与 LRC 相同
        return super().parse(content)


class LyricParserFactory:
    """歌词解析器工厂"""

    _parsers = {
        ".txt": TXTParser,
        ".lrc": LRCParser,
        ".kra": KRAParser,
    }

    @classmethod
    def get_parser(cls, file_path: str) -> LyricParser:
        """根据文件扩展名获取解析器

        Args:
            file_path: 文件路径

        Returns:
            对应的解析器实例

        Raises:
            ParseError: 不支持的文件格式
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext not in cls._parsers:
            raise ParseError(f"不支持的文件格式: {ext}")

        return cls._parsers[ext]()

    @classmethod
    def parse_file(cls, file_path: str) -> List[ParsedLine]:
        """自动根据文件扩展名解析文件

        Args:
            file_path: 文件路径

        Returns:
            解析后的行列表
        """
        parser = cls.get_parser(file_path)
        return parser.parse_file(file_path)


def parse_to_lyric_lines(
    parsed_lines: List[ParsedLine], singer_id: str
) -> List[LyricLine]:
    """将解析结果转换为 LyricLine 对象

    Args:
        parsed_lines: 解析后的行列表
        singer_id: 演唱者ID

    Returns:
        LyricLine 对象列表
    """
    lines = []

    for parsed in parsed_lines:
        line = LyricLine(singer_id=singer_id, text=parsed.text)

        # 添加时间标签
        for char_idx, timestamp_ms in parsed.timetags:
            from strange_uta_game.backend.domain import TimeTag, TimeTagType

            tag = TimeTag(
                timestamp_ms=timestamp_ms,
                singer_id=singer_id,
                char_idx=char_idx,
                checkpoint_idx=0,
                tag_type=TimeTagType.CHAR_START,
            )
            line.add_timetag(tag)

        lines.append(line)

    return lines
