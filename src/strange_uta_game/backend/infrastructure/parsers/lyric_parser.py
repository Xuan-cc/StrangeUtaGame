"""歌词文件解析器

支持 TXT、LRC、KRA、Nicokara 格式的解析。
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from pathlib import Path

from strange_uta_game.backend.domain import Sentence, Character, Ruby


class ParseError(Exception):
    """解析错误"""

    pass


@dataclass
class ParsedLine:
    """解析后的歌词行数据"""

    text: str
    timetags: List[Tuple[int, int]]  # (char_idx, timestamp_ms) 列表


@dataclass
class NicokaraParsedLine:
    """Nicokara 解析后的歌词行数据（含演唱者信息）"""

    text: str
    timetags: List[Tuple[int, int]]  # (char_idx, timestamp_ms) 列表
    # char_idx → singer_key 映射（singer_key 如 "sv1"、"sv9"）
    char_singer_map: Dict[int, str] = field(default_factory=dict)
    line_singer_key: str = ""  # 行级别默认演唱者 key


@dataclass
class NicokaraRubyEntry:
    """Nicokara @Ruby 条目"""

    kanji: str  # 漢字原文
    reading: str  # 読み（可能含相对时间戳）
    positions: List[str] = field(default_factory=list)  # 出现位置时间戳


@dataclass
class NicokaraParseResult:
    """Nicokara 文件完整解析结果"""

    lines: List[NicokaraParsedLine]
    ruby_entries: List[NicokaraRubyEntry]
    # singer_key → singer 显示名 映射（从 @Emoji 解析）
    singer_definitions: Dict[str, str]
    metadata: Dict[str, str] = field(default_factory=dict)


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
                r"^[\[\]【】（）(){}<>\"\u2018\u2019`~!@#$%^&*+=|\\:;,.?/\\s\-]+$",
                line_text,
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
                r"^[\[\]【】（）(){}<>\"\u2018\u2019`~!@#$%^&*+=|\\:;,.?/\\s\-]+$",
                line_text,
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


class NicokaraParser:
    """Nicokara LRC 格式解析器

    解析 RhythmicaLyrics 风格的 Nicokara 逐字 LRC 格式，包括：
    - 【svN】演唱者标签（行首和行内切换）
    - [MM:SS:CC] 时间戳（冒号分隔的厘秒格式）
    - @Ruby 注音元数据
    - @Emoji 演唱者定义

    Nicokara 样例格式:
        [00:02:50]【sv1】この色...       # 头部：singer 声明行
        【sv1】[00:18:74]♪[00:19:74]押...  # 正文：【svN】开头，per-char timestamp
        【sv1】[00:29:78]Fight [00:30:08]【sv9】[00:30:23]fight  # 行内 singer 切换
        @Emoji=【sv1】,透明画像1x1.png,...   # 尾部：singer 定义
        @Ruby1=押,お                       # @Ruby: 汉字→假名映射
    """

    # Nicokara 时间戳: [MM:SS:CC] 冒号分隔
    NICOKARA_TS_PATTERN = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]")
    # 标准 LRC 时间戳: [MM:SS.CC] 或 [MM:SS:CC]
    FLEXIBLE_TS_PATTERN = re.compile(r"\[(\d{1,2}):(\d{2})[:.](\d{2,3})\]")
    # 演唱者标签: 【svN】或【演唱者名】
    SINGER_TAG_PATTERN = re.compile(r"【([^】]+)】")
    # @Ruby 条目
    RUBY_PATTERN = re.compile(r"^@Ruby(\d+)=(.+)$")
    # @Emoji 条目（演唱者定义）
    EMOJI_PATTERN = re.compile(r"^@Emoji=(.+)$")
    # 元数据标签
    META_PATTERN = re.compile(r"^@(\w+)=(.*)$")

    @staticmethod
    def is_nicokara_format(content: str) -> bool:
        """检测内容是否为 Nicokara 格式

        特征：含有 【svN】 标签 或 @Ruby/@Emoji 元数据
        """
        # 检查 【svN】 模式
        if re.search(r"【sv\d+】", content):
            return True
        # 检查 @Ruby 或 @Emoji 元数据
        if re.search(r"^@(Ruby\d+|Emoji)=", content, re.MULTILINE):
            return True
        # 检查 [MM:SS:CC] 冒号分隔的时间戳（Nicokara 特有）
        if re.search(r"\[\d{1,2}:\d{2}:\d{2}\]", content):
            # 排除内联格式 [N|MM:SS:CC]
            if not re.search(r"\[\d+\|\d{2}:\d{2}:\d{2}\]", content):
                return True
        return False

    def parse(self, content: str) -> NicokaraParseResult:
        """解析 Nicokara 格式文件内容

        Returns:
            NicokaraParseResult 含歌词行、ruby 条目和演唱者定义
        """
        raw_lines = content.split("\n")
        body_lines: List[str] = []
        ruby_entries: List[NicokaraRubyEntry] = []
        singer_definitions: Dict[str, str] = {}
        metadata: Dict[str, str] = {}

        for raw_line in raw_lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            # 解析 @Ruby 元数据
            ruby_match = self.RUBY_PATTERN.match(raw_line)
            if ruby_match:
                entry = self._parse_ruby_entry(ruby_match.group(2))
                if entry:
                    ruby_entries.append(entry)
                continue

            # 解析 @Emoji 元数据（演唱者定义）
            emoji_match = self.EMOJI_PATTERN.match(raw_line)
            if emoji_match:
                defs = self._parse_emoji_line(emoji_match.group(1))
                singer_definitions.update(defs)
                continue

            # 解析其他 @ 元数据
            meta_match = self.META_PATTERN.match(raw_line)
            if meta_match:
                key = meta_match.group(1)
                value = meta_match.group(2)
                if key not in ("Ruby", "Emoji"):
                    metadata[key] = value
                continue

            # 正文歌词行
            body_lines.append(raw_line)

        # 解析正文行
        parsed_lines = []
        for line_text in body_lines:
            parsed = self._parse_body_line(line_text)
            if parsed and parsed.text.strip():
                parsed_lines.append(parsed)

        return NicokaraParseResult(
            lines=parsed_lines,
            ruby_entries=ruby_entries,
            singer_definitions=singer_definitions,
            metadata=metadata,
        )

    def parse_file(self, file_path: str) -> NicokaraParseResult:
        """从文件解析 Nicokara 格式"""
        path = Path(file_path)
        if not path.exists():
            raise ParseError(f"文件不存在: {file_path}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="shift_jis")
            except Exception as e:
                raise ParseError(f"无法解码文件: {e}")
        except Exception as e:
            raise ParseError(f"读取文件失败: {e}")

        return self.parse(content)

    def _parse_body_line(self, line_text: str) -> Optional[NicokaraParsedLine]:
        """解析一行正文歌词

        处理 【svN】 演唱者标签和 [MM:SS:CC] 时间戳
        """
        # 查找所有 token（时间戳和演唱者标签）
        tokens: List[Tuple[int, int, str, str]] = []
        # (start, end, type, value)  type: 'ts' or 'singer'

        for m in self.FLEXIBLE_TS_PATTERN.finditer(line_text):
            ts_ms = self._parse_nicokara_timestamp(m)
            tokens.append((m.start(), m.end(), "ts", str(ts_ms)))

        for m in self.SINGER_TAG_PATTERN.finditer(line_text):
            tokens.append((m.start(), m.end(), "singer", m.group(1)))

        # 按位置排序
        tokens.sort(key=lambda t: t[0])

        # 提取纯文本字符和对应的时间戳/演唱者
        lyric_chars: List[str] = []
        timetags: List[Tuple[int, int]] = []
        char_singer_map: Dict[int, str] = {}
        line_singer_key = ""
        current_singer = ""
        char_idx = 0

        # 分段处理文本
        prev_end = 0
        pending_ts: Optional[int] = None

        for start, end, token_type, value in tokens:
            # 处理 token 之前的纯文本字符
            text_between = line_text[prev_end:start]
            for ch in text_between:
                lyric_chars.append(ch)
                if pending_ts is not None:
                    timetags.append((char_idx, pending_ts))
                    pending_ts = None
                if current_singer:
                    char_singer_map[char_idx] = current_singer
                char_idx += 1

            if token_type == "ts":
                pending_ts = int(value)
            elif token_type == "singer":
                current_singer = value
                if not line_singer_key:
                    line_singer_key = value

            prev_end = end

        # 处理最后一段文本
        remaining = line_text[prev_end:]
        for ch in remaining:
            lyric_chars.append(ch)
            if pending_ts is not None:
                timetags.append((char_idx, pending_ts))
                pending_ts = None
            if current_singer:
                char_singer_map[char_idx] = current_singer
            char_idx += 1

        # 如果最后有未消费的时间戳（行末时间戳），添加为最后一个字符的附加 tag
        if pending_ts is not None and lyric_chars:
            timetags.append((len(lyric_chars) - 1, pending_ts))

        text = "".join(lyric_chars).strip()
        if not text:
            return None

        # 重新计算索引（去除前导空白）
        leading_spaces = len("".join(lyric_chars)) - len("".join(lyric_chars).lstrip())
        if leading_spaces > 0:
            timetags = [
                (ci - leading_spaces, ts) for ci, ts in timetags if ci >= leading_spaces
            ]
            new_singer_map: Dict[int, str] = {}
            for ci, sk in char_singer_map.items():
                if ci >= leading_spaces:
                    new_singer_map[ci - leading_spaces] = sk
            char_singer_map = new_singer_map

        return NicokaraParsedLine(
            text=text,
            timetags=timetags,
            char_singer_map=char_singer_map,
            line_singer_key=line_singer_key,
        )

    def _parse_nicokara_timestamp(self, match: re.Match) -> int:
        """从正则匹配解析 Nicokara 时间戳（毫秒）

        支持 [MM:SS:CC]（厘秒）和 [MM:SS.xxx]（毫秒）
        """
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        sub = match.group(3)

        if len(sub) == 2:
            millis = int(sub) * 10  # 厘秒 → 毫秒
        else:
            millis = int(sub)  # 已经是毫秒

        return (minutes * 60 + seconds) * 1000 + millis

    def _parse_ruby_entry(self, entry_text: str) -> Optional[NicokaraRubyEntry]:
        """解析 @Ruby 条目

        格式: 漢字,読み[相対時間],位置1,位置2,...
        例:   押,お
              奪,う[00:00:22]ば
              者,しゃ,,[00:27:01]
        """
        parts = entry_text.split(",")
        if len(parts) < 2:
            return None

        kanji = parts[0]
        reading = parts[1]
        positions = parts[2:] if len(parts) > 2 else []

        return NicokaraRubyEntry(
            kanji=kanji,
            reading=reading,
            positions=positions,
        )

    def _parse_emoji_line(self, emoji_text: str) -> Dict[str, str]:
        """解析 @Emoji 行提取演唱者定义

        格式: @Emoji=【sv1】,透明画像1x1.png,...
        提取 【svN】 标签作为 singer_key
        """
        defs: Dict[str, str] = {}
        parts = emoji_text.split(",")
        for part in parts:
            part = part.strip()
            m = self.SINGER_TAG_PATTERN.match(part)
            if m:
                singer_key = m.group(1)
                # 默认用 singer_key 作为显示名
                defs[singer_key] = singer_key
        return defs


def nicokara_result_to_sentences(
    result: NicokaraParseResult,
    singer_key_to_id: Dict[str, str],
    default_singer_id: str,
) -> List[Sentence]:
    """将 NicokaraParseResult 转换为 Sentence 对象列表

    Args:
        result: Nicokara 解析结果
        singer_key_to_id: singer_key (如 "sv1") → Singer.id 的映射
        default_singer_id: 默认演唱者 ID（无 singer 标签的行/字符使用）

    Returns:
        Sentence 对象列表
    """
    sentences: List[Sentence] = []

    for parsed in result.lines:
        # 确定行级别演唱者
        line_singer_id = default_singer_id
        if parsed.line_singer_key and parsed.line_singer_key in singer_key_to_id:
            line_singer_id = singer_key_to_id[parsed.line_singer_key]

        # 创建句子（from_text 设置默认 checkpoint 配置）
        sentence = Sentence.from_text(
            text=parsed.text,
            singer_id=line_singer_id,
        )

        # 添加时间标签（含 per-char 演唱者）
        for char_idx, timestamp_ms in parsed.timetags:
            if char_idx < 0 or char_idx >= len(sentence.characters):
                continue
            # 获取该字符的演唱者
            char_singer_key = parsed.char_singer_map.get(char_idx, "")
            if char_singer_key and char_singer_key in singer_key_to_id:
                tag_singer_id = singer_key_to_id[char_singer_key]
            else:
                tag_singer_id = line_singer_id

            char = sentence.characters[char_idx]
            char.singer_id = tag_singer_id
            char.add_timestamp(timestamp_ms)

        # 应用 @Ruby 注音（基于文本匹配）
        _apply_ruby_entries(sentence, result.ruby_entries)

        sentences.append(sentence)

    return sentences


# 兼容别名
nicokara_result_to_lyric_lines = nicokara_result_to_sentences


def _apply_ruby_entries(sentence: Sentence, ruby_entries: List[NicokaraRubyEntry]):
    """将 @Ruby 注音条目应用到句子

    通过文本匹配找到漢字在行中的位置并添加 Ruby 注音。
    在新模型中，多字符漢字的 ruby 按字拆分为 per-char Ruby。
    """
    from strange_uta_game.backend.infrastructure.parsers.inline_format import (
        split_ruby_for_checkpoints,
    )

    text = sentence.text
    for entry in ruby_entries:
        # 清除读音中的时间戳（[MM:SS:CC] 格式）
        clean_reading = re.sub(r"\[\d{1,2}:\d{2}[:.]?\d{2,3}\]", "", entry.reading)

        # 在文本中查找漢字位置
        start = 0
        while True:
            pos = text.find(entry.kanji, start)
            if pos == -1:
                break
            end_pos = pos + len(entry.kanji)
            # 检查是否已有 ruby 覆盖该范围
            has_existing = any(
                sentence.characters[ci].ruby is not None
                for ci in range(pos, min(end_pos, len(sentence.characters)))
            )
            if not has_existing:
                # 按字拆分 ruby 到各字符
                block_len = end_pos - pos
                split_parts = split_ruby_for_checkpoints(clean_reading, block_len)
                for ci in range(pos, min(end_pos, len(sentence.characters))):
                    part_idx = ci - pos
                    if part_idx < len(split_parts) and split_parts[part_idx]:
                        sentence.characters[ci].set_ruby(
                            Ruby(text=split_parts[part_idx])
                        )
                break  # 每个 entry 只匹配第一个未标注的出现
            start = end_pos


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
    def detect_nicokara(file_path: str) -> bool:
        """检测文件是否为 Nicokara 格式

        Args:
            file_path: 文件路径

        Returns:
            是否为 Nicokara 格式
        """
        path = Path(file_path)
        if not path.exists():
            return False
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="shift_jis")
            except Exception:
                return False
        except Exception:
            return False
        return NicokaraParser.is_nicokara_format(content)

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


def parse_to_sentences(
    parsed_lines: List[ParsedLine], singer_id: str
) -> List[Sentence]:
    """将解析结果转换为 Sentence 对象

    Args:
        parsed_lines: 解析后的行列表
        singer_id: 演唱者 ID

    Returns:
        Sentence 对象列表
    """
    sentences = []

    for parsed in parsed_lines:
        # 创建句子（from_text 设置默认 checkpoint 配置）
        sentence = Sentence.from_text(
            text=parsed.text,
            singer_id=singer_id,
        )

        # 添加时间标签
        for char_idx, timestamp_ms in parsed.timetags:
            if 0 <= char_idx < len(sentence.characters):
                sentence.characters[char_idx].add_timestamp(timestamp_ms)

        sentences.append(sentence)

    return sentences


# 兼容别名
parse_to_lyric_lines = parse_to_sentences
