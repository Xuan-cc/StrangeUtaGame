"""Nicokara (ニコカラ) LRC 格式导出器。

输出 RhythmicaLyrics 风格的 Nicokara 逐字 LRC 格式：
- 时间戳格式: [MM:SS:CC]（分:秒:厘秒，冒号分隔）
- 每个字符前有独立时间戳
- 行末附加结束时间戳
- @Ruby 注音标签（含字内相对时间和多次出现位置）
- @Offset 全局偏移
- @Title/@Artist/@Album/@TaggingBy/@SilencemSec 元数据标签（可选）
"""

from collections import OrderedDict
from typing import List, Optional, Dict, Any

from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, LyricLine


def _format_nicokara_ts(timestamp_ms: int, offset_ms: int = 0) -> str:
    """格式化 Nicokara 时间戳 [MM:SS:CC]

    Args:
        timestamp_ms: 毫秒时间戳
        offset_ms: 偏移量（毫秒）

    Returns:
        格式化后的字符串，如 [00:12:34]
    """
    timestamp_ms = max(0, timestamp_ms + offset_ms)
    minutes = timestamp_ms // 60000
    seconds = (timestamp_ms % 60000) // 1000
    centiseconds = (timestamp_ms % 1000) // 10
    return f"[{minutes:02d}:{seconds:02d}:{centiseconds:02d}]"


class NicokaraExporter(BaseExporter):
    """Nicokara 逐字 LRC 格式导出器

    每个字符前有独立 [MM:SS:CC] 时间戳，行末附加结束时间戳。
    """

    @property
    def name(self) -> str:
        return "Nicokara"

    @property
    def description(self) -> str:
        return "Nicokara 逐字 LRC 格式（ニコカラメーカー用）"

    @property
    def file_extension(self) -> str:
        return ".lrc"

    @property
    def file_filter(self) -> str:
        return "Nicokara LRC 文件 (*.lrc)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 Nicokara 逐字 LRC 格式"""
        self._validate_project(project)

        output_lines: List[str] = []
        prev_end_ms = 0

        for i, line in enumerate(project.lines):
            # 段落间距 >5 秒时插入空行
            if i > 0 and line.timetags:
                line_start = min(t.timestamp_ms for t in line.timetags)
                if line_start - prev_end_ms > 5000:
                    output_lines.append("")

            output_lines.append(self._export_line(line))

            if line.timetags:
                prev_end_ms = max(t.timestamp_ms for t in line.timetags)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _export_line(self, line: LyricLine) -> str:
        """导出一行: [ts]字[ts]字...[ts_end]"""
        if not line.timetags or not line.chars:
            return line.text

        # char_idx → 首个 checkpoint (checkpoint_idx=0) 的时间戳
        char_start_times: dict[int, int] = {}
        for tag in line.timetags:
            if tag.checkpoint_idx == 0 and tag.char_idx not in char_start_times:
                char_start_times[tag.char_idx] = tag.timestamp_ms

        if not char_start_times:
            return line.text

        parts: List[str] = []
        for i, char in enumerate(line.chars):
            if i in char_start_times:
                parts.append(_format_nicokara_ts(char_start_times[i], self._offset_ms))
            parts.append(char)

        # 行末结束时间戳（最后一个字符的 line-end checkpoint）
        last_idx = len(line.chars) - 1
        if last_idx < len(line.checkpoints):
            config = line.checkpoints[last_idx]
            if config.is_line_end and config.check_count >= 2:
                end_tags = [
                    t
                    for t in line.timetags
                    if t.char_idx == last_idx
                    and t.checkpoint_idx == config.check_count - 1
                ]
                if end_tags:
                    parts.append(
                        _format_nicokara_ts(end_tags[0].timestamp_ms, self._offset_ms)
                    )

        return "".join(parts)


class NicokaraWithRubyExporter(NicokaraExporter):
    """带注音的 Nicokara LRC 格式导出器

    在 Nicokara 逐字格式基础上追加：
    - @Offset 全局偏移
    - @RubyN=漢字,読み[相対時間],出現位置1,出現位置2,...
    """

    @property
    def name(self) -> str:
        return "Nicokara (带注音)"

    @property
    def description(self) -> str:
        return "Nicokara 逐字 LRC 格式（含 @Ruby 注音标签）"

    def export(
        self,
        project: Project,
        file_path: str,
        tag_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """导出为带 @Ruby 注音标签的 Nicokara LRC 格式

        Args:
            project: 项目数据
            file_path: 输出文件路径
            tag_data: Nicokara 元数据标签，格式与 AppSettings["nicokara_tags"] 相同
        """
        self._validate_project(project)

        output_lines: List[str] = []
        prev_end_ms = 0

        for i, line in enumerate(project.lines):
            if i > 0 and line.timetags:
                line_start = min(t.timestamp_ms for t in line.timetags)
                if line_start - prev_end_ms > 5000:
                    output_lines.append("")

            output_lines.append(self._export_line(line))

            if line.timetags:
                prev_end_ms = max(t.timestamp_ms for t in line.timetags)

        # 元数据标签（从 AppSettings 或传入的 tag_data 读取）
        tags = tag_data or {}
        if not tags:
            try:
                from strange_uta_game.frontend.settings.settings_interface import (
                    AppSettings,
                )

                tags = AppSettings().get("nicokara_tags") or {}
            except Exception:
                tags = {}

        output_lines.append("")
        if tags.get("title"):
            output_lines.append(f"@Title={tags['title']}")
        if tags.get("artist"):
            output_lines.append(f"@Artist={tags['artist']}")
        if tags.get("album"):
            output_lines.append(f"@Album={tags['album']}")
        if tags.get("tagging_by"):
            output_lines.append(f"@TaggingBy={tags['tagging_by']}")
        silence = tags.get("silence_ms", 0)
        if silence:
            output_lines.append(f"@SilencemSec={silence}")
        for custom in tags.get("custom", []):
            if custom:
                output_lines.append(custom)

        # @Offset
        output_lines.append("@Offset=+0")

        # @Ruby 注音标签
        ruby_entries = self._collect_ruby_entries(project)
        for idx, entry in enumerate(ruby_entries, 1):
            output_lines.append(f"@Ruby{idx}={entry}")

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    # ------------------------------------------------------------------
    # @Ruby 生成
    # ------------------------------------------------------------------

    def _collect_ruby_entries(self, project: Project) -> List[str]:
        """收集所有注音并生成 @Ruby 条目列表

        按 (汉字原文, 读音) 分组，合并多次出现的位置。

        Returns:
            格式: ["漢字,読み[ts],pos1,pos2", ...]
        """
        ruby_groups: OrderedDict[tuple[str, str], list[tuple[LyricLine, int, int]]] = (
            OrderedDict()
        )

        for line in project.lines:
            for ruby in line.rubies:
                kanji = "".join(line.chars[ruby.start_idx : ruby.end_idx])
                key = (kanji, ruby.text)
                if key not in ruby_groups:
                    ruby_groups[key] = []
                ruby_groups[key].append((line, ruby.start_idx, ruby.end_idx))

        entries: List[str] = []
        for (kanji, reading), occurrences in ruby_groups.items():
            # 用第一个有时间数据的出现来计算读音内相对时间戳
            reading_with_ts = reading
            for occ_line, start, end in occurrences:
                built = self._build_reading_with_timestamps(
                    occ_line, start, end, reading
                )
                if built != reading:
                    reading_with_ts = built
                    break

            # 出现位置列表
            positions: List[str] = []
            for j, (occ_line, _start, _end) in enumerate(occurrences):
                if j == 0:
                    positions.append("")  # 首次出现，位置留空
                else:
                    if occ_line.timetags:
                        line_start = min(t.timestamp_ms for t in occ_line.timetags)
                        positions.append(
                            _format_nicokara_ts(line_start, self._offset_ms)
                        )
                    else:
                        positions.append("")

            entry = f"{kanji},{reading_with_ts},{','.join(positions)}"
            entries.append(entry)

        return entries

    def _build_reading_with_timestamps(
        self,
        line: LyricLine,
        start_idx: int,
        end_idx: int,
        reading: str,
    ) -> str:
        """构建带相对时间戳的读音文本

        格式: た[00:00:15]か[00:00:27]らばこ
        相对时间基于 ruby 组第一个字符的首个 checkpoint。

        Args:
            line:      歌词行
            start_idx: ruby 起始字符索引
            end_idx:   ruby 结束字符索引
            reading:   读音文本

        Returns:
            带内嵌相对时间戳的读音字符串
        """
        # 建立 kana → (char_idx, checkpoint_idx) 的映射
        mapping: List[tuple[str, int, int]] = []
        reading_pos = 0

        for char_idx in range(start_idx, end_idx):
            if char_idx >= len(line.checkpoints):
                break
            config = line.checkpoints[char_idx]
            effective_count = config.check_count
            # 行末字符的最后一个 checkpoint 是 line-end，不计入读音
            if config.is_line_end and effective_count > 1:
                effective_count -= 1

            for cp_idx in range(effective_count):
                if reading_pos < len(reading):
                    mapping.append((reading[reading_pos], char_idx, cp_idx))
                    reading_pos += 1

        # 补充剩余读音字符（正常情况不应发生）
        while reading_pos < len(reading):
            mapping.append((reading[reading_pos], -1, -1))
            reading_pos += 1

        if not mapping:
            return reading

        # 获取组起始时间
        first_tags = line.get_timetags_for_char(start_idx)
        first_tags_cp0 = [t for t in first_tags if t.checkpoint_idx == 0]
        if not first_tags_cp0:
            return reading
        group_start_ms = first_tags_cp0[0].timestamp_ms

        # 拼装读音字符 + 相对时间戳
        result: List[str] = []
        for i, (kana, char_idx, cp_idx) in enumerate(mapping):
            if i == 0:
                # 第一个假名不加时间戳
                result.append(kana)
                continue

            if char_idx >= 0 and cp_idx >= 0:
                tags = [
                    t
                    for t in line.timetags
                    if t.char_idx == char_idx and t.checkpoint_idx == cp_idx
                ]
                if tags:
                    relative_ms = tags[0].timestamp_ms - group_start_ms
                    result.append(_format_nicokara_ts(relative_ms))

            result.append(kana)

        return "".join(result)
