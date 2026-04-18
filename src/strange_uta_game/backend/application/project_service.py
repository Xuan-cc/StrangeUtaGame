"""项目管理服务。

负责项目的创建、打开、保存、导入等生命周期管理。
"""

from pathlib import Path
from typing import Optional, Callable, List
from dataclasses import dataclass

from strange_uta_game.backend.domain import Project, Singer, Sentence
from strange_uta_game.backend.infrastructure.persistence.sug_parser import (
    SugProjectParser,
    SugParseError,
)
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    LyricParserFactory,
    ParsedLine,
    parse_to_sentences,
)


class ProjectServiceError(Exception):
    """项目服务错误"""

    pass


@dataclass
class ProjectCallbacks:
    """项目服务回调函数"""

    on_project_loaded: Optional[Callable[[Project], None]] = None
    on_project_saved: Optional[Callable[[Project, str], None]] = None
    on_error: Optional[Callable[[str], None]] = None


class ProjectService:
    """项目管理服务

    管理项目生命周期：创建、打开、保存、导入。
    """

    def __init__(self, callbacks: ProjectCallbacks = None):
        """
        Args:
            callbacks: 回调函数
        """
        self._current_project: Optional[Project] = None
        self._callbacks = callbacks or ProjectCallbacks()

    @property
    def current_project(self) -> Optional[Project]:
        """获取当前项目"""
        return self._current_project

    def create_project(self) -> Project:
        """创建新项目

        Returns:
            新创建的项目
        """
        project = Project()
        self._current_project = project

        if self._callbacks.on_project_loaded:
            self._callbacks.on_project_loaded(project)

        return project

    def open_project(self, file_path: str) -> Optional[Project]:
        """打开已有项目

        从 .sug 文件加载项目。

        Args:
            file_path: 项目文件路径

        Returns:
            加载的项目，如果失败则返回 None
        """
        try:
            project = SugProjectParser.load(file_path)
            self._current_project = project

            if self._callbacks.on_project_loaded:
                self._callbacks.on_project_loaded(project)

            return project

        except SugParseError as e:
            if self._callbacks.on_error:
                self._callbacks.on_error(f"打开项目失败: {e}")
            return None
        except Exception as e:
            if self._callbacks.on_error:
                self._callbacks.on_error(f"未知错误: {e}")
            return None

    def save_project(self, file_path: str) -> bool:
        """保存当前项目

        Args:
            file_path: 保存路径

        Returns:
            是否成功
        """
        if not self._current_project:
            if self._callbacks.on_error:
                self._callbacks.on_error("没有当前项目可保存")
            return False

        try:
            SugProjectParser.save(self._current_project, file_path)

            if self._callbacks.on_project_saved:
                self._callbacks.on_project_saved(self._current_project, file_path)

            return True

        except SugParseError as e:
            if self._callbacks.on_error:
                self._callbacks.on_error(f"保存项目失败: {e}")
            return False
        except Exception as e:
            if self._callbacks.on_error:
                self._callbacks.on_error(f"未知错误: {e}")
            return False

    def import_lyrics(self, file_path: str, singer_id: str = None) -> List[Sentence]:
        """导入歌词文件

        支持 TXT, LRC, KRA 格式。

        Args:
            file_path: 歌词文件路径
            singer_id: 演唱者ID（如果为 None 使用默认演唱者）

        Returns:
            导入的句子列表
        """
        if not self._current_project:
            if self._callbacks.on_error:
                self._callbacks.on_error("没有当前项目")
            return []

        # 获取演唱者ID
        if singer_id is None:
            default_singer = self._current_project.get_default_singer()
            singer_id = default_singer.id

        try:
            # 解析文件
            parsed_lines = LyricParserFactory.parse_file(file_path)

            # 转换为 Sentence
            sentences = parse_to_sentences(parsed_lines, singer_id)

            # 添加到项目
            for sentence in sentences:
                self._current_project.add_sentence(sentence)

            return sentences

        except Exception as e:
            if self._callbacks.on_error:
                self._callbacks.on_error(f"导入歌词失败: {e}")
            return []

    def validate_project(self) -> List[str]:
        """验证当前项目

        Returns:
            错误信息列表（为空表示验证通过）
        """
        if not self._current_project:
            return ["没有当前项目"]

        return self._current_project.validate()

    def get_project_statistics(self) -> dict:
        """获取项目统计信息

        Returns:
            统计信息字典
        """
        if not self._current_project:
            return {}

        return self._current_project.get_timing_statistics()

    def close_project(self) -> None:
        """关闭当前项目"""
        self._current_project = None
