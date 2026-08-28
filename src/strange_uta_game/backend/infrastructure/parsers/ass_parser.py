r"""ASS 字幕格式解析器

支持 ASS/SSA 字幕文件的解析。提取卡拉OK时间标签（\k/\kf/\ko/\K/\kF/\kO）、
Aegisub 注音 (`{\k...}汉字|<かな`)，末尾 \k 的尾部时长（绑为句尾释放点），
非末尾空文本 \k 段的段起点（绑为句中停顿/断句轴点释放 ts），
以及 SUG 私有 `{\\sing_<name>}` per-char 演唱者切换标记。

设计原则（与 entities.py 对齐）：
1. 末尾 \k 的时长不再丢弃，作为 ParsedLine.line_end_ts 输出，
   parse_to_sentences 会把它绑给末字符的 sentence_end_ts。
   第三方（非 SUG 哨兵）文件再与 Dialogue End 取较大者；无 \k 的
   普通 Dialogue 行直接用 Dialogue End 作为 line_end_ts。
1b. has_karaoke_tags / has_ruby_syntax 暴露内容特征，供上层在导入后
   决定是否弹「保留原有注音」三选一（与 Nicokara 导入一致）。
2. Aegisub 注音 `{\k...}汉字|<かな` 不再被无差别去掉，而是按段提取，
   写入 ParsedLine.ruby_map。
3. 仅保留卡拉OK相关的 `\k/\kf/\ko/\\K/\kF/\kO` 与 `\\sing_*` 计算；
   其他 ASS 标签（`\\b`, `\\r`, `\\c` 等）剥除时不连带删掉注音文本。

SUG 私有约定（roundtrip 用）：
- [Script Info] 段若含 `; Generator: StrangeUtaGame`，启用 pre-roll 补偿：
  Dialogue Start 实际比首字 ts 早 SUG-PreRollMs，解析时 start_ms 加回去；
  line_end_ts 自然回到 last_end_ts（post-roll 只影响 Dialogue End，不影响 \k 链）。
- 第三方工具写的 ASS 没有哨兵 → 不补偿，按原语义解析。
"""

import re
from typing import Dict, List, Optional, Tuple

from .lyric_parser import LyricParser, ParsedLine


class ASSParser(LyricParser):
    """ASS 字幕格式解析器"""

    # ASS 时间戳格式: H:MM:SS.cc
    ASS_TIME_PATTERN = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")

    # 卡拉OK标签: {\kf32}, {\k50}, {\ko10}, {\K30}, {\kF20}, {\kO15}
    # 注意：模式不能匹配 {\sing_...}（\sing 以 s 开头不是 [kK]）
    KARAOKE_TAG_PATTERN = re.compile(r"\{\\[kK][oOfF]?(\d+)\}")

    # SUG 私有 per-char singer 切换标记: {\sing_<name>}
    # name 允许中文/拉丁/数字/下划线/连字符，遇到 `}` 结束
    SING_TAG_PATTERN = re.compile(r"\{\\sing_([^}]*)\}")

    # Dialogue 行（10 个字段）
    DIALOGUE_PATTERN = re.compile(
        r"^Dialogue:\s*\d+,"  # Layer
        r"([^,]+),"  # Start time
        r"([^,]+),"  # End time
        r"[^,]*,"  # Style
        r"[^,]*,"  # Name
        r"[^,]*,"  # MarginL
        r"[^,]*,"  # MarginR
        r"[^,]*,"  # MarginV
        r"[^,]*,"  # Effect
        r"(.*)$"  # Text
    )

    # [Script Info] 中的 SUG 哨兵 & 数值 & 标准 Title
    _SUG_GENERATOR_RE = re.compile(
        r"^\s*;\s*Generator\s*:\s*StrangeUtaGame\s*$", re.IGNORECASE
    )
    _SUG_PRE_ROLL_RE = re.compile(
        r"^\s*;\s*SUG-PreRollMs\s*:\s*(\d+)\s*$", re.IGNORECASE
    )
    _SUG_POST_ROLL_RE = re.compile(
        r"^\s*;\s*SUG-PostRollMs\s*:\s*(\d+)\s*$", re.IGNORECASE
    )
    _TITLE_RE = re.compile(r"^Title\s*:\s*(.*)$", re.IGNORECASE)

    def __init__(self) -> None:
        # parse() 填充；parse_metadata() 暴露给上层
        self.metadata: Dict[str, str] = {}
        self._is_sug: bool = False
        self._pre_roll_ms: int = 0
        self._post_roll_ms: int = 0
        # parse() 填充的内容特征（供上层决定导入后处理，如是否弹
        #「保留原有注音」三选一）：
        # - has_karaoke_tags: 任一 Dialogue 行含 {\k...} 卡拉OK标签
        # - has_ruby_syntax:  任一解析行产出 ruby_map（`汉字|かな` 语法）
        self.has_karaoke_tags: bool = False
        self.has_ruby_syntax: bool = False

    def parse_metadata(self) -> Dict[str, str]:
        """返回上一次 parse() 收集到的元数据（Title 等）。

        仅 ASSParser 暴露此方法；调用方在 parse() 之后取用。
        """
        return dict(self.metadata)

    def parse(self, content: str) -> List[ParsedLine]:
        """解析 ASS 格式内容"""
        # 重置状态，避免同一实例多次解析时旧 metadata 残留
        self.metadata = {}
        self._is_sug = False
        self._pre_roll_ms = 0
        self._post_roll_ms = 0
        self.has_karaoke_tags = False
        self.has_ruby_syntax = False

        lines: List[ParsedLine] = []
        section = ""  # 当前所在 section（小写）

        for raw_line in content.split("\n"):
            raw_line = raw_line.rstrip("\r")
            stripped = raw_line.strip()

            # section 切换
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped.lower()
                continue

            if section == "[script info]":
                self._consume_script_info_line(stripped)
                continue

            if section != "[events]":
                continue

            if stripped.startswith("Format:") or stripped.startswith(";"):
                continue

            match = self.DIALOGUE_PATTERN.match(stripped)
            if not match:
                continue

            start_time_str = match.group(1).strip()
            end_time_str = match.group(2).strip()
            text_field = match.group(3)

            start_ms = self._parse_ass_timestamp(start_time_str)
            if start_ms is None:
                continue

            end_ms = self._parse_ass_timestamp(end_time_str)
            # 结束时间戳必须晚于起始才有意义（防手写错位）
            if end_ms is not None and end_ms <= start_ms:
                end_ms = None

            # SUG 哨兵：实际首字 ts = Dialogue Start + pre_roll_ms。
            # 第三方 ASS 没有哨兵 → 不补偿。
            if self._is_sug:
                start_ms = max(0, start_ms + self._pre_roll_ms)

            if self.KARAOKE_TAG_PATTERN.search(text_field):
                self.has_karaoke_tags = True

            parsed_line = self._parse_karaoke_text(text_field, start_ms, end_ms)
            if parsed_line and parsed_line.text.strip():
                if parsed_line.ruby_map:
                    self.has_ruby_syntax = True
                lines.append(parsed_line)

        return lines

    def _consume_script_info_line(self, line: str) -> None:
        """处理 [Script Info] 段的一行"""
        if not line:
            return

        if self._SUG_GENERATOR_RE.match(line):
            self._is_sug = True
            self.metadata["generator"] = "StrangeUtaGame"
            return

        m = self._SUG_PRE_ROLL_RE.match(line)
        if m:
            try:
                self._pre_roll_ms = int(m.group(1))
            except ValueError:
                pass
            return

        m = self._SUG_POST_ROLL_RE.match(line)
        if m:
            try:
                self._post_roll_ms = int(m.group(1))
            except ValueError:
                pass
            return

        m = self._TITLE_RE.match(line)
        if m:
            self.metadata["title"] = m.group(1).strip()
            return

    def _parse_ass_timestamp(self, time_str: str) -> Optional[int]:
        """解析 ASS 时间戳 H:MM:SS.cc → 毫秒"""
        match = self.ASS_TIME_PATTERN.match(time_str.strip())
        if not match:
            return None

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        centis = int(match.group(4))

        return ((hours * 3600 + minutes * 60 + seconds) * 1000) + (centis * 10)

    # ──────────────────────────────────────────────
    # 段内文本处理
    # ──────────────────────────────────────────────

    @staticmethod
    def _strip_non_karaoke_tags(text: str) -> str:
        r"""剥除非卡拉OK、非 \sing_* 的 ASS 标签（如 {\r}, {\b1}, {\c&HFFFFFF&}）。

        卡拉OK标签 {\k.../\kf.../\ko...} 与 SUG 私有 {\sing_...} 在外部已经
        先抽走，这里只用去除残留装饰性标签。注音文本（| 后内容）保持原样。
        """
        return re.sub(r"\{[^}]*\}", "", text)

    @staticmethod
    def _classify_segment(segment: str) -> Tuple[str, str, str, str]:
        r"""识别 \k 段的语义类型并拆分。

        Aegisub 注音三类段：
        - "continuation"  `#|<かな>` 或 `#|かな`: 续段，无主文，假名归属上一汉字
        - "with_ruby"     `<汉字>|<<かな>` 或 `<汉字>|かな`: 首段，主文+首 part 假名
        - "plain"         无 `|`: 普通段，仅主文（也可能空）

        Returns:
            (kind, main_text, ruby_text, raw_segment)
            kind ∈ {"continuation", "with_ruby", "plain"}
        """
        stripped = segment.lstrip()
        if stripped.startswith("#|"):
            rest = stripped[2:]
            ruby_text = rest.lstrip("<")
            return "continuation", "", ruby_text, segment

        if "|" in segment:
            main, _, ruby = segment.partition("|")
            ruby = ruby.lstrip("<")
            return "with_ruby", main, ruby, segment

        return "plain", segment, "", segment

    def _parse_karaoke_text(
        self, text: str, start_ms: int, end_ms: Optional[int] = None
    ) -> Optional[ParsedLine]:
        r"""解析含卡拉OK标签的文本。

        Aegisub karaoke-template 注音真实语法（三类段）：
        - `{\k<d>}<字>`            普通段
        - `{\k<d>}<汉字>|<<かな>`   带 ruby 首段（新建字符 + ruby.parts[0]）
        - `{\k<d>}#|<かな>`         续段（不新建字符；ts 与 part 追加给前一字）

        SUG 扩展：`{\sing_<name>}` 可出现在任意 `{\k...}` 段前/段内，
        声明该段（及之后段）的演唱者，直到下一次 `\sing_` 切换。

        策略：
        - 先抽出全部 `\\k...` 与 `\\sing_...` token，按位置排序。
        - 按 `\\k` 段切片，每段用 `_classify_segment` 区分种类。
        - 每段开始前若有 `\\sing_`，更新 current_singer。
        - "with_ruby"/"plain"：产生新字符 + 一条 timetag；首字写入 char_singer_map。
        - "continuation"：把 (上一字的 char_idx → ts) 写入 extra_checkpoints_map，
          把假名追加到该字 ruby_map 的 parts_list。
        - 空文本 plain 段（`{\\k<N>}` 后无任何文字）：SUG 导出器用它表达
          句中停顿（断句轴点）后的间隙。非末尾空段的**段起点**即停顿释放
          ts，写入 gap_pause_map 绑给最近的有 ts 字符（parse_to_sentences
          还原为该字的 sentence_end_ts）；末尾空段是行尾 post-roll 占位
          `{\\k0}`，维持 line_end_ts 既有路径。
        - 累加 duration → 下一片的起始时间。
        - 末尾片的 duration 不丢弃，作为 `line_end_ts`。
        """
        karaoke_tags = list(self.KARAOKE_TAG_PATTERN.finditer(text))

        if not karaoke_tags:
            clean_text = self._strip_non_karaoke_tags(text)
            if "|" in clean_text:
                main_text, _, _ = clean_text.partition("|")
                main_text = main_text.strip()
            else:
                main_text = clean_text.strip()
            if main_text:
                # 无 \k 的普通 Dialogue：行级时间轴，Start 给首字符，
                # End（Dialogue End）即整行末尾 → line_end_ts 绑行末字符。
                return ParsedLine(
                    text=main_text,
                    timetags=[(0, start_ms)],
                    line_end_ts=end_ms,
                    line_end_bind_last=True,
                )
            return None

        # 预扫所有 \sing_ 位置 → 排序索引，按 \k 段切片时同步推进
        sing_tags = list(self.SING_TAG_PATTERN.finditer(text))
        sing_iter_idx = 0

        lyric_chars: List[str] = []
        timetags: List[Tuple[int, int]] = []
        # char_idx → (parts_list, span_length)
        ruby_map: Dict[int, Tuple[List[str], int]] = {}
        extra_checkpoints_map: Dict[int, List[int]] = {}
        # char_idx → 句中停顿释放 ts（空文本 \k 间隙段的段起点）
        gap_pause_map: Dict[int, int] = {}
        char_singer_map: Dict[int, str] = {}
        current_ms = start_ms
        char_idx = 0
        last_duration_ms = 0
        last_char_idx_for_ruby: Optional[int] = None
        # 最近一个「有 ts」字符的 char_idx（空段停顿绑定的目标）
        last_timed_char_idx: Optional[int] = None
        current_singer: str = ""

        for i, tag_match in enumerate(karaoke_tags):
            duration_cs = int(tag_match.group(1))
            duration_ms = duration_cs * 10
            last_duration_ms = duration_ms

            # 推进 \sing_ 游标：任何位于本 \k 段之前（含同位置）的 \sing_
            # 都更新 current_singer，作为本段的有效 singer。
            # 注意：出现在「本段文本之后、下一段 \k 之前」的 \sing_ 属于下一段
            # 的切换（参考 SUG 导出契约：sing 标签插在新 singer 字符的 \k 段之前），
            # 由下一次迭代的 pre-loop 拾起，本段不消费。
            seg_left_bound = tag_match.start()
            while (
                sing_iter_idx < len(sing_tags)
                and sing_tags[sing_iter_idx].start() <= seg_left_bound
            ):
                current_singer = sing_tags[sing_iter_idx].group(1).strip()
                sing_iter_idx += 1

            text_start = tag_match.end()
            text_end = (
                karaoke_tags[i + 1].start()
                if i + 1 < len(karaoke_tags)
                else len(text)
            )

            raw_segment = text[text_start:text_end]
            cleaned = self._strip_non_karaoke_tags(raw_segment)
            kind, main_text, ruby_text, _ = self._classify_segment(cleaned)

            if kind == "plain" and not main_text:
                # 空文本 \k 段：SUG 导出的句中停顿间隙段（{\k<N>} 后无文字）。
                # 非末尾空段 = 断句轴点边界：段起点即停顿释放 ts，绑给最近的
                # 有 ts 字符；末尾空段是行尾占位 {\k0}，走 line_end_ts 路径。
                if i + 1 < len(karaoke_tags) and last_timed_char_idx is not None:
                    gap_pause_map[last_timed_char_idx] = current_ms
                current_ms += duration_ms
                continue

            if kind == "continuation":
                if last_char_idx_for_ruby is not None and ruby_text:
                    extra_checkpoints_map.setdefault(
                        last_char_idx_for_ruby, []
                    ).append(current_ms)
                    if last_char_idx_for_ruby in ruby_map:
                        parts_list, span = ruby_map[last_char_idx_for_ruby]
                        parts_list.append(ruby_text)
                        ruby_map[last_char_idx_for_ruby] = (parts_list, span)
                    else:
                        ruby_map[last_char_idx_for_ruby] = ([ruby_text], 1)
                if last_char_idx_for_ruby is not None:
                    last_timed_char_idx = last_char_idx_for_ruby
                current_ms += duration_ms
                continue

            if main_text:
                first_char_idx_in_segment = char_idx
                timetags.append((first_char_idx_in_segment, current_ms))
                if ruby_text:
                    ruby_map[first_char_idx_in_segment] = (
                        [ruby_text],
                        len(main_text),
                    )
                if current_singer:
                    # 整段所有字符共享同一 singer（含 multi-char span）
                    for offset in range(len(main_text)):
                        char_singer_map[first_char_idx_in_segment + offset] = (
                            current_singer
                        )
                last_char_idx_for_ruby = first_char_idx_in_segment

                for ch in main_text:
                    lyric_chars.append(ch)
                    char_idx += 1
                last_timed_char_idx = char_idx - 1

            current_ms += duration_ms

        lyric_text = "".join(lyric_chars).strip()
        if not lyric_text:
            return None

        # 去除前导空白带来的 char_idx 偏移
        full_text = "".join(lyric_chars)
        leading = len(full_text) - len(full_text.lstrip())
        if leading > 0:
            timetags = [
                (ci - leading, ts) for ci, ts in timetags if ci >= leading
            ]
            ruby_map = {
                ci - leading: rb for ci, rb in ruby_map.items() if ci >= leading
            }
            extra_checkpoints_map = {
                ci - leading: ts_list
                for ci, ts_list in extra_checkpoints_map.items()
                if ci >= leading
            }
            gap_pause_map = {
                ci - leading: ts
                for ci, ts in gap_pause_map.items()
                if ci >= leading
            }
            char_singer_map = {
                ci - leading: name
                for ci, name in char_singer_map.items()
                if ci >= leading
            }

        # 句尾释放：最后一片 \k 的 duration_ms 不丢弃
        line_end_ts: Optional[int] = None
        if timetags or extra_checkpoints_map:
            line_end_ts = current_ms
            all_ts = [ts for _, ts in timetags]
            for extras in extra_checkpoints_map.values():
                all_ts.extend(extras)
            if all_ts:
                last_ts = max(all_ts)
                if line_end_ts <= last_ts:
                    line_end_ts = last_ts + max(0, last_duration_ms)

        # 第三方文件的 Dialogue End 是行时间轴末尾的权威声明：
        # - 非 SUG 文件：\k 链末与 Dialogue End 取较大者（链末可能因厘秒
        #   舍入略超 End；End 也可能含 \k 链之外的尾部 padding），
        #   并绑到行末字符（line_end_bind_last）。
        # - SUG 文件：post-roll 只加在 Dialogue End 上（见文件头注释），
        #   行尾释放必须留在 \k 链末（链尾绑定），否则 round-trip 漂移。
        if not self._is_sug and end_ms is not None:
            if line_end_ts is None or end_ms > line_end_ts:
                line_end_ts = end_ms

        return ParsedLine(
            text=lyric_text,
            timetags=timetags,
            line_end_ts=line_end_ts,
            line_end_bind_last=not self._is_sug,
            ruby_map=ruby_map,
            extra_checkpoints_map=extra_checkpoints_map,
            gap_pause_map=gap_pause_map,
            char_singer_map=char_singer_map,
        )
