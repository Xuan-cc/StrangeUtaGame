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
from strange_uta_game.backend.infrastructure.parsers.english_ruby import (
    EnglishRubyLookup,
    find_english_words,
)
from strange_uta_game.backend.infrastructure.parsers.e2k_engine import (
    EnglishToKanaEngine,
)


# 允许自动注音的字符类型白名单（#11）：英文字符、汉字、平假名、片假名
_RUBY_ALLOWED_TYPES = {
    CharType.ALPHABET,
    CharType.KANJI,
    CharType.HIRAGANA,
    CharType.KATAKANA,
    CharType.SOKUON,
    CharType.LONG_VOWEL,
}


@dataclass
class AutoCheckResult:
    """自动检查结果"""

    line_idx: int
    char_idx: int
    char: str
    check_count: int
    ruby: Optional[str]
    origin_block_id: int = -1
    # 注音来源："dict"=用户词典, "e2k"=英语词典, "library"=库函数, "self"=原字符, "none"=无注音
    origin_source: str = "none"


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
        # pykakasi 用于无约束分区的参考读音
        self._pykakasi_conv = None
        try:
            import pykakasi

            kks = pykakasi.kakasi()
            kks.setMode("J", "H")
            self._pykakasi_conv = kks.getConverter()
        except Exception:
            pass

    def _apply_dictionary(
        self, text: str, ruby_results: List[RubyResult]
    ) -> Tuple[List[RubyResult], set]:
        """用用户词典覆盖 ruby_results（最长匹配优先）。

        遍历文本，尝试将词典词条匹配到文本位置，匹配成功时替换同位置的 ruby_results。

        Returns:
            (合并后的 ruby_results, 被用户词典覆盖的字符索引集合)
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
            return ruby_results, covered

        filtered = [
            r
            for r in ruby_results
            if not (set(range(r.start_idx, r.end_idx)) & covered)
        ]
        merged = filtered + overrides
        merged.sort(key=lambda r: r.start_idx)
        return merged, covered

    def _apply_english_dictionary(
        self, text: str, ruby_results: List[RubyResult], dict_covered: set
    ) -> Tuple[List[RubyResult], set]:
        """对英文单词应用自动注音（#12 / 第八批 #9）。

        优先级（针对英文单词）：
          1. e2k 规则引擎（基于 CMU Pronouncing Dictionary 的音素规则转换）
          2. e2k.txt 词表（作为引擎失败时的 fallback）

        只覆盖未被用户词典占用的英文单词。

        Returns:
            (合并后的 ruby_results, 被英文注音覆盖的字符索引集合)
        """
        engine = EnglishToKanaEngine.instance()
        lookup = EnglishRubyLookup.instance()
        has_engine = engine.has()
        has_lookup = lookup.has()
        if not has_engine and not has_lookup:
            return ruby_results, set()
        e2k_covered: set[int] = set()
        overrides: List[RubyResult] = []
        for start, end, word in find_english_words(text):
            span = set(range(start, end))
            # 跳过被用户词典覆盖的范围
            if span & dict_covered:
                continue
            # 优先使用规则引擎
            reading = engine.convert(word) if has_engine else None
            # 引擎未命中则回退到静态词表
            if not reading and has_lookup:
                reading = lookup.lookup(word)
            if not reading:
                continue
            overrides.append(
                RubyResult(
                    text=word, reading=reading, start_idx=start, end_idx=end
                )
            )
            e2k_covered |= span
        if not overrides:
            return ruby_results, e2k_covered
        # 移除被英文注音覆盖位置上来自库函数的结果
        filtered = [
            r
            for r in ruby_results
            if not (set(range(r.start_idx, r.end_idx)) & e2k_covered)
        ]
        merged = filtered + overrides
        merged.sort(key=lambda r: r.start_idx)
        return merged, e2k_covered

    def _try_split_to_chars(self, word: str, reading: str) -> Optional[List[str]]:
        """尝试将多字词的读音拆分到各字符（三遍策略）。

        Pass 1: 约束回溯 — 用户字典 + 库分析器 + pykakasi 候选读音
        Pass 2: pykakasi 参考分区 — 使用 _partition_reading 三级匹配
        Pass 3: 无约束分区 — 空参考，尝试所有可能拆分

        Args:
            word: 多字词
            reading: 词的总读音

        Returns:
            各字符读音列表（如果可拆分），否则 None
        """
        if len(word) <= 1:
            return None

        clean_reading = reading.replace(",", "")
        if not clean_reading:
            return None

        n = len(word)

        # ---------- 收集每个字符的候选读音 ----------
        per_char_options: List[List[str]] = []
        pykakasi_refs: List[str] = []

        for ch in word:
            options: List[str] = []
            # 1. 用户字典单字条目
            for dict_word, dict_reading in self._dict:
                if dict_word == ch and dict_reading:
                    clean = dict_reading.replace(",", "")
                    if clean and clean not in options:
                        options.append(clean)
            # 2. 库分析器
            results = self._analyzer.analyze(ch)
            for r in results:
                if r.reading and r.reading != ch and r.reading not in options:
                    options.append(r.reading)
            # 3. pykakasi 参考读音（加入候选 + 保存为 ref）
            pyk_ref = ""
            if self._pykakasi_conv is not None:
                try:
                    converted = self._pykakasi_conv.do(ch)
                    if converted and converted != ch:
                        pyk_ref = converted
                        if pyk_ref not in options:
                            options.append(pyk_ref)
                except Exception:
                    pass
            pykakasi_refs.append(pyk_ref)

            per_char_options.append(options)

        # ---------- Pass 1: 约束回溯 ----------
        has_all_options = all(len(opts) > 0 for opts in per_char_options)
        if has_all_options:

            def backtrack(idx: int, remaining: str) -> Optional[List[str]]:
                if idx == n:
                    return [] if not remaining else None
                for opt in per_char_options[idx]:
                    if remaining.startswith(opt):
                        sub = backtrack(idx + 1, remaining[len(opt) :])
                        if sub is not None:
                            return [opt] + sub
                return None

            result = backtrack(0, clean_reading)
            if result is not None:
                return result

        # ---------- Pass 2: pykakasi 参考分区 ----------
        if any(r for r in pykakasi_refs):
            result = self._partition_reading(clean_reading, n, pykakasi_refs)
            if result is not None:
                return result

        # ---------- Pass 3: 无约束分区 ----------
        empty_refs = [""] * n
        result = self._partition_reading(clean_reading, n, empty_refs)
        return result

    def _partition_reading(
        self,
        reading: str,
        n: int,
        ref_readings: List[str],
        ki: int = 0,
        ri: int = 0,
    ) -> Optional[List[str]]:
        """递归分区读音到 n 个字符。三级匹配策略：精确 > 前缀 > 无约束。"""
        if ki == n:
            return [] if ri == len(reading) else None
        if ri >= len(reading):
            return None
        remaining_chars = n - ki
        remaining_reading = len(reading) - ri
        if remaining_reading < remaining_chars:
            return None
        max_len = remaining_reading - (remaining_chars - 1)

        ref = ref_readings[ki] if ki < len(ref_readings) else ""
        tried: set = set()

        # 优先精确匹配
        if ref:
            ref_len = len(ref)
            if ref_len <= max_len:
                portion = reading[ri : ri + ref_len]
                if portion == ref:
                    rest = self._partition_reading(
                        reading, n, ref_readings, ki + 1, ri + ref_len
                    )
                    if rest is not None:
                        return [portion] + rest
                    tried.add(ref_len)

        # 前缀匹配
        for try_len in range(1, max_len + 1):
            if try_len in tried:
                continue
            portion = reading[ri : ri + try_len]
            if ref and not ref.startswith(portion):
                continue
            rest = self._partition_reading(
                reading, n, ref_readings, ki + 1, ri + try_len
            )
            if rest is not None:
                return [portion] + rest
            tried.add(try_len)

        # 无约束匹配
        for try_len in range(1, max_len + 1):
            if try_len in tried:
                continue
            rest = self._partition_reading(
                reading, n, ref_readings, ki + 1, ri + try_len
            )
            if rest is not None:
                return [reading[ri : ri + try_len]] + rest
        return None

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

        # #11: 过滤掉符号/括号等非目标字符的注音条目
        # 自动注音仅针对：英文字符、英文单词、汉字、日汉字、平假名、片假名
        def _result_should_keep(r: RubyResult) -> bool:
            if not r.text:
                return False
            # 检查首字符类型即可（ruby_results 的 text 通常是整词或单字符）
            for c in r.text:
                ct = get_char_type(c) if len(c) == 1 else CharType.OTHER
                if ct in _RUBY_ALLOWED_TYPES:
                    return True
            return False

        ruby_results = [r for r in ruby_results if _result_should_keep(r)]

        # 应用用户词典覆盖（最长优先匹配） — 记录被用户词典覆盖的索引集合
        dict_covered: set = set()
        if self._dict:
            ruby_results, dict_covered = self._apply_dictionary(text, ruby_results)

        # #12: 应用英语词典（e2k）覆盖（用户词典之后，库函数之前的优先级）
        ruby_results, e2k_covered = self._apply_english_dictionary(
            text, ruby_results, dict_covered
        )

        # 记录每个块的来源（用于 #10 连词判定）
        block_source: Dict[int, str] = {}
        for block_id, result in enumerate(ruby_results):
            span = set(range(result.start_idx, result.end_idx))
            if span & dict_covered:
                block_source[block_id] = "dict"
            elif span & e2k_covered:
                block_source[block_id] = "e2k"
            else:
                block_source[block_id] = "library"

        # 创建字符到注音的映射（按 mora 分割到每个字符）
        char_to_ruby: Dict[int, str] = {}
        char_to_block: Dict[int, int] = {}
        for block_id, result in enumerate(ruby_results):
            block_len = result.end_idx - result.start_idx
            # 词典条目可能用逗号分隔各字符的读音（如 "だい,ぼう,けん"）
            if "," in (result.reading or "") and block_len > 1:
                parts = [p.strip() for p in result.reading.split(",")]
                # 补齐不足的部分
                while len(parts) < block_len:
                    parts.append("")
                split_parts = parts[:block_len]
            else:
                if block_len > 1:
                    # 尝试按单字读音拆分，不可拆分则连词
                    char_split = self._try_split_to_chars(result.text, result.reading)
                    if char_split is not None:
                        split_parts = char_split
                    else:
                        # 连词：第一字承载全部读音，其余为空
                        split_parts = [result.reading] + [""] * (block_len - 1)
                else:
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
            # 词典条目可能用逗号分隔各字符的读音（如 "だい,ぼう,けん"）
            if "," in (result.reading or "") and block_len > 1:
                parts = [p.strip() for p in result.reading.split(",")]
                while len(parts) < block_len:
                    parts.append("")
                split_parts = parts[:block_len]
            else:
                if block_len > 1:
                    char_split = self._try_split_to_chars(result.text, result.reading)
                    if char_split is not None:
                        split_parts = char_split
                    else:
                        split_parts = [result.reading] + [""] * (block_len - 1)
                else:
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
            block_id = char_to_block.get(i, -1)
            source = block_source.get(block_id, "self" if not char_to_ruby.get(i) else "self")
            # 无注音块时 fallback 为 "self"（由后续 per-char 自注音补上）
            if block_id < 0:
                source = "self"
            results.append(
                AutoCheckResult(
                    line_idx=0,  # 将在 analyze_project 中设置
                    char_idx=i,
                    char=char,
                    check_count=count,
                    ruby=char_to_ruby.get(i),
                    origin_block_id=block_id,
                    origin_source=source,
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
        old_sentence_end_ts: Dict[int, int] = {}
        old_singer_map: Dict[int, str] = {}
        for i, char in enumerate(sentence.characters):
            if char.timestamps:
                old_timestamps[i] = list(char.timestamps)
            if char.sentence_end_ts is not None:
                old_sentence_end_ts[i] = char.sentence_end_ts
            old_singer_map[i] = char.singer_id

        # 构建新的 Character 对象列表
        add_line_end = self._flags.get("check_line_end", True)
        check_space_as_line_end = self._flags.get("check_space_as_line_end", True)
        new_characters: List[Character] = []
        for i, result in enumerate(results):
            is_last = i == len(results) - 1
            # 空格视为句尾：当前字符后面紧跟空格时额外+1
            is_before_space = (
                not is_last
                and check_space_as_line_end
                and i + 1 < len(results)
                and len(results[i + 1].char) == 1
                and results[i + 1].char.isspace()
            )
            extra = 0
            is_sentence_end = False
            if is_last and add_line_end:
                is_sentence_end = True
            if is_before_space:
                is_sentence_end = True
            check_count = result.check_count

            # 每个字符直接携带自己的 Ruby（无需跨字符合并）
            ruby_obj = Ruby(text=result.ruby) if result.ruby else None

            character = Character(
                char=result.char,
                ruby=ruby_obj,
                check_count=check_count,
                is_line_end=(is_last and add_line_end),
                is_sentence_end=is_sentence_end,
                singer_id=old_singer_map.get(i, sentence.singer_id),
            )
            new_characters.append(character)

        # 设置 linked_to_next: 当下一个字符 check_count==0 时，当前字符连词到下一个
        # 空格字符不应触发连词（空格 check_count==0 是过滤规则的结果，不代表连读）
        # #10: 仅当注音来源是「用户词典」或「e2k 英语词典」时才允许连词；
        #      库函数（Sudachi/pykakasi）和自注音的结果一律不连词。
        _LINKABLE_SOURCES = {"dict", "e2k"}
        for i in range(len(new_characters) - 1):
            next_ch = new_characters[i + 1]
            if next_ch.char and next_ch.char.isspace():
                continue
            if next_ch.check_count == 0:
                cur_src = results[i].origin_source if i < len(results) else "self"
                next_src = (
                    results[i + 1].origin_source if i + 1 < len(results) else "self"
                )
                # 仅可连词来源且属于同一个注音块时，才建立连词关系
                if (
                    cur_src in _LINKABLE_SOURCES
                    and next_src in _LINKABLE_SOURCES
                    and results[i].origin_block_id
                    == results[i + 1].origin_block_id
                ):
                    new_characters[i].linked_to_next = True

        # 恢复时间标签
        if keep_existing_timetags:
            for i, char in enumerate(new_characters):
                if i in old_timestamps:
                    char.timestamps = old_timestamps[i]
                if char.is_sentence_end and i in old_sentence_end_ts:
                    char.sentence_end_ts = old_sentence_end_ts[i]
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
        check_space_as_line_end = self._flags.get("check_space_as_line_end", True)
        for i, char in enumerate(sentence.characters):
            is_last = i == len(sentence.characters) - 1
            # 空格视为句尾：当前字符后面紧跟空格时额外+1
            is_before_space = (
                not is_last
                and check_space_as_line_end
                and i + 1 < len(sentence.characters)
                and len(chars[i + 1]) == 1
                and chars[i + 1].isspace()
            )
            extra = 0
            is_sentence_end = False
            if is_last and add_line_end:
                is_sentence_end = True
            if is_before_space:
                is_sentence_end = True
            char.check_count = check_counts[i]
            char.is_line_end = is_last and add_line_end
            char.is_sentence_end = is_sentence_end
            if not char.is_sentence_end:
                char.clear_sentence_end_ts()

        # #10: 此函数仅更新节奏点，不改变 linked_to_next。
        # linked_to_next 已由 analyze_sentence/apply_to_sentence 根据注音来源
        # （用户词典/e2k/库函数）正确设置，不应被此函数覆盖。
        # 仅清理与当前 check_count 不符的连词关系（例如原本连词但下一字符 check_count 变为非0）。
        for i in range(len(sentence.characters) - 1):
            next_ch = sentence.characters[i + 1]
            if sentence.characters[i].linked_to_next and next_ch.check_count != 0:
                sentence.characters[i].linked_to_next = False

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
