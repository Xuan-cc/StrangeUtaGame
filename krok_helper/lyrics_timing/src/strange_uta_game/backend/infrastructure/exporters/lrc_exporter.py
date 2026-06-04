"""LRC 格式导出器。

LRC 格式是通用歌词格式：
[mm:ss.xxx]歌词文本

支持三种子格式：
- LRC (逐行): [mm:ss.xxx]一整行歌词
- LRC (逐字): [mm:ss.xxx]字[mm:ss.xxx]字...
- LRC (增强型): [mm:ss.xxx]<mm:ss.xxx>字<mm:ss.xxx>字...

设计原则（参考 entities.py 重构后契约）：
1. 时间永远从 char.global_timestamps / char.global_sentence_end_ts 取，
   领域层已经把偏移量算好了，导出器不再二次叠加 offset。
2. 每个字符在输出文本里只出现一次。多 checkpoint（一字多拍）的字符
   只取第一个时间戳 global_timestamps[0]，行尾拖音用句尾字符的
   global_sentence_end_ts 单独追加一个标签，不再生成重复字符。
3. 没有时间戳的字符（标点、未打轴字符）原样附在前一个标签之后，
   不为它单独生成时间标签。
"""

from typing import List, Optional
from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence


class LRCExporter(BaseExporter):
    """LRC 增强型格式导出器

    导出增强型 LRC 歌词格式（逐字时间标签使用尖括号）。
    """

    _precision_ms: bool = True

    @property
    def name(self) -> str:
        return "LRC (增强型)"

    @property
    def description(self) -> str:
        return "增强型 LRC 格式，逐字时间标签使用尖括号"

    @property
    def file_extension(self) -> str:
        return ".lrc"

    @property
    def file_filter(self) -> str:
        return "LRC 歌词文件 (*.lrc)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 LRC 格式"""
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)

        lines = []

        # 元数据标签
        if project.metadata:
            if project.metadata.title:
                lines.append(f"[ti:{project.metadata.title}]")
            if project.metadata.artist:
                lines.append(f"[ar:{project.metadata.artist}]")
            if project.metadata.album:
                lines.append(f"[al:{project.metadata.album}]")

            # 工具信息
            lines.append(f"[by:StrangeUtaGame]")

        lines.append("")  # 空行分隔

        # 导出行（空行也输出以保留用户排版）
        for sentence in project.sentences:
            line_text = self._export_sentence(sentence)
            lines.append(line_text)

        # 写入文件
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    # ── 辅助：定位句尾拖音时间戳 ──
    def _find_sentence_end_ts(self, sentence: Sentence) -> Optional[int]:
        """取本行最后一个标记为 is_sentence_end 的字符的 global_sentence_end_ts。

        若没有任何 sentence_end 标记，回退到 None（由调用方决定是否兜底）。
        """
        for ch in reversed(sentence.characters):
            if ch.is_sentence_end and ch.global_sentence_end_ts is not None:
                return ch.global_sentence_end_ts
        return None

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行歌词（增强型格式）

        每个字符只输出一次：取 global_timestamps[0] 作为该字时间。
        多 checkpoint 字符的额外 timestamps 不再生成重复字符（修复字符重影 bug）。
        行末若有 sentence_end_ts，追加一个不带字符的尾时间标签作为拖音终止点。
        """
        if not sentence.has_timetags:
            return sentence.text

        # 行起始时间：行内最早的全局时间戳
        line_start_ms = sentence.global_timing_start_ms
        if line_start_ms is None:
            return sentence.text

        result: List[str] = [self._format_timestamp(line_start_ms, precision_ms=self._precision_ms)]

        # 逐字符输出：每字最多一个时间标签 + 该字字符
        any_char_with_ts = False
        for ch in sentence.characters:
            if ch.global_timestamps:
                # 取第一个 checkpoint 作为该字时间
                ts = ch.global_timestamps[0]
                # 行首字符已经被行级 [mm:ss.xxx] 覆盖，仍然再补一个 <mm:ss.xxx>
                # 以便逐字播放器能精确高亮（增强型 LRC 标准）。
                time_str = self._format_timestamp(ts, precision_ms=self._precision_ms).replace("[", "<").replace("]", ">")
                result.append(time_str)
                result.append(ch.char)
                any_char_with_ts = True
            else:
                # 没有时间戳的字符（如标点、未打轴字符）：原样附着，
                # 不生成时间标签，避免破坏歌词文本。
                result.append(ch.char)

        # 行尾拖音：追加 sentence_end 时间戳（不带字符）
        end_ts = self._find_sentence_end_ts(sentence)
        if end_ts is not None and any_char_with_ts:
            end_str = self._format_timestamp(end_ts, precision_ms=self._precision_ms).replace("[", "<").replace("]", ">")
            result.append(end_str)

        return "".join(result)


class LRCLineExporter(LRCExporter):
    """LRC 逐行格式导出器

    每行只有一个行级时间标签，不含逐字标签。
    格式: [mm:ss.xxx]歌词文本
    """

    @property
    def name(self) -> str:
        return "LRC (逐行)"

    @property
    def description(self) -> str:
        return "LRC 逐行格式，每行一个时间标签"

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行歌词（逐行格式，只取行起始时间）"""
        if not sentence.has_timetags:
            return sentence.text

        first_ts = sentence.global_timing_start_ms
        if first_ts is None:
            return sentence.text

        timestamp = self._format_timestamp(first_ts, precision_ms=self._precision_ms)
        return f"{timestamp}{sentence.text}"


class LRCWordExporter(LRCExporter):
    """LRC 逐字格式导出器

    每个字符有独立的方括号时间标签。
    格式: [mm:ss.xxx]字[mm:ss.xxx]字[mm:ss.xxx]字...
    """

    @property
    def name(self) -> str:
        return "LRC (逐字)"

    @property
    def description(self) -> str:
        return "LRC 逐字格式，每个字符一个时间标签"

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行歌词（逐字格式，方括号时间标签）

        每字一个时间标签 + 字符；无时间戳的字符紧贴前字符不插标签；
        行末若有 sentence_end_ts 追加尾标签作为拖音终止点。
        """
        if not sentence.has_timetags:
            return sentence.text

        result: List[str] = []
        any_char_with_ts = False
        for ch in sentence.characters:
            if ch.global_timestamps:
                ts = ch.global_timestamps[0]
                result.append(self._format_timestamp(ts, precision_ms=self._precision_ms))
                result.append(ch.char)
                any_char_with_ts = True
            else:
                result.append(ch.char)

        if not any_char_with_ts:
            return sentence.text

        end_ts = self._find_sentence_end_ts(sentence)
        if end_ts is not None:
            result.append(self._format_timestamp(end_ts, precision_ms=self._precision_ms))

        return "".join(result)


class KRAExporter(LRCExporter):
    """KRA 格式导出器

    KRA 格式与 LRC 完全相同，只是文件扩展名不同。
    通常用于卡拉 OK 软件。
    """

    _precision_ms: bool = False

    @property
    def name(self) -> str:
        return "KRA"

    @property
    def description(self) -> str:
        return "卡拉 OK 专用格式（同 LRC）"

    @property
    def file_extension(self) -> str:
        return ".kra"

    @property
    def file_filter(self) -> str:
        return "KRA 卡拉 OK 文件 (*.kra)"
