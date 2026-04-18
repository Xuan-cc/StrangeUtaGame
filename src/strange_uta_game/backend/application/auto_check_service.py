"""自动检查服务。

分析歌词文本，计算节奏点数量，生成注音。
"""

from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass

from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Ruby,
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
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    split_ruby_for_checkpoints,
    split_into_moras,
)


@dataclass
class AutoCheckResult:
    """自动检查结果"""

    line_idx: int
    char_idx: int
    char: str
    check_count: int
    ruby: Optional[str]
    origin_block_id: int = -1


class AutoCheckService:
    """自动检查服务

    分析歌词文本：
    1. 拆分字符
    2. 分析注音
    3. 计算节奏点数量
    4. 构建 Character 对象
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
        covered: set[int] = set()
        overrides: List[RubyResult] = []

        for word, reading in self._dict:
            wlen = len(word)
            pos = 0
            while pos <= len(text) - wlen:
                if text[pos : pos + wlen] == word:
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

        filtered = [
            r
            for r in ruby_results
            if not (set(range(r.start_idx, r.end_idx)) & covered)
        ]
        merged = filtered + overrides
        merged.sort(key=lambda r: r.start_idx)
        return merged

    def analyze_sentence(
        self, sentence: Sentence, split_config: Optional[SplitConfig] = None
    ) -> List[AutoCheckResult]:
        """分析句子歌词

        Args:
            sentence: 句子
            split_config: 拆分配置

        Returns:
            分析结果列表
        """
        text = sentence.text
        if not text:
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
        chars, check_counts = split_text(text, split_config)

        # 分析注音
        ruby_results = self._analyzer.analyze(text)

        # 应用用户词典覆盖（最长优先匹配）
        if self._dict:
            ruby_results = self._apply_dictionary(text, ruby_results)

        # 创建字符到注音的映射（按 mora 分割到每个字符）
        char_to_ruby: Dict[int, str] = {}
        char_to_block: Dict[int, int] = {}
        for block_id, result in enumerate(ruby_results):
            block_len = result.end_idx - result.start_idx
            # 按 mora 分割 reading，为每个字符分配 ruby（包括自注音字符）
            split_parts = split_ruby_for_checkpoints(result.reading, block_len)
            for idx in range(result.start_idx, result.end_idx):
                if idx < len(chars):
                    pos = idx - result.start_idx
                    if pos < len(split_parts) and split_parts[pos]:
                        char_to_ruby[idx] = split_parts[pos]
                    char_to_block[idx] = block_id

        # 未被分析器覆盖的字符使用自注音（保证所有字符都有 ruby）
        for idx, char in enumerate(chars):
            if idx not in char_to_ruby:
                char_to_ruby[idx] = char

        # 根据注音更新 check_count（汉字按 mora 数分配节奏点）
        for result in ruby_results:
            if result.text == result.reading:
                continue  # 假名/符号/空格等读音与原文相同，不更新

            block_len = result.end_idx - result.start_idx
            split_parts = split_ruby_for_checkpoints(result.reading, block_len)
            for idx in range(result.start_idx, result.end_idx):
                if idx < len(check_counts):
                    pos = idx - result.start_idx
                    if pos < len(split_parts) and split_parts[pos]:
                        check_counts[idx] = len(split_into_moras(split_parts[pos]))
                    else:
                        check_counts[idx] = 0

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

                # 小写假名（排除促音っ/ッ，已有独立flag）
                _SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮゕゖ")
                if char in _SMALL_KANA and not self._flags.get("small_kana", False):
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
                    line_idx=0,  # 将在 analyze_project 中设置
                    char_idx=i,
                    char=char,
                    check_count=count,
                    ruby=char_to_ruby.get(i),
                    origin_block_id=char_to_block.get(i, -1),
                )
            )

        return results

    def apply_to_sentence(
        self,
        sentence: Sentence,
        split_config: Optional[SplitConfig] = None,
        keep_existing_timetags: bool = True,
    ) -> None:
        """分析并应用自动检查结果到句子

        构建新的 Character 对象列表，每个字符直接携带自己的 Ruby。
        相比旧的多字符 Ruby 合并方式更简洁。

        Args:
            sentence: 句子
            split_config: 拆分配置
            keep_existing_timetags: 是否保留现有时间标签
        """
        results = self.analyze_sentence(sentence, split_config)

        if not results:
            return

        # 保留现有时间标签和演唱者映射
        old_timestamps: Dict[int, List[int]] = {}
        old_singer_map: Dict[int, str] = {}
        for i, char in enumerate(sentence.characters):
            if char.timestamps:
                old_timestamps[i] = list(char.timestamps)
            old_singer_map[i] = char.singer_id

        # 构建新的 Character 对象列表
        add_line_end = self._flags.get("check_line_end", True)
        new_characters: List[Character] = []
        for i, result in enumerate(results):
            is_last = i == len(results) - 1
            check_count = result.check_count + (1 if is_last and add_line_end else 0)

            # 每个字符直接携带自己的 Ruby（无需跨字符合并）
            ruby_obj = Ruby(text=result.ruby) if result.ruby else None

            character = Character(
                char=result.char,
                ruby=ruby_obj,
                check_count=check_count,
                is_line_end=(is_last and add_line_end),
                singer_id=old_singer_map.get(i, sentence.singer_id),
            )
            new_characters.append(character)

        # 设置 linked_to_next: 当下一个字符 check_count==0 时，当前字符连词到下一个
        for i in range(len(new_characters) - 1):
            if new_characters[i + 1].check_count == 0:
                new_characters[i].linked_to_next = True

        # 恢复时间标签
        if keep_existing_timetags:
            for i, char in enumerate(new_characters):
                if i in old_timestamps:
                    char.timestamps = old_timestamps[i]
                    char.push_to_ruby()

        sentence.characters = new_characters

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

        for i, sentence in enumerate(project.sentences):
            sent_results = self.analyze_sentence(sentence, split_config)
            # 更新行索引
            for r in sent_results:
                r.line_idx = i
            results.append((i, sent_results))

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
        for sentence in project.sentences:
            self.apply_to_sentence(sentence, split_config, keep_existing_timetags)

    def update_checkpoints_from_rubies(
        self,
        sentence: Sentence,
        split_config: Optional[SplitConfig] = None,
    ) -> None:
        """根据现有注音更新节奏点配置（不重新分析注音）

        仅更新 checkpoint 的 check_count，保留现有的 Ruby 不变。
        在新模型中，每个字符直接持有自己的 Ruby，无需跨字符拆分。

        Args:
            sentence: 句子
            split_config: 拆分配置
        """
        if not sentence.characters:
            return

        split_config = split_config or SplitConfig()

        # 使用 text_splitter 获取默认节奏点数
        _, check_counts = split_text(sentence.text, split_config)

        # 确保长度匹配
        while len(check_counts) < len(sentence.characters):
            check_counts.append(1)
        check_counts = check_counts[: len(sentence.characters)]

        # 根据现有 per-char 注音更新 check_count
        for i, char in enumerate(sentence.characters):
            if not char.ruby:
                continue
            if char.char == char.ruby.text:
                continue  # 自注音（假名等），保留默认 check_count
            # 汉字等：按 Ruby 的 mora 数分配节奏点
            check_counts[i] = len(split_into_moras(char.ruby.text))

        # 应用自动打勾过滤规则（与 analyze_sentence 相同逻辑）
        chars = [c.char for c in sentence.characters]
        if self._flags:
            for i, ch in enumerate(chars):
                if i >= len(check_counts):
                    break

                ct = get_char_type(ch) if len(ch) == 1 else CharType.OTHER

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

                if ch == "ん" and not self._flags.get("check_n", False):
                    check_counts[i] = 0
                    continue

                if ct == CharType.SOKUON and not self._flags.get("check_sokuon", False):
                    check_counts[i] = 0
                    continue

                _SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮゕゖ")
                if ch in _SMALL_KANA and not self._flags.get("small_kana", False):
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
                for i, ch in enumerate(chars):
                    if ch in ("(", "（"):
                        in_paren = True
                    elif ch in (")", "）"):
                        in_paren = False
                    elif in_paren and i < len(check_counts):
                        check_counts[i] = 0

            if self._flags.get("check_line_start", False) and check_counts:
                check_counts[0] = max(check_counts[0], 1)

        # 更新字符属性
        add_line_end = self._flags.get("check_line_end", True)
        for i, char in enumerate(sentence.characters):
            is_last = i == len(sentence.characters) - 1
            char.check_count = check_counts[i] + (1 if is_last and add_line_end else 0)
            char.is_line_end = is_last and add_line_end

        # 重置并设置 linked_to_next
        for char in sentence.characters:
            char.linked_to_next = False
        for i in range(len(sentence.characters) - 1):
            if sentence.characters[i + 1].check_count == 0:
                sentence.characters[i].linked_to_next = True

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
        for sentence in project.sentences:
            self.update_checkpoints_from_rubies(sentence, split_config)

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
