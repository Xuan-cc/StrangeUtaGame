"""春日向注音（带罗马音）双注音格式导出器。

导出为双注音内联文本格式，同时包含假名读音和罗马音层。
"""

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.parsers.kasugamuki_format import (
    sentences_to_kasugamuki_romaji,
)
from .base import BaseExporter, ExportError


class KasugamukiRomajiExporter(BaseExporter):
    """春日向双注音导出器（带罗马音）

    将项目导出为春日向双注音格式文本。
    格式: {kanji|[ts]kana...>[ts]romaji...}
    纯假名: {kana|>[ts]romaji...}
    """

    @property
    def name(self) -> str:
        return "春日向注音（带罗马音）"

    @property
    def description(self) -> str:
        return "春日向双注音格式（假名 + 罗马音）"

    @property
    def file_extension(self) -> str:
        return ".krl"

    @property
    def file_filter(self) -> str:
        return "春日向注音文件 (*.krl)"

    def export(self, project: Project, file_path: str) -> None:
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)
        try:
            content = sentences_to_kasugamuki_romaji(project.sentences)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            raise ExportError(f"导出春日向注音（带罗马音）格式失败: {e}") from e
