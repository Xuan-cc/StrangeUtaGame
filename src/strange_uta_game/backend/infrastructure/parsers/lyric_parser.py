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

        支持换行符分割，自动过滤空行和纯标点行。
        """
        lines = []

        for line_text in content.split("\n"):
            line_text = line_text.strip()

            # 跳过空行
            if not line_text:
                continue

            # 跳过纯数字行
            if line_text.isdigit():
                continue

            # 跳过纯标点和特殊符号行
            if re.match(
                r"^[\[\]【】（）(){}<>" "''`~!@#$%^&*+=|\-:;,.?/\\s]+$", line_text
            ):
                continue

            # 跳过 HTML/XML 标签行
            if line_text.startswith("<") and line_text.endswith(">"):
                continue

            # 跳过纯时间戳行
            if re.match(r"^\[\d{1,2}:\d{2}[.:]\d{2,3}\]$", line_text):
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
    # 支持 [mm:ss.xx] 和 [mm:ss.xxx] 格式
    TIME_TAG_PATTERN = re.compile(r"\[(\d{1,2}):(\d{2})[:.](\d{2,3})\]")

    # 增强 LRC 逐字时间标签: <mm:ss.xx>
    WORD_TIME_PATTERN = re.compile(r"<(\d{1,2}):(\d{2})[:.](\d{2,3})>")

    # ID标签（元数据）: [xx:xx]
    ID_TAG_PATTERN = re.compile(r"^\[([a-zA-Z]+):(.*)\]$")

    def parse(self, content: str) -> List[ParsedLine]:
        """解析 LRC 格式"""
        lines = []

        for line_text in content.split("\n"):
            line_text = line_text.strip()

            if not line_text:
                continue

            # 跳过纯数字行
            if line_text.isdigit():
                continue

            # 跳过纯标点和特殊符号行
            if re.match(
                r"^[\[\]【】（）(){}<>" "''`~!@#$%^&*+=|\-:;,.?/\\s]+$", line_text
            ):
                continue

            # 检查是否是纯 ID 标签行（如 [ti:标题] [ar:艺术家]）
            if self.ID_TAG_PATTERN.match(line_text):
                continue

            # 检查是否是纯时间戳行（没有时间标签后接文本）
            if re.match(r"^\[\d{1,2}:\d{2}[.:]\d{2,3}\]+$", line_text):
                continue

            # 提取所有时间标签 [mm:ss.xx]
            matches = list(self.TIME_TAG_PATTERN.finditer(line_text))

            if not matches:
                # 没有时间标签，但有其他内容
                # 检查是否是纯歌词文本（没有时间标签）
                # 如果是纯文本，可以作为无时间标签的歌词行
                if len(line_text) > 0 and not line_text.startswith("["):
                    lines.append(ParsedLine(text=line_text, timetags=[]))
                continue

            # 提取最后一个时间标签后的文本作为歌词
            last_match = matches[-1]
            lyric_text = line_text[last_match.end() :].strip()

            # 移除可能残留的增强LRC时间标签
            lyric_text = self.WORD_TIME_PATTERN.sub("", lyric_text)

            if not lyric_text:
                # 没有时间标签后的文本，跳过
                continue

            # 检查是否是增强 LRC 格式（包含逐字时间）
            if self.WORD_TIME_PATTERN.search(lyric_text):
                # 解析增强 LRC 格式
                word_timetags = self._parse_enhanced_lrc(lyric_text)
                # 清理文本：移除所有逐字时间标签
                clean_text = self.WORD_TIME_PATTERN.sub("", lyric_text)
                lines.append(ParsedLine(text=clean_text, timetags=word_timetags))
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
        centis = match.group(3)

        # 统一转换为毫秒
        if len(centis) == 2:
            # 百分秒（0-99）
            millis = int(centis) * 10
        else:
            # 毫秒（0-999）
            millis = int(centis)

        return (minutes * 60 + seconds) * 1000 + millis

    def _parse_enhanced_lrc(self, text: str) -> List[Tuple[int, int]]:
        """解析增强 LRC 格式（逐字时间标签）"""
        timetags = []
        char_idx = 0

        # 查找所有逐字时间标签
        for match in self.WORD_TIME_PATTERN.finditer(text):
            timestamp_ms = self._parse_timestamp(match)
            timetags.append((char_idx, timestamp_ms))
            char_idx += 1

        return timetags


class KRAParser(LRCParser):
    """KRA 格式解析器

    KRA 格式与 LRC 完全相同，只是文件扩展名不同。
    """

    pass


class LyricParserFactory:
    """歌词解析器工厂

    根据文件扩展名自动选择合适的解析器。
    """

    @staticmethod
    def get_parser(file_path: str) -> LyricParser:
        """根据文件路径获取合适的解析器

        Args:
            file_path: 歌词文件路径

        Returns:
            对应的歌词解析器

        Raises:
            ParseError: 不支持的文件格式
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext == ".txt":
            return TXTParser()
        elif ext == ".lrc":
            return LRCParser()
        elif ext == ".kra":
            return KRAParser()
        else:
            raise ParseError(f"不支持的文件格式: {ext}")

    @staticmethod
    def parse_file(file_path: str) -> List[ParsedLine]:
        """自动选择解析器并解析文件

        Args:
            file_path: 歌词文件路径

        Returns:
            解析后的行列表
        """
        parser = LyricParserFactory.get_parser(file_path)
        return parser.parse_file(file_path)


def parse_to_lyric_lines(
    parsed_lines: List[ParsedLine], singer_id: str
) -> List[LyricLine]:
    """将解析结果转换为 LyricLine 对象

    Args:
        parsed_lines: 解析后的行列表
        singer_id: 演唱者 ID

    Returns:
        LyricLine 对象列表
    """
    from strange_uta_game.backend.domain import Singer

    lines = []

    for i, parsed in enumerate(parsed_lines):
        # 创建歌词行
        line = LyricLine(
            singer_id=singer_id,
            text=parsed.text,
        )

        # 添加时间标签
        for char_idx, timestamp_ms in parsed.timetags:
            tag = TimeTag(
                timestamp_ms=timestamp_ms,
                singer_id=singer_id,
                char_idx=char_idx,
                checkpoint_idx=0,
            )
            line.add_timetag(tag)

        lines.append(line)

    return lines
