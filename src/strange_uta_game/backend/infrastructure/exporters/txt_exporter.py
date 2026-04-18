"""TXT 格式导出器。

支持：
- 纯文本歌词（每行前面加时间戳）
- 支持显示节奏点信息
"""

from typing import List, Optional
from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence


class TXTExporter(BaseExporter):
    """TXT 格式导出器

    导出为纯文本格式，每行包含时间戳和歌词。
    """

    @property
    def name(self) -> str:
        return "TXT"

    @property
    def description(self) -> str:
        return "纯文本时间标签格式"

    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def file_filter(self) -> str:
        return "TXT 文本文件 (*.txt)"

    def __init__(self, include_checkpoints: bool = True, include_rubies: bool = False):
        """
        Args:
            include_checkpoints: 是否包含节奏点信息
            include_rubies: 是否包含注音
        """
        super().__init__()
        self.include_checkpoints = include_checkpoints
        self.include_rubies = include_rubies

    def export(self, project: Project, file_path: str) -> None:
        """导出为 TXT 格式"""
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)

        lines = []

        # 标题
        if project.metadata and project.metadata.title:
            lines.append(f"# {project.metadata.title}")
            if project.metadata.artist:
                lines.append(f"# {project.metadata.artist}")
            lines.append("")

        # 统计信息
        stats = project.get_timing_statistics()
        lines.append(f"# 总行数: {stats.get('total_lines', 0)}")
        lines.append(
            f"# 已完成: {stats.get('completed_lines', 0)} / {stats.get('total_lines', 0)}"
        )
        lines.append(f"# 进度: {stats.get('timing_progress', '0/0')}")
        lines.append("")

        # 导出行
        for i, sentence in enumerate(project.sentences):
            line_text = self._export_sentence(sentence, i + 1)
            if line_text:
                lines.append(line_text)

        # 写入文件
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _export_sentence(self, sentence: Sentence, line_number: int) -> str:
        """导出一行歌词"""
        parts = []

        # 行号
        parts.append(f"[{line_number:03d}]")

        # 时间标签
        if sentence.has_timetags:
            start_ms = sentence.timing_start_ms
            time_str = self._format_timestamp(start_ms, "lrc")
            parts.append(time_str)
        else:
            parts.append("[--:--.--]")

        # 歌词文本
        parts.append(sentence.text)

        # 节奏点信息（可选）
        if self.include_checkpoints and sentence.characters:
            total_checks = sum(c.check_count for c in sentence.characters)
            parts.append(f"  [节奏点: {total_checks}]")

        # 注音（可选）
        if self.include_rubies and sentence.rubies:
            ruby_texts = [r.text for r in sentence.rubies[:3]]
            if ruby_texts:
                parts.append(f"  (注音: {', '.join(ruby_texts)}...)")

        return " ".join(parts)
