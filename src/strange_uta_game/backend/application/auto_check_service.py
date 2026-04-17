"""自动检查服务。

分析歌词文本，计算节奏点数量，生成注音。
"""

from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass

from strange_uta_game.backend.domain import (
    Project,
    LyricLine,
    Ruby,
    CheckpointConfig,
)
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    split_text,
    SplitConfig,
    get_char_type,
    CharType,
)
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    create_analyzer,
    RubyAnalyzer,
    RubyResult,
)


@dataclass
class AutoCheckResult:
    """自动检查结果"""

    line_idx: int
    char_idx: int
    char: str
    check_count: int
    ruby: Optional[str]


class AutoCheckService:
    """自动检查服务

    分析歌词文本：
    1. 拆分字符
    2. 分析注音
    3. 计算节奏点数量
    4. 生成 CheckpointConfig
    """

    def __init__(
        self,
        ruby_analyzer: Optional[RubyAnalyzer] = None,
        auto_check_flags: Optional[Dict[str, Any]] = None,
        user_dictionary: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Args:
            ruby_analyzer: 注音分析器（如果为 None 则自动创建）
            auto_check_flags: 自动打勾过滤标志
            user_dictionary: 用户读音词典，格式 [{"enabled": bool, "word": str, "reading": str}, ...]
        """
        self._analyzer = ruby_analyzer or create_analyzer()
        self._flags = auto_check_flags or {}
        # 只保留已启用的词典条目，按词长降序（最长优先匹配）
        raw = user_dictionary or []
        self._dict: List[Tuple[str, str]] = sorted(
            [
                (e["word"], e["reading"])
                for e in raw
                if e.get("enabled", True) and e.get("word") and e.get("reading")
            ],
            key=lambda x: len(x[0]),
            reverse=True,
        )

    def _apply_dictionary(
        self, text: str, ruby_results: List[RubyResult]
    ) -> List[RubyResult]:
        """用用户词典覆盖 ruby_results（最长匹配优先）。

        遍历文本，尝试将词典词条匹配到文本位置，匹配成功时替换同位置的 ruby_results。
        """
        # 构建 char_idx → RubyResult 的原始映射（只保留首 char_idx 的条目）
        covered: set[int] = set()
        overrides: List[RubyResult] = []

        for word, reading in self._dict:
            wlen = len(word)
            pos = 0
            while pos <= len(text) - wlen:
                if text[pos : pos + wlen] == word:
                    # 确认该区间未被更长的词典条目占用
                    span = set(range(pos, pos + wlen))
                    if not span & covered:
                        overrides.append(
                            RubyResult(
                                text=word,
                                reading=reading,
                                start_idx=pos,
                                end_idx=pos + wlen,
                            )
                        )
                        covered |= span
                    pos += wlen
                else:
                    pos += 1

        if not overrides:
            return ruby_results

        # 将覆盖区间内的原始 ruby_results 剔除，加入词典匹配结果
        filtered = [
            r
            for r in ruby_results
            if not (set(range(r.start_idx, r.end_idx)) & covered)
        ]
        merged = filtered + overrides
        merged.sort(key=lambda r: r.start_idx)
        return merged

    def analyze_line(
        self, line: LyricLine, split_config: Optional[SplitConfig] = None
    ) -> List[AutoCheckResult]:
        """分析单行歌词

        Args:
            line: 歌词行
            split_config: 拆分配置

        Returns:
            分析结果列表
        """
        if not line.text:
            if self._flags.get("check_empty_lines", False):
                return [
                    AutoCheckResult(
                        line_idx=0,
                        char_idx=0,
                        char="",
                        check_count=1,
                        ruby=None,
                    )
                ]
            return []

        split_config = split_config or SplitConfig()

        # 拆分文本
        chars, check_counts = split_text(line.text, split_config)

        # 分析注音
        ruby_results = self._analyzer.analyze(line.text)

        # 应用用户词典覆盖（最长优先匹配）
        if self._dict:
            ruby_results = self._apply_dictionary(line.text, ruby_results)

        # 创建字符到注音的映射
        char_to_ruby = {}
        for result in ruby_results:
            for idx in range(result.start_idx, result.end_idx):
                if idx < len(chars):
                    char_to_ruby[idx] = result.reading

        # 根据注音更新 check_count（汉字按读音假名数分配节奏点）
        for result in ruby_results:
            if result.text == result.reading:
                continue  # 假名/符号/空格等读音与原文相同，不更新

            block_len = result.end_idx - result.start_idx
            reading_len = len(result.reading)

            for idx in range(result.start_idx, result.end_idx):
                if idx < len(check_counts):
                    pos_in_block = idx - result.start_idx
                    base = reading_len // block_len
                    extra = 1 if pos_in_block < (reading_len % block_len) else 0
                    check_counts[idx] = base + extra

        # 应用自动打勾过滤规则
        if self._flags:
            for i, char in enumerate(chars):
                if i >= len(check_counts):
                    break

                ct = get_char_type(char) if len(char) == 1 else CharType.OTHER

                type_flag_map = {
                    CharType.HIRAGANA: "hiragana",
                    CharType.KATAKANA: "katakana",
                    CharType.KANJI: "kanji",
                    CharType.ALPHABET: "alphabet",
                    CharType.NUMBER: "digit",
                    CharType.SYMBOL: "symbol",
                    CharType.SPACE: "space",
                }
                flag_key = type_flag_map.get(ct)
                if flag_key and not self._flags.get(flag_key, True):
                    check_counts[i] = 0
                    continue

                if char == "ん" and not self._flags.get("check_n", False):
                    check_counts[i] = 0
                    continue

                if ct == CharType.SOKUON and not self._flags.get("check_sokuon", False):
                    check_counts[i] = 0
                    continue

                if ct == CharType.KANJI and self._flags.get(
                    "kanji_single_check", False
                ):
                    check_counts[i] = min(check_counts[i], 1)

                if ct == CharType.SPACE and i > 0:
                    prev_char = chars[i - 1]
                    prev_ct = (
                        get_char_type(prev_char)
                        if len(prev_char) == 1
                        else CharType.OTHER
                    )
                    if prev_ct in (
                        CharType.HIRAGANA,
                        CharType.KATAKANA,
                        CharType.KANJI,
                        CharType.SOKUON,
                        CharType.LONG_VOWEL,
                    ):
                        if not self._flags.get("space_after_japanese", True):
                            check_counts[i] = 0
                    elif prev_ct == CharType.ALPHABET:
                        if not self._flags.get("space_after_alphabet", True):
                            check_counts[i] = 0
                    elif prev_ct in (CharType.SYMBOL, CharType.NUMBER):
                        if not self._flags.get("space_after_symbol", True):
                            check_counts[i] = 0

            if not self._flags.get("check_parentheses", True):
                in_paren = False
                for i, char in enumerate(chars):
                    if char in ("(", "（"):
                        in_paren = True
                    elif char in (")", "）"):
                        in_paren = False
                    elif in_paren and i < len(check_counts):
                        check_counts[i] = 0

            if self._flags.get("check_line_start", False) and check_counts:
                check_counts[0] = max(check_counts[0], 1)

        # 构建结果
        results = []
        for i, (char, count) in enumerate(zip(chars, check_counts)):
            results.append(
                AutoCheckResult(
                    line_idx=0,  # 将在 apply_to_line 中设置
                    char_idx=i,
                    char=char,
                    check_count=count,
                    ruby=char_to_ruby.get(i),
                )
            )

        return results

    def apply_to_line(
        self,
        line: LyricLine,
        split_config: Optional[SplitConfig] = None,
        keep_existing_timetags: bool = True,
    ) -> None:
        """分析并应用自动检查结果到歌词行

        Args:
            line: 歌词行
            split_config: 拆分配置
            keep_existing_timetags: 是否保留现有时间标签
        """
        results = self.analyze_line(line, split_config)

        if not results:
            return

        # 更新 chars
        line.chars = [r.char for r in results]

        # 重建注音
        line.rubies.clear()
        current_ruby = None
        current_start = 0

        for i, result in enumerate(results):
            if result.ruby:
                if current_ruby is None:
                    current_ruby = result.ruby
                    current_start = i
                elif result.ruby != current_ruby:
                    # 注音变化，保存之前的
                    line.add_ruby(
                        Ruby(text=current_ruby, start_idx=current_start, end_idx=i)
                    )
                    current_ruby = result.ruby
                    current_start = i
            else:
                if current_ruby is not None:
                    # 保存之前的注音
                    line.add_ruby(
                        Ruby(text=current_ruby, start_idx=current_start, end_idx=i)
                    )
                    current_ruby = None

        # 处理最后一个注音
        if current_ruby is not None:
            line.add_ruby(
                Ruby(text=current_ruby, start_idx=current_start, end_idx=len(results))
            )

        # 重建 checkpoint 配置
        # 句尾字符额外 +1 节奏点用于记录行结束时间（长按释放）
        add_line_end = self._flags.get("check_line_end", True)
        line.checkpoints = [
            CheckpointConfig(
                char_idx=i,
                check_count=(
                    result.check_count
                    + (1 if i == len(results) - 1 and add_line_end else 0)
                ),
                is_line_end=(i == len(results) - 1 and add_line_end),
            )
            for i, result in enumerate(results)
        ]

        # 如果不保留时间标签，清空
        if not keep_existing_timetags:
            line.timetags.clear()

    def analyze_project(
        self, project: Project, split_config: Optional[SplitConfig] = None
    ) -> List[Tuple[int, List[AutoCheckResult]]]:
        """分析整个项目

        Args:
            project: 项目
            split_config: 拆分配置

        Returns:
            (行索引, 分析结果) 列表
        """
        results = []

        for i, line in enumerate(project.lines):
            line_results = self.analyze_line(line, split_config)
            # 更新行索引
            for r in line_results:
                r.line_idx = i
            results.append((i, line_results))

        return results

    def apply_to_project(
        self,
        project: Project,
        split_config: Optional[SplitConfig] = None,
        keep_existing_timetags: bool = True,
    ) -> None:
        """分析并应用到整个项目

        Args:
            project: 项目
            split_config: 拆分配置
            keep_existing_timetags: 是否保留现有时间标签
        """
        for line in project.lines:
            self.apply_to_line(line, split_config, keep_existing_timetags)

    def update_checkpoints_from_rubies(
        self,
        line: LyricLine,
        split_config: Optional[SplitConfig] = None,
    ) -> None:
        """根据现有注音更新节奏点配置（不重新分析注音）

        仅更新 checkpoint 的 check_count，保留现有的 rubies 不变。

        Args:
            line: 歌词行
            split_config: 拆分配置
        """
        if not line.chars:
            return

        split_config = split_config or SplitConfig()

        # 使用 text_splitter 获取默认节奏点数
        _, check_counts = split_text(line.text, split_config)

        # 确保长度匹配
        while len(check_counts) < len(line.chars):
            check_counts.append(1)
        check_counts = check_counts[: len(line.chars)]

        # 根据现有注音更新 check_count
        for ruby in line.rubies:
            target_text = "".join(line.chars[ruby.start_idx : ruby.end_idx])
            if target_text == ruby.text:
                continue  # 假名注音，不更新

            block_len = ruby.end_idx - ruby.start_idx
            reading_len = len(ruby.text)

            for idx in range(ruby.start_idx, min(ruby.end_idx, len(check_counts))):
                pos_in_block = idx - ruby.start_idx
                base = reading_len // block_len
                extra = 1 if pos_in_block < (reading_len % block_len) else 0
                check_counts[idx] = base + extra

        # 重建 checkpoint 配置
        add_line_end = self._flags.get("check_line_end", True)
        line.checkpoints = [
            CheckpointConfig(
                char_idx=i,
                check_count=count
                + (1 if i == len(check_counts) - 1 and add_line_end else 0),
                is_line_end=(i == len(check_counts) - 1 and add_line_end),
            )
            for i, count in enumerate(check_counts)
        ]

    def update_checkpoints_for_project(
        self,
        project: Project,
        split_config: Optional[SplitConfig] = None,
    ) -> None:
        """根据现有注音更新整个项目的节奏点配置（不重新分析注音）

        Args:
            project: 项目
            split_config: 拆分配置
        """
        for line in project.lines:
            self.update_checkpoints_from_rubies(line, split_config)

    def estimate_check_count(self, text: str) -> int:
        """估算文本的节奏点数量

        Args:
            text: 输入文本

        Returns:
            估算的节奏点数量
        """
        if not text:
            return 0

        try:
            results = self._analyzer.analyze(text)

            count = 0
            for result in results:
                # 汉字：注音假名数量
                if self._is_kanji(result.text[0]):
                    count += len(result.reading)
                # 假名：1 个
                elif self._is_kana(result.text[0]):
                    count += 1

            return count

        except Exception:
            # 如果分析失败，返回字符数作为保守估计
            return len(text)

    @staticmethod
    def _is_kanji(char: str) -> bool:
        """检查是否是汉字"""
        code = ord(char)
        return (
            (0x4E00 <= code <= 0x9FFF)
            or (0x3400 <= code <= 0x4DBF)
            or (0xF900 <= code <= 0xFAFF)
        )

    @staticmethod
    def _is_kana(char: str) -> bool:
        """检查是否是假名"""
        code = ord(char)
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)
