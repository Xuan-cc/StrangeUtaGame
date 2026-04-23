"""Nicokara (ニコカラ) LRC 格式导出器。

输出 RhythmicaLyrics 风格的 Nicokara 逐字 LRC 格式：
- 时间戳格式: [MM:SS:CC]（分:秒:厘秒，冒号分隔）
- 每个字符前有独立时间戳
- 行末附加结束时间戳
- @Ruby 注音标签（含字内相对时间；多次出现时每次独立条目+位置范围）
- @Offset 全局偏移
- @Title/@Artist/@Album/@TaggingBy/@SilencemSec 元数据标签（可选）
- 演唱者过滤：可按选定的演唱者筛选输出行/字符
- 演唱者标签：在演唱者切换处自动插入【演唱者名】标签
"""

import re
from collections import OrderedDict
from typing import List, Optional, Dict, Any, Set, Tuple

from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence, Singer

_NICOKARA_TS_RE = re.compile(r'\[\d+:\d+\.\d+\]')


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
        prev_singer_id: Optional[str] = None
        default_singer_id = self._get_default_singer_id(project)

        for i, sentence in enumerate(project.sentences):
            # 演唱者过滤：检查行内是否有选中的演唱者字符
            if singer_ids is not None:
                if not self._sentence_has_singer(
                    sentence, singer_ids, default_singer_id
                ):
                    continue

            # 段落间距 >5 秒时插入空行
            if i > 0 and sentence.has_timetags:
                line_start = sentence.timing_start_ms
                if line_start is not None and line_start - prev_end_ms > 5000:
                    output_lines.append("")

            line_text, prev_singer_id = self._export_sentence_with_singer(
                sentence,
                singer_ids,
                insert_singer_tags,
                singer_map,
                prev_singer_id,
                default_singer_id,
            )
            # 演唱者过滤后可能产生无实际字符的空行，跳过
            stripped = line_text.strip()
            content_only = _NICOKARA_TS_RE.sub('', stripped)
            if singer_ids is not None and not content_only.strip():
                continue
            output_lines.append(line_text)

            if sentence.has_timetags:
                end_ms = sentence.timing_end_ms
                if end_ms is not None:
                    prev_end_ms = end_ms

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    @staticmethod
    def _get_default_singer_id(project: Project) -> Optional[str]:
        """获取项目的默认演唱者 ID"""
        for singer in project.singers:
            if singer.is_default:
                return singer.id
        if project.singers:
            return project.singers[0].id
        return None

    @staticmethod
    def _normalize_singer_id(
        singer_id: Optional[str], default_singer_id: Optional[str]
    ) -> Optional[str]:
        """将空/未知/? 演唱者 ID 归一化为默认演唱者"""
        if not singer_id or singer_id in ("?", "未知"):
            return default_singer_id
        return singer_id

    def _sentence_has_singer(
        self,
        sentence: Sentence,
        singer_ids: Set[str],
        default_singer_id: Optional[str] = None,
    ) -> bool:
        """检查行内是否有属于指定演唱者的字符

        未知/空/? 演唱者视为默认演唱者。
        """
        eff = self._normalize_singer_id(sentence.singer_id, default_singer_id)
        if eff in singer_ids:
            return True
        # per-char 级别检查
        for ch in sentence.characters:
            eff_ch = self._normalize_singer_id(
                ch.singer_id or sentence.singer_id, default_singer_id
            )
            if eff_ch in singer_ids:
                return True
        return False

    def _export_sentence_with_singer(
        self,
        sentence: Sentence,
        singer_ids: Optional[Set[str]],
        insert_singer_tags: bool,
        singer_map: Optional[Dict[str, str]],
        prev_singer_id: Optional[str] = None,
        default_singer_id: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """导出一行，支持演唱者过滤和标签插入

        Returns:
            (行文本, 最后一个有效演唱者 ID)
        """
        if not sentence.has_timetags or not sentence.characters:
            return sentence.text, prev_singer_id

        parts: List[str] = []

        for i, ch in enumerate(sentence.characters):
            # 有效演唱者：优先使用 per-char，回退到行级别
            effective_singer = ch.singer_id or sentence.singer_id
            # 归一化未知演唱者为默认演唱者
            effective_singer = self._normalize_singer_id(
                effective_singer, default_singer_id
            )

            # 演唱者过滤：跳过不属于选定演唱者的字符
            if singer_ids is not None and effective_singer not in singer_ids:
                continue

            # 演唱者标签插入：在演唱者发生变化时插入标签
            if insert_singer_tags and singer_map and effective_singer != prev_singer_id:
                singer_name = singer_map.get(effective_singer, "")
                if singer_name:
                    parts.append(f"【{singer_name}】")
                prev_singer_id = effective_singer

            # 字符起始时间戳（第一个 checkpoint，使用导出时间戳含偏移）
            if ch.export_timestamps:
                parts.append(_format_nicokara_ts(ch.export_timestamps[0]))
            parts.append(ch.char)

            # 非行尾句尾字符的释放时间戳（句中句尾需要输出一前一后两个时间戳）
            if (
                i < len(sentence.characters) - 1
                and ch.is_sentence_end
                and ch.export_sentence_end_ts is not None
            ):
                eff = self._normalize_singer_id(
                    ch.singer_id or sentence.singer_id, default_singer_id
                )
                if singer_ids is None or eff in singer_ids:
                    parts.append(_format_nicokara_ts(ch.export_sentence_end_ts))

        # 行末结束时间戳（最后一个字符的 sentence-end checkpoint）
        if sentence.characters:
            last_char = sentence.characters[-1]
            if (
                last_char.is_sentence_end
                and last_char.export_sentence_end_ts is not None
            ):
                # 演唱者过滤：只有该字符属于选定演唱者时才输出
                eff = self._normalize_singer_id(
                    last_char.singer_id or sentence.singer_id, default_singer_id
                )
                if singer_ids is None or eff in singer_ids:
                    parts.append(_format_nicokara_ts(last_char.export_sentence_end_ts))

        return "".join(parts), prev_singer_id

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行（向后兼容，不带演唱者过滤）"""
        text, _ = self._export_sentence_with_singer(sentence, None, False, None)
        return text


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
        prev_singer_id: Optional[str] = None
        default_singer_id = self._get_default_singer_id(project)

        for i, sentence in enumerate(project.sentences):
            # 演唱者过滤
            if singer_ids is not None:
                if not self._sentence_has_singer(
                    sentence, singer_ids, default_singer_id
                ):
                    continue

            if i > 0 and sentence.has_timetags:
                line_start = sentence.timing_start_ms
                if line_start is not None and line_start - prev_end_ms > 5000:
                    output_lines.append("")

            line_text, prev_singer_id = self._export_sentence_with_singer(
                sentence,
                singer_ids,
                insert_singer_tags,
                singer_map,
                prev_singer_id,
                default_singer_id,
            )
            # 演唱者过滤后可能产生无实际字符的空行，跳过
            stripped = line_text.strip()
            content_only = _NICOKARA_TS_RE.sub('', stripped)
            if singer_ids is not None and not content_only.strip():
                continue
            output_lines.append(line_text)

            if sentence.has_timetags:
                end_ms = sentence.timing_end_ms
                if end_ms is not None:
                    prev_end_ms = end_ms

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

        每次出现的 (汉字, 读音) 组都生成独立的 @Ruby 条目，
        每个条目有各自的字内相对时间戳和出现位置范围。
        对于没有内部时间戳的读音（如单假名）且多次出现时，
        合并为一个全局条目，不附加位置信息。

        Args:
            project: 项目数据
            singer_ids: 要输出的演唱者 ID 集合（None 表示全部）

        Returns:
            格式: ["漢字,読み[ts],pos1,pos2", ...]
        """
        default_singer_id = self._get_default_singer_id(project)
        # key: (kanji, reading) → List[{reading_with_ts, first_char_ts}]
        ruby_groups: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()

        for sentence in project.sentences:
            if singer_ids is not None:
                if not self._sentence_has_singer(
                    sentence, singer_ids, default_singer_id
                ):
                    continue

            char_offset = 0
            for word in sentence.words:
                if word.has_ruby:
                    kanji = word.text
                    reading = word.ruby_text
                    start_idx = char_offset
                    end_idx = char_offset + word.char_count
                    key = (kanji, reading)

                    # 为每次出现独立构建带时间戳的读音
                    reading_with_ts = self._build_reading_with_timestamps(
                        sentence, start_idx, end_idx, reading
                    )

                    # 获取本次出现的首字符时间戳（用于位置标记）
                    first_char_ts: Optional[int] = None
                    if start_idx < len(sentence.characters):
                        ch = sentence.characters[start_idx]
                        if ch.export_timestamps:
                            first_char_ts = ch.export_timestamps[0]

                    if key not in ruby_groups:
                        ruby_groups[key] = []
                    ruby_groups[key].append(
                        {
                            "reading_with_ts": reading_with_ts,
                            "first_char_ts": first_char_ts,
                        }
                    )
                char_offset += word.char_count

        # --- 第二步：生成 @Ruby 条目 ---
        # 按出现顺序收集所有条目，最终按首字符时间戳排序实现跨组交错
        # 每个条目: (sort_key, entry_string)
        #   sort_key = (first_char_ts, insertion_order) 用于稳定排序
        all_entries: List[tuple[tuple[int, int], str]] = []
        group_index = 0

        for (kanji, _reading), occurrences in ruby_groups.items():
            # 检查所有出现的 reading_with_ts 是否完全相同
            distinct_readings = set(occ["reading_with_ts"] for occ in occurrences)

            if len(distinct_readings) == 1:
                # 所有出现读音相同 → 单条全局条目，不附加时间范围
                r_ts = occurrences[0]["reading_with_ts"]
                sort_ts = occurrences[0].get("first_char_ts") or 0
                all_entries.append(((sort_ts, group_index), f"{kanji},{r_ts}"))
                group_index += 1
            else:
                # 不同出现有不同读音 → 需要时间范围
                # 合并连续相同 reading_with_ts 的出现为子组
                sub_groups: List[List[dict]] = []
                for occ in occurrences:
                    if (
                        sub_groups
                        and sub_groups[-1][0]["reading_with_ts"]
                        == occ["reading_with_ts"]
                    ):
                        sub_groups[-1].append(occ)
                    else:
                        sub_groups.append([occ])

                n = len(sub_groups)
                for i, sg in enumerate(sub_groups):
                    r_ts = sg[0]["reading_with_ts"]
                    this_ts = sg[0].get("first_char_ts")
                    sort_ts = this_ts or 0

                    if n == 1:
                        # 合并后只剩一个子组 → 全局条目
                        entry = f"{kanji},{r_ts}"
                    elif i == 0:
                        # 首个子组：省略开始时间，结束=下一子组首次出现时间
                        next_ts = sub_groups[i + 1][0].get("first_char_ts")
                        if next_ts is not None:
                            entry = f"{kanji},{r_ts},,{_format_nicokara_ts(next_ts)}"
                        else:
                            entry = f"{kanji},{r_ts}"
                    elif i == n - 1:
                        # 最后子组：开始=本子组首次出现时间，省略结束时间
                        if this_ts is not None:
                            entry = f"{kanji},{r_ts},{_format_nicokara_ts(this_ts)}"
                        else:
                            entry = f"{kanji},{r_ts}"
                    else:
                        # 中间子组：开始=本子组首次出现，结束=下一子组首次出现
                        next_ts = sub_groups[i + 1][0].get("first_char_ts")
                        p1 = _format_nicokara_ts(this_ts) if this_ts is not None else ""
                        p2 = _format_nicokara_ts(next_ts) if next_ts is not None else ""
                        entry = f"{kanji},{r_ts},{p1},{p2}"

                    all_entries.append(((sort_ts, group_index), entry))
                    group_index += 1

        # 按首字符时间戳排序，同时间戳按出现顺序（insertion_order）
        all_entries.sort(key=lambda x: x[0])
        return [entry for _, entry in all_entries]

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

        for char_idx in range(start_idx, end_idx):
            if char_idx >= len(sentence.characters):
                break
            ch = sentence.characters[char_idx]
            ruby = ch.ruby
            groups = [p.text for p in ruby.parts] if ruby else []

            for cp_idx, group_text in enumerate(groups):
                mapping.append((group_text, char_idx, cp_idx))

        if not mapping and reading:
            mapping.append((reading, -1, -1))

        if not mapping:
            return reading

        # 获取组起始时间（第一个字符的首个 checkpoint，使用导出时间戳）
        first_char = (
            sentence.characters[start_idx]
            if start_idx < len(sentence.characters)
            else None
        )
        if not first_char or not first_char.export_timestamps:
            return reading
        group_start_ms = first_char.export_timestamps[0]

        # 拼装读音字符 + 相对时间戳
        result: List[str] = []
        for i, (group_text, char_idx, cp_idx) in enumerate(mapping):
            if i == 0:
                # 第一个假名不加时间戳
                result.append(group_text)
                continue

            if char_idx >= 0 and cp_idx >= 0:
                ch = sentence.characters[char_idx]
                if cp_idx < len(ch.export_timestamps):
                    relative_ms = ch.export_timestamps[cp_idx] - group_start_ms
                    result.append(_format_nicokara_ts(relative_ms))

            result.append(group_text)

        return "".join(result)
