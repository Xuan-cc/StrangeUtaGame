"""春日向注音（单注音）格式导出器。

导出为单注音内联文本格式，仅包含假名读音层。
"""

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.parsers.kasugamuki_format import (
    sentences_to_kasugamuki,
)
from .base import BaseExporter, ExportError


class KasugamukiExporter(BaseExporter):
    """春日向注音导出器

    将项目导出为春日向单注音格式文本。
    格式: [ts]char...{kanji|[ts]kana[ts]kana...}...
    """

    @property
    def name(self) -> str:
        return "春日向注音"

    @property
    def description(self) -> str:
        return "春日向单注音格式（假名读音）"

    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def file_filter(self) -> str:
        return "春日向注音文本 (*.txt)"

    def export(self, project: Project, file_path: str) -> None:
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)
        try:
            content = sentences_to_kasugamuki(project.sentences)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            raise ExportError(f"导出春日向注音格式失败: {e}") from e
