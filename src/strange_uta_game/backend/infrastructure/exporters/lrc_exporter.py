"""LRC 格式导出器。

LRC 格式是通用歌词格式：
[mm:ss.xx]歌词文本
"""

from typing import List
from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, LyricLine, TimeTag


class LRCExporter(BaseExporter):
    """LRC 格式导出器

    导出标准 LRC 歌词格式，支持增强 LRC（逐字时间标签）。
    """

    @property
    def name(self) -> str:
        return "LRC"

    @property
    def description(self) -> str:
        return "通用歌词格式，支持逐字时间标签"

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

            # 偏移量（如果有）
            # lines.append(f"[offset:0]")

        lines.append("")  # 空行分隔

        # 导出行
        for line in project.lines:
            line_text = self._export_line(line)
            if line_text:
                lines.append(line_text)

        # 写入文件
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _export_line(self, line: LyricLine) -> str:
        """导出一行歌词

        如果该行有时间标签，使用第一个时间标签作为整行时间。
        如果有多个时间标签，尝试生成增强 LRC 格式。
        """
        if not line.timetags:
            # 没有时间标签，只输出文本
            return line.text

        # 按时间排序的时间标签
        sorted_tags = sorted(line.timetags, key=lambda t: t.timestamp_ms)

        if len(sorted_tags) == 1:
            # 只有一个时间标签，标准 LRC 格式
            timestamp = self._format_timestamp(sorted_tags[0].timestamp_ms)
            return f"{timestamp}{line.text}"

        # 多个时间标签，生成增强 LRC 格式
        # [mm:ss.xx]<mm:ss.xx>字<mm:ss.xx>字...
        result = []

        # 行起始时间
        first_time = sorted_tags[0].timestamp_ms
        result.append(self._format_timestamp(first_time))

        # 逐字时间标签
        for tag in sorted_tags:
            time_str = self._format_timestamp(tag.timestamp_ms)
            # 去掉方括号，使用尖括号
            time_str = time_str.replace("[", "<").replace("]", ">")

            # 获取对应的字符
            char_idx = min(tag.char_idx, len(line.chars) - 1)
            if char_idx < len(line.chars):
                char = line.chars[char_idx]
                result.append(time_str)
                result.append(char)

        return "".join(result)


class KRAExporter(LRCExporter):
    """KRA 格式导出器

    KRA 格式与 LRC 完全相同，只是文件扩展名不同。
    通常用于卡拉 OK 软件。
    """

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
