"""txt2ass 与 ASS 字幕格式导出器。

包含两类导出器：
1. Txt2AssExporter: 给 txt2ass 工具用的简单 [mm:ss.xx]text 文本格式。
2. ASSDirectExporter: 直接生成 Aegisub 兼容的 .ass 卡拉OK字幕，
   支持 \\k 时长标签和 Aegisub 风格的注音 ({字|<かな})。

设计原则（参考 entities.py 重构后契约）：
1. 时间永远从 char.global_timestamps / char.global_sentence_end_ts 取，
   领域层已经把偏移量算好，导出器不再二次叠加。
2. 每个字符在 Dialogue 文本里只出现一次。多 checkpoint 字符的额外
   timestamps 不再生成重复字符（修复字符重影 bug）。
3. 行 End Time 不再依赖「下一行 Start」（会让字幕跨过整段间奏），
   而是用本行最后字符的 global_sentence_end_ts，没有则退化为
   global_timing_end_ms + post-roll。
4. ASS 卡拉OK标签 \\k 的单位是厘秒(10ms)。每字时长 =
   下一个时间戳(或行末 sentence_end_ts) - 当前字时间戳，转厘秒。
"""

from typing import Dict, List, Optional, Tuple
from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence


class Txt2AssExporter(BaseExporter):
    """txt2ass 格式导出器

    导出 txt2ass 格式，用于配合外部 txt2ass 工具生成 ASS。
    格式简单：每行 [mm:ss.xx]Lyrics
    """

    @property
    def name(self) -> str:
        return "txt2ass"

    @property
    def description(self) -> str:
        return "用于生成 ASS 字幕的格式"

    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def file_filter(self) -> str:
        return "txt2ass 文件 (*.txt)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 txt2ass 格式"""
        self._validate_project(project)

        lines: List[str] = []

        # 标题信息（注释）
        if project.metadata:
            if project.metadata.title:
                lines.append(f"# Title: {project.metadata.title}")
            if project.metadata.artist:
                lines.append(f"# Artist: {project.metadata.artist}")

        lines.append("# Format: [mm:ss.xx]Lyrics")
        lines.append("")

        for sentence in project.sentences:
            line_text = self._export_sentence(sentence)
            if line_text:
                lines.append(line_text)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行歌词"""
        if not sentence.has_timetags:
            return f"[00:00.00]{sentence.text}"

        start_ms = sentence.global_timing_start_ms
        if start_ms is None:
            return f"[00:00.00]{sentence.text}"

        time_str = self._format_timestamp(start_ms, "lrc")
        return f"{time_str}{sentence.text}"


# ──────────────────────────────────────────────
# ASSDirectExporter
# ──────────────────────────────────────────────

# 行前后留白（毫秒），让 Dialogue Start/End 之外有一点缓冲，
# 字幕进入/退出更自然。
# 注意：留白只作用于 Dialogue Start/End 时间，不再额外生成 \k 段时长——
# 行首 / 行尾的 \k 占位符固定输出 {\k0}，让用户自行用模板/特效填充。
_PRE_ROLL_MS = 200
_POST_ROLL_MS = 200
# 行末若无 sentence_end_ts 时的兜底拖音时长（毫秒）
_FALLBACK_TAIL_MS = 500


class ASSDirectExporter(BaseExporter):
    """ASS 字幕直接导出器

    直接生成 Aegisub 兼容的 ASS 卡拉OK字幕。
    支持 \\k 时长标签和 Aegisub 注音 ({汉字|<かな})。
    """

    @property
    def name(self) -> str:
        return "ASS"

    @property
    def description(self) -> str:
        return "ASS 字幕格式（Advanced SubStation Alpha）"

    @property
    def file_extension(self) -> str:
        return ".ass"

    @property
    def file_filter(self) -> str:
        return "ASS 字幕文件 (*.ass)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 ASS 格式"""
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)

        lines: List[str] = []

        # ASS 文件头
        lines.extend(self._generate_header(project))
        lines.append("")

        # Styles
        lines.extend(self._generate_styles())
        lines.append("")

        # Events
        lines.extend(self._generate_events(project))

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _generate_header(self, project: Project) -> List[str]:
        title = project.metadata.title if project.metadata else "Untitled"
        return [
            "[Script Info]",
            f"Title: {title}",
            "ScriptType: v4.00+",
            "Collisions: Normal",
            "PlayDepth: 0",
            "Timer: 100.0000",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.601",
        ]

    def _generate_styles(self) -> List[str]:
        return [
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1",
            "Style: Karaoke,Arial,24,&H00FF6B6B,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,5,10,10,30,1",
        ]

    def _generate_events(self, project: Project) -> List[str]:
        lines = [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        # 预建 singer_id → Singer 映射，避免每行 O(n) 查找
        singer_map = {s.id: s for s in project.singers}

        for sentence in project.sentences:
            if not sentence.has_timetags:
                continue

            line_start_ms = sentence.global_timing_start_ms
            if line_start_ms is None:
                continue

            # 行结束时间：优先用本行 sentence_end_ts，否则用最大全局时间戳 + 兜底
            line_end_ms = self._compute_line_end_ms(sentence)

            # 行首尾留白（只作用于 Dialogue Start/End 时间，不影响 \k 段）
            start_str = self._format_timestamp(
                max(0, line_start_ms - _PRE_ROLL_MS), "ass"
            )
            end_str = self._format_timestamp(line_end_ms + _POST_ROLL_MS, "ass")

            # 卡拉OK文本（传入 singer_map 以便插入演唱者切换标记）
            karaoke_text = self._generate_karaoke_text(
                sentence, line_start_ms, line_end_ms, singer_map
            )

            # Name 字段：收集行内所有演唱者名（按出现顺序），用 _ 连接
            name_field = ""
            seen_singer_ids = set()
            singer_names = []
            for ch in sentence.characters:
                effective_id = ch.singer_id or sentence.singer_id
                if effective_id in seen_singer_ids:
                    continue
                seen_singer_ids.add(effective_id)
                singer = singer_map.get(effective_id)
                if singer is not None:
                    singer_names.append(singer.name)
            if singer_names:
                name_field = self._escape_ass_field("_".join(singer_names))

            event_line = (
                f"Dialogue: 0,{start_str},{end_str},Default,{name_field},0,0,0,karaoke,{karaoke_text}"
            )
            lines.append(event_line)

        return lines

    def _compute_line_end_ms(self, sentence: Sentence) -> int:
        """计算本行的结束时间（毫秒）。

        优先级：
        1. 最后一个 is_sentence_end 字符的 global_sentence_end_ts
        2. 行内最晚全局时间戳 + 兜底拖音
        """
        for ch in reversed(sentence.characters):
            if ch.is_sentence_end and ch.global_sentence_end_ts is not None:
                return ch.global_sentence_end_ts

        end = sentence.global_timing_end_ms
        if end is None:
            # 不应发生：has_timetags 已保证至少一个时间戳
            return 0
        return end + _FALLBACK_TAIL_MS

    def _generate_karaoke_text(
        self, sentence: Sentence, line_start_ms: int, line_end_ms: int,
        singer_map: dict = None
    ) -> str:
        """生成带卡拉OK效果的 Dialogue 文本（Aegisub 真实注音语法）。

        Aegisub karaoke-template 注音语法精确规则：
        - 无注音字：`{\\k<dur>}<字>`
        - 单 part 注音：`{\\k<dur>}<漢字>|<<かな>`
        - 多 part 注音（一字配多假名）：每个 part 一个独立 `\\k`，
            首 part 用 `|<` 绑给汉字，续 part 用 `#|` 单独成段。
            例如「届」配「とど」(2 parts)：
                `{\\k20}届|<と{\\k12}#|ど`
            其中 `\\k20` 是「と」段时长，`\\k12` 是「ど」段时长。

        - 无时间戳字符（标点、未打轴的连词后续字）追加到**前一个 \\k 块尾**，
            不产生新的 \\k 标签，避免时间轴偏移。
        - 行首所有未打轴字符并入 pre-roll 占位 `{\\k0}` 后。
        - 行首/行尾固定输出 `{\\k0}` 占位符，让用户自行用 Aegisub 模板/特效填充。

        参考用户提供的真实样例（一行 Aegisub 卡拉OK）：
            {\\k0}い{\\k8}つ{\\k36}か{\\k20}見|<み{\\k26}た
            {\\k10}夢|<ゆ{\\k31}#|め{\\k5}　{\\k10}届|<と{\\k12}#|ど{\\k0}
        """
        chars = sentence.characters

        if not chars:
            return "{\\k0}{\\k0}"

        # 行首占位 {\k0}（pre-roll 由 Dialogue Start 时间承担）
        parts: List[str] = ["{\\k0}"]

        # 1. 收集所有「有 ts 的字符」做为锚点；记录每个锚点的 ts 列表起点
        #    用于计算每个 \k 段的时长
        anchor_indices = [
            i for i, ch in enumerate(chars) if ch.global_timestamps
        ]
        if not anchor_indices:
            # 全行无打轴：整段并入 pre-roll，仅给 post-roll 占位收尾
            plain_text = "".join(self._escape_ass_text(c.char) for c in chars)
            parts.append(plain_text)
            parts.append("{\\k0}")
            return "".join(parts)

        # 2. 行首未打轴字符（在第一个锚点前）并入 pre-roll \k 区，
        #    不产生新的 \k。
        first_anchor = anchor_indices[0]
        for j in range(first_anchor):
            parts.append(self._escape_ass_text(chars[j].char))

        # 3. 构造所有 \k 段的「时间锚点序列」(扁平化的 ts 列表)：
        #    每个有 ts 字符贡献 len(global_timestamps) 个段（= part 数 或 1）。
        #    末段的 dur 用 line_end_ms 兜底。
        flat_anchors: List[Tuple[int, int, int]] = []
        # 每项 = (char_idx, part_idx, ts_ms)；part_idx=0 表示该字第一段
        for ci in anchor_indices:
            ch = chars[ci]
            for pi, ts in enumerate(ch.global_timestamps):
                flat_anchors.append((ci, pi, ts))

        # 计算每个段的下一锚点 ts（用于求 \k 时长）
        def next_ts(seg_idx: int) -> int:
            if seg_idx + 1 < len(flat_anchors):
                return flat_anchors[seg_idx + 1][2]
            return line_end_ms

        # 4. 逐段渲染。无 ts 字符（标点等）追加到所属字符的「最后一段」尾巴。
        #    所属字符 = 该字之前最近的一个有 ts 字符。
        # 先建立映射：char_idx → 该字最后段在 flat_anchors 的下标
        last_seg_of_char: Dict[int, int] = {}
        for seg_idx, (ci, pi, _) in enumerate(flat_anchors):
            last_seg_of_char[ci] = seg_idx

        # 预计算连词尾部字符：anchor ci 若 linked_to_next=True，则其后续
        # 无时间戳的 linked 字符链属于该连词，应并入 pi==0 的 kanji，不作 tail_text。
        anchor_indices_set = set(anchor_indices)
        compound_tail: Dict[int, List[int]] = {}
        for ci in anchor_indices:
            if not chars[ci].linked_to_next:
                continue
            tail: List[int] = []
            j = ci + 1
            while j < len(chars):
                if j in anchor_indices_set:
                    break  # 后续字符自有时间戳，独立渲染，不并入本连词
                tail.append(j)
                if not chars[j].linked_to_next:
                    break
                j += 1
            if tail:
                compound_tail[ci] = tail
        compound_tail_set = {j for tails in compound_tail.values() for j in tails}

        # 收集「该 seg 结尾要追加的无 ts 字符文字」
        tail_text: Dict[int, str] = {}
        # 遍历 chars，把每个无 ts 字符塞到「前一个有 ts 字符的最后段」尾巴；
        # 已归入 compound_tail 的字符跳过（它们并入了 kanji）。
        prev_anchor_ci: Optional[int] = None
        for j, ch in enumerate(chars):
            if ch.global_timestamps:
                prev_anchor_ci = j
                continue
            if j < first_anchor:
                continue  # 已并入 pre-roll
            if j in compound_tail_set:
                continue  # 连词尾部字符，已并入 kanji，不重复追加
            if prev_anchor_ci is None:
                continue
            tail_seg = last_seg_of_char[prev_anchor_ci]
            tail_text[tail_seg] = (
                tail_text.get(tail_seg, "") + self._escape_ass_text(ch.char)
            )

        # 5. 渲染每个段
        prev_char_idx = -1
        prev_effective_id = ""
        for seg_idx, (ci, pi, ts) in enumerate(flat_anchors):
            dur_ms = max(0, next_ts(seg_idx) - ts)
            k_cs = dur_ms // 10
            ch = chars[ci]

            # 演唱者变化标记：在新字符的第一段前检测
            if ci != prev_char_idx:
                effective_id = chars[ci].singer_id or sentence.singer_id
                if effective_id != prev_effective_id and singer_map:
                    singer = singer_map.get(effective_id)
                    if singer is not None:
                        escaped_name = self._escape_ass_text(singer.name)
                        parts.append(f"{{\\sing_{escaped_name}}}")
                prev_char_idx = ci
                prev_effective_id = effective_id

            if pi == 0:
                # 该字第一段：写字符（+ 连词尾部字符 + 可选首 part ruby）
                kanji = self._escape_ass_text(ch.char)
                # 连词：把后续无时间戳的 linked 字符文本并入 kanji
                if ci in compound_tail:
                    for linked_idx in compound_tail[ci]:
                        kanji += self._escape_ass_text(chars[linked_idx].char)
                if ch.ruby and ch.ruby.parts:
                    first_part_text = self._escape_ass_text(ch.ruby.parts[0].text)
                    seg_body = f"{kanji}|<{first_part_text}"
                else:
                    seg_body = kanji
            else:
                # 该字续段：用 #| 前缀，单独成段（无主文）
                # ruby.parts[pi] 必定存在（push_to_ruby 保证 parts 数 = ts 数）
                part_text = ""
                if ch.ruby and pi < len(ch.ruby.parts):
                    part_text = self._escape_ass_text(ch.ruby.parts[pi].text)
                seg_body = f"#|{part_text}"

            # 追加该段尾巴的无 ts 文字（标点等）
            seg_body += tail_text.get(seg_idx, "")
            parts.append(f"{{\\k{k_cs}}}{seg_body}")

        # 6. 行尾占位 {\k0}（post-roll 由 Dialogue End 时间承担）
        parts.append("{\\k0}")

        return "".join(parts)

    @staticmethod
    def _escape_ass_field(text: str) -> str:
        """转义 ASS Dialogue 字段值中的逗号。

        Dialogue 行以逗号分隔字段，Name 等字段里的逗号会破坏解析。
        """
        if not text:
            return ""
        return text.replace(",", "_").replace("\n", " ").replace("\r", "")

    @staticmethod
    def _escape_ass_text(text: str) -> str:
        """转义 ASS 文本中的特殊字符。

        ASS 里 `{` `}` `\\` 是标签语法的一部分，需转义。
        """
        if not text:
            return text
        # 反斜杠先处理，避免连锁替换
        text = text.replace("\\", "\\\\")
        text = text.replace("{", "\\{").replace("}", "\\}")
        return text
