"""RhythmicaLyrics 内联格式导出器。

导出为 RhythmicaLyrics 风格的内联文本格式，包含时间标签、注音和节奏点信息。
格式说明见 infrastructure/parsers/inline_format.py。
"""

from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    sentences_to_inline_text,
)


class InlineExporter(BaseExporter):
    """RhythmicaLyrics 内联格式导出器

    将项目导出为带有内联时间标签、注音和节奏点的文本格式。
    此格式可被 StrangeUtaGame 重新导入，保留所有打轴数据。
    """

    @property
    def name(self) -> str:
        return "Inline"

    @property
    def description(self) -> str:
        return "RhythmicaLyrics 内联格式（含时间标签和注音）"

    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def file_filter(self) -> str:
        return "RhythmicaLyrics 内联文本 (*.txt)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为内联格式"""
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)

        try:
            content = sentences_to_inline_text(project.sentences)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            raise ExportError(f"导出内联格式失败: {e}")
