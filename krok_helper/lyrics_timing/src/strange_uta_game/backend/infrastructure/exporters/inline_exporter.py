"""RLF 编辑模式格式导出器。

导出为 RhythmicaLyrics 编辑模式的内联文本格式，可直接复制进 RhythmicaLyrics 编辑器。
格式说明见 infrastructure/parsers/inline_format.py。
"""

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    sentences_to_inline_text,
)

from .base import BaseExporter, ExportError


class InlineExporter(BaseExporter):
    """RL 编辑模式格式导出器

    将项目导出为 RhythmicaLyrics 编辑模式的内联文本格式。
    此格式可直接复制进 RhythmicaLyrics 编辑器使用。
    """

    @property
    def name(self) -> str:
        return "RL 编辑模式"

    @property
    def description(self) -> str:
        return "RL 编辑模式格式（可复制进 RhythmicaLyrics）"

    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def file_filter(self) -> str:
        return "RL 编辑模式文本 (*.txt)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 RL 编辑模式格式"""
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)

        try:
            content = sentences_to_inline_text(project.sentences)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            raise ExportError(f"导出 RL 编辑模式格式失败: {e}") from e
