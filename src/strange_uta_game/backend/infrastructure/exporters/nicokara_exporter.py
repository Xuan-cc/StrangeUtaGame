"""Nicokara (ニコカラ) LRC 格式导出器。

输出 RhythmicaLyrics 风格的 Nicokara 逐字 LRC 格式：
- 时间戳格式: [MM:SS:CC]（分:秒:厘秒，冒号分隔）
- 每个字符前有独立时间戳
- 行末附加结束时间戳
- @Ruby 注音标签（含字内相对时间和多次出现位置）
- @Offset 全局偏移
- @Title/@Artist/@Album/@TaggingBy/@SilencemSec 元数据标签（可选）
- 演唱者过滤：可按选定的演唱者筛选输出行/字符
- 演唱者标签：在演唱者切换处自动插入【演唱者名】标签
"""

from collections import OrderedDict
from typing import List, Optional, Dict, Any, Set

from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence, Singer


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
    支持演唱者过滤和演唱者标签插入。
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

    def export(
        self,
        project: Project,
        file_path: str,
        singer_ids: Optional[Set[str]] = None,
        insert_singer_tags: bool = False,
        singer_map: Optional[Dict[str, str]] = None,
    ) -> None:
        """导出为 Nicokara 逐字 LRC 格式

        Args:
            project: 项目数据
            file_path: 输出文件路径
            singer_ids: 要输出的演唱者 ID 集合（None 表示全部）
            insert_singer_tags: 是否在演唱者切换处插入【演唱者名】标签
            singer_map: singer_id → 演唱者显示名的映射（insert_singer_tags 时使用）
        """
        self._validate_project(project)

        output_lines: List[str] = []
        prev_end_ms = 0

        for i, sentence in enumerate(project.sentences):
            # 演唱者过滤：检查行内是否有选中的演唱者字符
            if singer_ids is not None:
                if not self._sentence_has_singer(sentence, singer_ids):
                    continue

            # 段落间距 >5 秒时插入空行
            if i > 0 and sentence.has_timetags:
                line_start = sentence.timing_start_ms
                if line_start - prev_end_ms > 5000:
                    output_lines.append("")

            output_lines.append(
                self._export_sentence_with_singer(
                    sentence, singer_ids, insert_singer_tags, singer_map
                )
            )

            if sentence.has_timetags:
                prev_end_ms = sentence.timing_end_ms

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _sentence_has_singer(self, sentence: Sentence, singer_ids: Set[str]) -> bool:
        """检查行内是否有属于指定演唱者的字符"""
        # 行级别演唱者
        if sentence.singer_id in singer_ids:
            return True
        # per-char 级别检查
        for ch in sentence.characters:
            if ch.singer_id in singer_ids:
                return True
        return False

    def _export_sentence_with_singer(
        self,
        sentence: Sentence,
        singer_ids: Optional[Set[str]],
        insert_singer_tags: bool,
        singer_map: Optional[Dict[str, str]],
    ) -> str:
        """导出一行，支持演唱者过滤和标签插入"""
        if not sentence.has_timetags or not sentence.characters:
            return sentence.text

        parts: List[str] = []
        prev_singer_id: Optional[str] = None

        for i, ch in enumerate(sentence.characters):
            char_singer = ch.singer_id

            # 演唱者过滤：跳过不属于选定演唱者的字符
            if singer_ids is not None and char_singer not in singer_ids:
                continue

            # 演唱者标签插入：在演唱者发生变化时插入标签
            if insert_singer_tags and singer_map and char_singer != prev_singer_id:
                singer_name = singer_map.get(char_singer, "")
                if singer_name:
                    parts.append(f"【{singer_name}】")
                prev_singer_id = char_singer

            # 字符起始时间戳（第一个 checkpoint）
            if ch.timestamps:
                parts.append(_format_nicokara_ts(ch.timestamps[0], self._offset_ms))
            parts.append(ch.char)

        # 行末结束时间戳（最后一个字符的 line-end checkpoint）
        if sentence.characters:
            last_char = sentence.characters[-1]
            if last_char.is_line_end and last_char.check_count >= 2:
                # 演唱者过滤：只有该字符属于选定演唱者时才输出
                if singer_ids is None or last_char.singer_id in singer_ids:
                    end_cp_idx = last_char.check_count - 1
                    if end_cp_idx < len(last_char.timestamps):
                        parts.append(
                            _format_nicokara_ts(
                                last_char.timestamps[end_cp_idx], self._offset_ms
                            )
                        )

        return "".join(parts)

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行（向后兼容，不带演唱者过滤）"""
        return self._export_sentence_with_singer(sentence, None, False, None)


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
        singer_ids: Optional[Set[str]] = None,
        insert_singer_tags: bool = False,
        singer_map: Optional[Dict[str, str]] = None,
        tag_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """导出为带 @Ruby 注音标签的 Nicokara LRC 格式

        Args:
            project: 项目数据
            file_path: 输出文件路径
            singer_ids: 要输出的演唱者 ID 集合（None 表示全部）
            insert_singer_tags: 是否在演唱者切换处插入【演唱者名】标签
            singer_map: singer_id → 演唱者显示名的映射
            tag_data: Nicokara 元数据标签，格式与 AppSettings["nicokara_tags"] 相同
        """
        self._validate_project(project)

        output_lines: List[str] = []
        prev_end_ms = 0

        for i, sentence in enumerate(project.sentences):
            # 演唱者过滤
            if singer_ids is not None:
                if not self._sentence_has_singer(sentence, singer_ids):
                    continue

            if i > 0 and sentence.has_timetags:
                line_start = sentence.timing_start_ms
                if line_start - prev_end_ms > 5000:
                    output_lines.append("")

            output_lines.append(
                self._export_sentence_with_singer(
                    sentence, singer_ids, insert_singer_tags, singer_map
                )
            )

            if sentence.has_timetags:
                prev_end_ms = sentence.timing_end_ms

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

        # @Ruby 注音标签（也按演唱者过滤）
        ruby_entries = self._collect_ruby_entries(project, singer_ids)
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

    def _collect_ruby_entries(
        self, project: Project, singer_ids: Optional[Set[str]] = None
    ) -> List[str]:
        """收集所有注音并生成 @Ruby 条目列表

        按 (汉字原文, 读音) 分组，合并多次出现的位置。
        如果指定了 singer_ids，只收集属于指定演唱者的注音。

        通过 Word 分组来收集 ruby：连词字符组成的词语中，
        各字符的 Ruby 文本连接起来作为完整读音。

        Args:
            project: 项目数据
            singer_ids: 要输出的演唱者 ID 集合（None 表示全部）

        Returns:
            格式: ["漢字,読み[ts],pos1,pos2", ...]
        """
        # key: (kanji, reading) → List[(sentence, start_idx, end_idx)]
        ruby_groups: OrderedDict[tuple[str, str], list[tuple[Sentence, int, int]]] = (
            OrderedDict()
        )

        for sentence in project.sentences:
            # 演唱者过滤：跳过不含选定演唱者的行
            if singer_ids is not None:
                if not self._sentence_has_singer(sentence, singer_ids):
                    continue

            # 通过 Word 分组收集 ruby
            char_offset = 0
            for word in sentence.words:
                if word.has_ruby:
                    kanji = word.text
                    reading = word.ruby_text
                    start_idx = char_offset
                    end_idx = char_offset + word.char_count
                    key = (kanji, reading)
                    if key not in ruby_groups:
                        ruby_groups[key] = []
                    ruby_groups[key].append((sentence, start_idx, end_idx))
                char_offset += word.char_count

        entries: List[str] = []
        for (kanji, reading), occurrences in ruby_groups.items():
            # 用第一个有时间数据的出现来计算读音内相对时间戳
            reading_with_ts = reading
            for occ_sentence, start, end in occurrences:
                built = self._build_reading_with_timestamps(
                    occ_sentence, start, end, reading
                )
                if built != reading:
                    reading_with_ts = built
                    break

            # 出现位置列表
            positions: List[str] = []
            for j, (occ_sentence, _start, _end) in enumerate(occurrences):
                if j == 0:
                    positions.append("")  # 首次出现，位置留空
                else:
                    start_ms = occ_sentence.timing_start_ms
                    if start_ms is not None:
                        positions.append(_format_nicokara_ts(start_ms, self._offset_ms))
                    else:
                        positions.append("")

            entry = f"{kanji},{reading_with_ts},{','.join(positions)}"
            entries.append(entry)

        return entries

    def _build_reading_with_timestamps(
        self,
        sentence: Sentence,
        start_idx: int,
        end_idx: int,
        reading: str,
    ) -> str:
        """构建带相对时间戳的读音文本

        格式: た[00:00:15]か[00:00:27]らばこ
        相对时间基于 ruby 组第一个字符的首个 checkpoint。

        Args:
            sentence:  句子
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
            if char_idx >= len(sentence.characters):
                break
            ch = sentence.characters[char_idx]
            effective_count = ch.check_count
            # 行末字符的最后一个 checkpoint 是 line-end，不计入读音
            if ch.is_line_end and effective_count > 1:
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

        # 获取组起始时间（第一个字符的首个 checkpoint）
        first_char = (
            sentence.characters[start_idx]
            if start_idx < len(sentence.characters)
            else None
        )
        if not first_char or not first_char.timestamps:
            return reading
        group_start_ms = first_char.timestamps[0]

        # 拼装读音字符 + 相对时间戳
        result: List[str] = []
        for i, (kana, char_idx, cp_idx) in enumerate(mapping):
            if i == 0:
                # 第一个假名不加时间戳
                result.append(kana)
                continue

            if char_idx >= 0 and cp_idx >= 0:
                ch = sentence.characters[char_idx]
                if cp_idx < len(ch.timestamps):
                    relative_ms = ch.timestamps[cp_idx] - group_start_ms
                    result.append(_format_nicokara_ts(relative_ms))

            result.append(kana)

        return "".join(result)
