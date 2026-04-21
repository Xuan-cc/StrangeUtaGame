"""导出服务。

提供统一的项目导出功能。
"""

from typing import Optional, Callable, List, Set, Dict
from dataclasses import dataclass
from pathlib import Path

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.exporters import (
    IExporter,
    ExportError,
    get_exporter_by_name,
    get_all_exporters,
)


@dataclass
class ExportResult:
    """导出结果"""

    success: bool
    file_path: Optional[str] = None
    format_name: Optional[str] = None
    error_message: Optional[str] = None


class ExportService:
    """导出服务

    管理项目导出到各种格式。
    """

    def __init__(self, progress_callback: Optional[Callable[[int, str], None]] = None):
        """
        Args:
            progress_callback: 进度回调函数 (progress_pct, message)
        """
        self._progress_callback = progress_callback

    def get_available_formats(self) -> List[dict]:
        """获取可用的导出格式列表

        Returns:
            格式信息列表，每项包含：
            - name: 格式名称
            - description: 描述
            - extension: 扩展名
            - filter: 文件选择器过滤字符串
        """
        formats = []

        for exporter in get_all_exporters():
            formats.append(
                {
                    "name": exporter.name,
                    "description": exporter.description,
                    "extension": exporter.file_extension,
                    "filter": exporter.file_filter,
                }
            )

        return formats

    def export(
        self,
        project: Project,
        format_name: str,
        file_path: str,
        offset_ms: int = 0,
        singer_ids: Optional[Set[str]] = None,
        insert_singer_tags: bool = False,
        singer_map: Optional[Dict[str, str]] = None,
    ) -> ExportResult:
        """导出项目

        Args:
            project: 项目对象
            format_name: 格式名称 ('LRC', 'KRA', 'TXT', 等)
            file_path: 导出文件路径
            offset_ms: 导出时间偏移（毫秒）
            singer_ids: 要输出的演唱者 ID 集合（None=全部，仅 Nicokara 格式有效）
            insert_singer_tags: 是否在演唱者切换处插入【演唱者名】标签
            singer_map: singer_id → 演唱者显示名的映射

        Returns:
            导出结果
        """
        try:
            # 获取导出器
            exporter = get_exporter_by_name(format_name)

            # 设置导出偏移：偏移已预计算在 Character.export_timestamps 中，
            # 导出器不再需要额外偏移
            if hasattr(exporter, "_offset_ms"):
                exporter._offset_ms = 0

            # 报告进度
            if self._progress_callback:
                self._progress_callback(0, f"开始导出为 {exporter.name} 格式...")

            # 执行导出（Nicokara 格式传递演唱者参数）
            from strange_uta_game.backend.infrastructure.exporters.nicokara_exporter import (
                NicokaraExporter,
                NicokaraWithRubyExporter,
            )

            if isinstance(exporter, NicokaraWithRubyExporter):
                exporter.export(
                    project,
                    file_path,
                    singer_ids=singer_ids,
                    insert_singer_tags=insert_singer_tags,
                    singer_map=singer_map,
                )
            elif isinstance(exporter, NicokaraExporter):
                exporter.export(
                    project,
                    file_path,
                    singer_ids=singer_ids,
                    insert_singer_tags=insert_singer_tags,
                    singer_map=singer_map,
                )
            else:
                exporter.export(project, file_path)

            # 报告完成
            if self._progress_callback:
                self._progress_callback(100, "导出完成")

            return ExportResult(
                success=True,
                file_path=file_path,
                format_name=exporter.name,
            )

        except ExportError as e:
            if self._progress_callback:
                self._progress_callback(0, f"导出失败: {e}")

            return ExportResult(
                success=False,
                error_message=str(e),
            )

        except Exception as e:
            if self._progress_callback:
                self._progress_callback(0, f"导出失败: {e}")

            return ExportResult(
                success=False,
                error_message=f"未知错误: {e}",
            )

    def validate_before_export(self, project: Project) -> List[str]:
        """验证项目是否可以导出

        Args:
            project: 项目对象

        Returns:
            错误信息列表（为空表示可以导出）
        """
        errors = []

        if not project:
            errors.append("项目为空")
            return errors

        if not project.sentences:
            errors.append("项目没有歌词行")

        # 检查是否有时间标签
        sentences_with_tags = sum(1 for s in project.sentences if s.has_timetags)
        if sentences_with_tags == 0:
            errors.append("没有时间标签，导出的歌词将没有时间信息")

        # 统计信息
        stats = project.get_timing_statistics()
        total_lines = stats.get("total_lines", 0)
        completed_lines = stats.get("completed_lines", 0)

        if completed_lines < total_lines:
            errors.append(f"只有 {completed_lines}/{total_lines} 行完成打轴")

        return errors
