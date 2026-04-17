"""导出器基础模块。

定义导出器接口和基础实现。
"""

from abc import ABC, abstractmethod
from typing import Optional
from pathlib import Path

from strange_uta_game.backend.domain import Project


class ExportError(Exception):
    """导出错误"""

    pass


class IExporter(ABC):
    """导出器接口

    所有导出器必须实现此接口。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """导出器名称"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """导出器描述"""
        pass

    @property
    @abstractmethod
    def file_extension(self) -> str:
        """文件扩展名（如 '.lrc'）"""
        pass

    @property
    @abstractmethod
    def file_filter(self) -> str:
        """文件选择器过滤字符串（如 'LRC 文件 (*.lrc)'）"""
        pass

    @abstractmethod
    def export(self, project: Project, file_path: str) -> None:
        """导出项目到文件

        Args:
            project: 项目对象
            file_path: 导出文件路径

        Raises:
            ExportError: 导出失败
        """
        pass


class BaseExporter(IExporter):
    """导出器基类

    提供通用的导出功能。
    """

    def __init__(self):
        self._offset_ms: int = 0

    def _validate_project(self, project: Project) -> None:
        """验证项目是否可导出"""
        if not project:
            raise ExportError("项目为空")

        if not project.lines:
            raise ExportError("项目没有歌词行")

    def _format_timestamp(self, timestamp_ms: int, format_type: str = "lrc") -> str:
        """格式化时间戳

        Args:
            timestamp_ms: 毫秒时间戳
            format_type: 格式类型 ('lrc', 'ass', 'nicokara')

        Returns:
            格式化后的时间字符串
        """
        timestamp_ms = max(0, timestamp_ms + self._offset_ms)
        total_seconds = timestamp_ms / 1000
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)

        if format_type == "lrc":
            # [mm:ss.xx]
            centis = int((timestamp_ms % 1000) / 10)
            return f"[{minutes:02d}:{seconds:02d}.{centis:02d}]"
        elif format_type == "ass":
            # H:MM:SS.cc
            hours = minutes // 60
            minutes = minutes % 60
            centis = int((timestamp_ms % 1000) / 10)
            return f"{hours:d}:{minutes:02d}:{seconds:02d}.{centis:02d}"
        elif format_type == "nicokara":
            # mm:ss.xx
            centis = int((timestamp_ms % 1000) / 10)
            return f"{minutes:02d}:{seconds:02d}.{centis:02d}"
        else:
            # 默认格式
            centis = int((timestamp_ms % 1000) / 10)
            return f"{minutes:02d}:{seconds:02d}.{centis:02d}"

    def _ensure_extension(self, file_path: str) -> str:
        """确保文件路径有正确的扩展名"""
        path = Path(file_path)
        if path.suffix.lower() != self.file_extension.lower():
            return str(path.with_suffix(self.file_extension))
        return file_path
