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

    支持逐行格式和逐字格式：
    - 逐行: [00:12.34]这是一整行歌词
    - 逐字: [00:12.34]这[00:13.00]是[00:14.00]逐[00:15.00]字
    """

    # 标准 LRC 时间标签正则: [mm:ss.xx] 或 [mm:ss.xxx]
    TIME_TAG_PATTERN = re.compile(r"\[(\d{1,2}):(\d{2})[:.](\d{2,3})\]")

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

            # 查找所有时间标签 [mm:ss.xx]
            matches = list(self.TIME_TAG_PATTERN.finditer(line_text))

            if not matches:
                # 没有时间标签，但有其他内容
                # 检查是否是纯歌词文本（没有时间标签）
                # 如果是纯文本，可以作为无时间标签的歌词行
                if len(line_text) > 0 and not line_text.startswith("["):
                    lines.append(ParsedLine(text=line_text, timetags=[]))
                continue

            # 判断是逐行格式还是逐字格式
            # 逐字格式特征：时间标签后面紧跟字符，然后又是时间标签
            # 例如：[00:00.000]春[00:01.086]日[00:01.629]影

            # 检查时间标签分布
            is_word_by_word = self._is_word_by_word_format(line_text, matches)

            if is_word_by_word:
                # 逐字格式：提取所有字符和时间标签
                lyric_text, timetags = self._parse_word_by_word(line_text, matches)
                if lyric_text:
                    lines.append(ParsedLine(text=lyric_text, timetags=timetags))
            else:
                # 逐行格式：提取最后一个时间标签后的文本作为歌词
                last_match = matches[-1]
                lyric_text = line_text[last_match.end() :].strip()

                if not lyric_text:
                    # 尝试提取时间标签之间的文本: [start]歌词[end]
                    if len(matches) >= 2:
                        first_end = matches[0].end()
                        last_start = matches[-1].start()
                        lyric_text = line_text[first_end:last_start].strip()
                    if not lyric_text:
                        continue

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

    def _is_word_by_word_format(self, line_text: str, matches: List[re.Match]) -> bool:
        """判断是否是逐字格式

        逐字格式特征：
        1. 时间标签之间间隔很短（通常是1-3个字符）
        2. 时间标签数量较多（超过2个）
        3. 第一个时间标签在开头或紧跟很少字符
        """
        if len(matches) < 3:
            return False

        # 检查第一个时间标签位置
        first_match = matches[0]
        if first_match.start() > 0:
            # 如果第一个时间标签前有内容，检查内容长度
            prefix = line_text[: first_match.start()].strip()
            if len(prefix) > 0:
                return False

        # 检查时间标签密度
        # 逐字格式通常每个字符都有一个时间标签
        # 简单判断：如果时间标签数量 > 2 且文本中包含多个时间标签，认为是逐字格式
        text_without_tags = self.TIME_TAG_PATTERN.sub("", line_text)
        # 移除空白后的纯文本长度
        clean_text = text_without_tags.strip()

        # 如果时间标签数量接近或大于文本长度，认为是逐字格式
        return len(matches) >= 3

    def _parse_word_by_word(
        self, line_text: str, matches: List[re.Match]
    ) -> Tuple[str, List[Tuple[int, int]]]:
        """解析逐字格式

        格式：[00:00.000]春[00:01.086]日[00:01.629]影

        Returns:
            (歌词文本, 时间标签列表)
        """
        lyric_chars = []
        timetags = []
        char_idx = 0

        # 遍历所有时间标签
        for i, match in enumerate(matches):
            timestamp_ms = self._parse_timestamp(match)

            # 获取时间标签后的字符（直到下一个时间标签或行尾）
            start_pos = match.end()
            if i + 1 < len(matches):
                end_pos = matches[i + 1].start()
            else:
                end_pos = len(line_text)

            # 提取字符
            chars = line_text[start_pos:end_pos]

            # 为每个字符添加时间标签
            for char in chars:
                if char.strip():  # 只处理非空白字符
                    lyric_chars.append(char)
                    timetags.append((char_idx, timestamp_ms))
                    char_idx += 1
                    # 每个字符后的时间戳递增一个很小的值（10ms）
                    timestamp_ms += 10
                else:
                    # 空白字符也添加到歌词，但不添加时间标签
                    lyric_chars.append(char)
                    char_idx += 1

        lyric_text = "".join(lyric_chars).strip()
        return lyric_text, timetags

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
