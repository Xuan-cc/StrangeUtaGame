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
        insert_singer_each_line: bool = False,
        singer_map: Optional[Dict[str, str]] = None,
        software_compensation_ms: int = 0,
    ) -> ExportResult:
        """导出项目

        Args:
            project: 项目对象
            format_name: 格式名称 ('LRC', 'KRA', 'TXT', 等)
            file_path: 导出文件路径
            offset_ms: 已弃用。全局偏移由前端通过 Character.set_offset() 预先写入
                       global_timestamps，本参数保留只为向后兼容，不会被使用。
            singer_ids: 要输出的演唱者 ID 集合（None=全部，仅 Nicokara 格式有效）
            insert_singer_tags: 是否在演唱者切换处插入【演唱者名】标签
            insert_singer_each_line: 是否在每行行首插入演唱者名称标签
            singer_map: singer_id → 演唱者显示名的映射
            software_compensation_ms: 软件导出补偿（毫秒），导出时给时间戳加上此值

        Returns:
            导出结果
        """
        try:
            # 获取导出器
            exporter = get_exporter_by_name(format_name)

            # 注：offset_ms 参数已弃用 —— 全局偏移由前端在导出前通过
            # Character.set_offset() 写入 global_timestamps / global_sentence_end_ts，
            # 各导出器从中直接读取，不再需要 service 层再次叠加。
            _ = offset_ms

            # 应用软件导出补偿
            if software_compensation_ms != 0:
                import copy
                project = copy.deepcopy(project)
                for sentence in project.sentences:
                    for ch in sentence.characters:
                        if ch.global_timestamps:
                            ch.global_timestamps = [
                                max(0, ts + software_compensation_ms)
                                for ts in ch.global_timestamps
                            ]
                        if ch.global_sentence_end_ts is not None:
                            ch.global_sentence_end_ts = max(
                                0, ch.global_sentence_end_ts + software_compensation_ms
                            )

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
                    insert_singer_each_line=insert_singer_each_line,
                    singer_map=singer_map,
                )
            elif isinstance(exporter, NicokaraExporter):
                exporter.export(
                    project,
                    file_path,
                    singer_ids=singer_ids,
                    insert_singer_tags=insert_singer_tags,
                    insert_singer_each_line=insert_singer_each_line,
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

    def validate_ruby_parts(self, project: Project) -> List[dict]:
        """校验项目中所有字符的 rubyPart 数量与 checkCount 是否匹配

        Args:
            project: 项目数据

        Returns:
            不匹配的字符信息列表
        """
        from strange_uta_game.backend.infrastructure.exporters.nicokara_exporter import (
            NicokaraWithRubyExporter,
        )
        exporter = NicokaraWithRubyExporter()
        return exporter.validate_ruby_parts(project)

    def get_ruby_mismatch_detail(
        self, project: Project, max_display: int = 10
    ) -> dict:
        """获取注音分段不匹配详情及按字符/mora 均分预览。

        Args:
            project: 项目数据
            max_display: 预览最多显示的条目数

        Returns:
            {
                "mismatches": [dict, ...],
                "mismatch_lines": [str, ...],
                "char_preview_lines": [str, ...],
                "mora_preview_lines": [str, ...],
                "total": int,
            }
        """
        from strange_uta_game.backend.infrastructure.parsers.inline_format import (
            split_ruby_for_checkpoints,
            distribute_ruby_chars_evenly,
        )

        mismatches = self.validate_ruby_parts(project)

        mismatch_lines = []
        for m in mismatches[:max_display]:
            parts_str = ",".join(m["ruby_parts"])
            mismatch_lines.append(
                f"行 {m['sentence_idx'] + 1} 字符 '{m['char']}': "
                f"check_count={m['check_count']} "
                f"ruby_parts={m['ruby_parts_count']} "
                f"注音='{''.join(m['ruby_parts'])}' "
                f"拆分=[{parts_str}]"
            )
        if len(mismatches) > max_display:
            mismatch_lines.append(f"...还有 {len(mismatches) - max_display} 个不匹配")

        limited = mismatches[:max_display]
        char_lines = []
        mora_lines = []
        for m in limited:
            full_text = "".join(m["ruby_parts"])
            cc = m["check_count"]
            label = f"行 {m['sentence_idx'] + 1} 字符 '{m['char']}': check_count={cc}"

            clean_chars = list(full_text.replace(",", ""))
            if clean_chars and cc > 0:
                char_split = distribute_ruby_chars_evenly(clean_chars, cc)
                mora_split = split_ruby_for_checkpoints(full_text, cc)
            else:
                char_split = [full_text] if full_text else [""]
                mora_split = [full_text] if full_text else [""]

            char_lines.append(f"{label} 拆分=[{','.join(char_split)}]")
            mora_lines.append(f"{label} 拆分=[{','.join(mora_split)}]")

        if len(mismatches) > max_display:
            char_lines.append(f"...还有 {len(mismatches) - max_display} 个")
            mora_lines.append(f"...还有 {len(mismatches) - max_display} 个")

        return {
            "mismatches": mismatches,
            "mismatch_lines": mismatch_lines,
            "char_preview_lines": char_lines,
            "mora_preview_lines": mora_lines,
            "total": len(mismatches),
        }

    def apply_ruby_parts_split(self, project: Project, mode: str) -> None:
        """对所有不匹配字符按指定模式重新拆分注音分段。

        Args:
            project: 项目数据（原地修改）
            mode: "char" 或 "mora"
        """
        from strange_uta_game.backend.infrastructure.parsers.inline_format import (
            split_ruby_for_checkpoints,
            distribute_ruby_chars_evenly,
        )
        from strange_uta_game.backend.domain.models import RubyPart

        mismatches = self.validate_ruby_parts(project)
        for m in mismatches:
            sent = project.sentences[m["sentence_idx"]]
            ch = sent.characters[m["char_idx"]]
            if ch.ruby is None or ch.check_count <= 0:
                continue
            full_text = "".join(p.text for p in ch.ruby.parts)
            cc = ch.check_count
            if mode == "char":
                clean = list(full_text.replace(",", ""))
                parts = (
                    distribute_ruby_chars_evenly(clean, cc)
                    if clean
                    else [""] * max(1, cc)
                )
            else:
                parts = split_ruby_for_checkpoints(full_text, cc)
            ch.ruby.parts = [RubyPart(text=t) for t in parts]
            ch.push_to_ruby()
