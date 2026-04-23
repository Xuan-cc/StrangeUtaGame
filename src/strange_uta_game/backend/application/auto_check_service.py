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
    RubyPart,
    PUNCTUATION_SET,
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
    _group_reading_for_character,
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


# 允许自动注音的字符类型白名单（第十批 #5）：
# 英文字符/英文词语、汉字/日汉字、平假名、片假名、阿拉伯数字
_RUBY_ALLOWED_TYPES = {
    CharType.ALPHABET,
    CharType.KANJI,
    CharType.HIRAGANA,
    CharType.KATAKANA,
    CharType.SOKUON,
    CharType.LONG_VOWEL,
    CharType.NUMBER,
}


def _has_latin(s: str) -> bool:
    """是否含有 ASCII 英文字母（用于词边界判定）。"""
    return any(c.isascii() and c.isalpha() for c in s)


@dataclass
class AutoCheckResult:
    """自动检查结果"""

    line_idx: int
    char_idx: int
    char: str
    check_count: int
    ruby: Optional[List[str]]  # Stage 0: _group_reading_for_character 返回 List[str]
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
        # 第十批 #5：建立英文整词 O(1) 查询表，作为 e2k 失败时的用户词典全词回退。
        # key 为小写词条，仅收录含 ASCII 英文字母的条目。
        self._dict_map: Dict[str, str] = {
            w.lower(): r for (w, r) in self._dict if _has_latin(w)
        }
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
            # 第十批 #5：含英文字母的词条须走整词边界匹配，否则 "we" 会命中 "answer" 中部。
            is_latin = _has_latin(word)
            pos = 0
            while pos <= len(text) - wlen:
                if text[pos : pos + wlen] == word:
                    # 英文词条的词边界检查：前后字符都不能是英文字母或 apostrophe
                    # （批 18 #7：' 和 ’ 属词内字符，否则 what 会在 what's 中部命中）
                    if is_latin:
                        def _is_word_inner(c: str) -> bool:
                            return (c.isascii() and c.isalpha()) or c in ("'", "\u2019")
                        left_ok = pos == 0 or not _is_word_inner(text[pos - 1])
                        right_ok = pos + wlen == len(text) or not _is_word_inner(
                            text[pos + wlen]
                        )
                        if not (left_ok and right_ok):
                            pos += 1
                            continue
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
        """对英文单词应用自动注音（第十批 #5）。

        用户要求的优先级：
          1. e2k 规则引擎（基于 CMU Pronouncing Dictionary 的音素规则转换）
          2. 用户词典整词回退（仅含字母的条目，通过 self._dict_map 做小写全词查询）
          3. e2k.txt 词表（EnglishRubyLookup 静态词表）

        只覆盖未被用户词典（非英文部分）占用的英文整词范围。
        本函数对命中的英文词以整词为粒度替换 ruby_results，使下游序列化产生
        形如 "{hello|ヘロー}" 的整词 ruby，而不是被 Sudachi 逐字符拆散。

        Returns:
            (合并后的 ruby_results, 被英文注音覆盖的字符索引集合)
        """
        engine = EnglishToKanaEngine.instance()
        lookup = EnglishRubyLookup.instance()
        has_engine = engine.has()
        has_lookup = lookup.has()
        has_user_dict = bool(self._dict_map)
        if not has_engine and not has_lookup and not has_user_dict:
            return ruby_results, set()
        e2k_covered: set[int] = set()
        overrides: List[RubyResult] = []
        for start, end, word in find_english_words(text):
            span = set(range(start, end))
            # 跳过已被用户词典（多字符跨英/非英复合词）占用的范围
            if span & dict_covered:
                continue
            # #11：规范化弯引号，保证 what\u2019s 也能命中 what's 条目
            from strange_uta_game.backend.infrastructure.parsers.english_ruby import (
                normalize_apostrophes,
            )

            normalized_word = normalize_apostrophes(word)
            # 第十批 #5 优先级：e2k → user dict 全词 → 静态 lookup
            reading = engine.convert(normalized_word) if has_engine else None
            if not reading and has_user_dict:
                reading = self._dict_map.get(normalized_word.lower())
            if not reading and has_lookup:
                reading = lookup.lookup(normalized_word)
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
        # 移除被英文注音覆盖位置上来自 Sudachi 的逐字符结果（防止 hello 被拆成 h/e/l/l/o）
        filtered = [
            r
            for r in ruby_results
            if not (set(range(r.start_idx, r.end_idx)) & e2k_covered)
        ]
        merged = filtered + overrides
        merged.sort(key=lambda r: r.start_idx)
        return merged, e2k_covered

    def _apply_english_fallback(
        self,
        text: str,
        ruby_results: List[RubyResult],
        dict_covered: set,
        e2k_covered: set,
    ) -> Tuple[List[RubyResult], set]:
        """批 17 #1：未命中任何词典的英文连续段作为整词 ruby。

        对 find_english_words 定位到、且未被 user_dict / e2k 覆盖的英文词，
        生成 RubyResult(text=word, reading=word)，整块挂 ruby，
        配合下游 check_counts 覆写（首字=1、其他=0）实现「英文词组首字母
        一个 cp、其他字母无 cp」的需求。

        单字母英文词（end-start <= 1）不视为"词组"，跳过以保留默认逐字 cp。

        Args:
            text: 原句子文本
            ruby_results: 当前已处理的 ruby 结果
            dict_covered: 用户词典已覆盖位置
            e2k_covered: e2k 英语词典已覆盖位置

        Returns:
            (合并后的 ruby_results, 本 fallback 覆盖的字符索引集合)
        """
        covered: set[int] = set()
        overrides: List[RubyResult] = []
        for start, end, word in find_english_words(text):
            if end - start <= 1:
                continue  # 单字母词：无词组概念，保留默认
            span = set(range(start, end))
            if span & dict_covered:
                continue
            if span & e2k_covered:
                continue
            # 整词 fallback：text == reading，下游 check_counts 覆写完成 cp 分配
            overrides.append(
                RubyResult(
                    text=word, reading=word, start_idx=start, end_idx=end
                )
            )
            covered |= span
        if not overrides:
            return ruby_results, covered
        # 移除 Sudachi 在这些位置的逐字符结果，防止残留
        filtered = [
            r
            for r in ruby_results
            if not (set(range(r.start_idx, r.end_idx)) & covered)
        ]
        merged = filtered + overrides
        merged.sort(key=lambda r: r.start_idx)
        return merged, covered

    def _try_split_to_chars(self, word: str, reading: str) -> Optional[List[str]]:
        """尝试将多字词的读音拆分到各字符（三遍策略）。

        Pass 1: 约束回溯 — 库分析器 + pykakasi 候选读音
        Pass 2: pykakasi 参考分区 — 使用 _partition_reading 三级匹配
        Pass 3: 无约束分区 — 空参考，尝试所有可能拆分

        注意：不查用户字典（用户字典由上游独立路径处理）。

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
            # 1. 库分析器
            results = self._analyzer.analyze(ch)
            for r in results:
                if r.reading and r.reading != ch and r.reading not in options:
                    options.append(r.reading)
            # 2. pykakasi 参考读音（加入候选 + 保存为 ref）
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

    def _get_single_char_candidates(self, ch: str) -> List[str]:
        """收集单个字符的候选读音（仅库：库分析器 + pykakasi）。

        用于连词回退时的「头尾假名剥离」策略。
        注意：不查用户字典、不查 e2k，用户字典/e2k 由上游独立路径处理。
        """
        options: List[str] = []
        # 1. 库分析器
        try:
            results = self._analyzer.analyze(ch)
            for r in results:
                if r.reading and r.reading != ch and r.reading not in options:
                    options.append(r.reading)
        except Exception:
            pass
        # 2. pykakasi 参考读音
        if self._pykakasi_conv is not None:
            try:
                converted = self._pykakasi_conv.do(ch)
                if converted and converted != ch and converted not in options:
                    options.append(converted)
            except Exception:
                pass
        return options

    def _fallback_split_peel_kana(
        self, word: str, reading: str
    ) -> List[str]:
        """连词回退策略：从 reading 头尾剥离能匹配的自注音字符。

        当 ``_try_split_to_chars`` 失败时调用。算法：
        1. 每字查候选读音（假名=自身，汉字查字典，其他=自身）。
        2. 从 reading 尾部递归剥离：若末字候选中某读音匹配 reading 尾部 → 扣除。
        3. 从 reading 头部递归剥离：同理。
        4. 对剩余的中间块（reading + 字符），首字承载全部剩余 reading。

        返回长度为 ``len(word)`` 的 split_parts 列表。
        中间连续汉字区域由 ``apply_to_sentence`` 基于 check_count==0 自动连词。

        Example:
            _fallback_split_peel_kana("可愛い", "かわいい")
                → ["かわい", "", "い"]   # 可吃汉字段 + い 自注音
            _fallback_split_peel_kana("明日", "あした")
                → ["あした", ""]         # 纯汉字，首字全吃
            _fallback_split_peel_kana("食べ物", "たべもの")
                → ["た", "べ", "もの"]   # 头剥食(た) + 尾剥物(もの) + べ 自注音
        """
        n = len(word)
        if n <= 1 or not reading:
            return [reading] + [""] * (n - 1)

        # Step 1: 收集每字的候选自注音
        def char_candidates(ch: str) -> List[str]:
            ct = get_char_type(ch) if len(ch) == 1 else CharType.OTHER
            if ct == CharType.KANJI:
                return self._get_single_char_candidates(ch)
            # 非汉字（假名/符号/字母/数字等）→ 自身作为唯一候选
            return [ch]

        candidates: List[List[str]] = [char_candidates(c) for c in word]

        # 选择优先匹配的候选：优先使用与字符本身一致的（假名自匹配），
        # 否则取第一个能匹配的候选
        def try_match_suffix(opts: List[str], s: str) -> Optional[str]:
            for opt in opts:
                if opt and s.endswith(opt):
                    return opt
            return None

        def try_match_prefix(opts: List[str], s: str) -> Optional[str]:
            for opt in opts:
                if opt and s.startswith(opt):
                    return opt
            return None

        split_parts: List[str] = [""] * n
        remaining = reading
        left = 0
        right = n - 1

        # Step 2: 尾部剥离（尝试所有字符，非汉字必须按自身剥；汉字按候选匹配）
        while right > left:
            ch = word[right]
            ct = get_char_type(ch) if len(ch) == 1 else CharType.OTHER
            match = try_match_suffix(candidates[right], remaining)
            if match is None:
                # 非汉字无法剥 → 停止（假名/符号在 reading 里位置不对，放弃）
                # 汉字无法剥 → 也停止（候选不匹配）
                break
            split_parts[right] = match
            remaining = remaining[: len(remaining) - len(match)]
            right -= 1

        # Step 3: 头部剥离
        while left < right:
            ch = word[left]
            ct = get_char_type(ch) if len(ch) == 1 else CharType.OTHER
            match = try_match_prefix(candidates[left], remaining)
            if match is None:
                break
            split_parts[left] = match
            remaining = remaining[len(match) :]
            left += 1

        # Step 4: 处理头尾相遇的单字符情况
        if left == right:
            # 单字：全吃剩余 reading（若为假名且剩余匹配自身，也自然成立）
            split_parts[left] = remaining
            return split_parts

        # Step 5: 中间块 [left..right]，尝试对「纯汉字中间块」再次调用 _try_split_to_chars
        # 若成功则按字分配；失败则首字全吃，其余空（后续 apply_to_sentence 会连词）
        mid_word = word[left : right + 1]
        all_kanji = all(
            (get_char_type(c) if len(c) == 1 else CharType.OTHER) == CharType.KANJI
            for c in mid_word
        )
        if all_kanji and len(mid_word) > 1:
            sub_split = self._try_split_to_chars(mid_word, remaining)
            if sub_split is not None:
                for i, part in enumerate(sub_split):
                    split_parts[left + i] = part
                return split_parts

        # 首字吃全部剩余（保留原回退语义）
        split_parts[left] = remaining
        # 中间块里 left+1..right 保持 ""（由 apply_to_sentence 基于 check_count==0 连词）
        return split_parts

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

        # 批 17 #1: 英文词组 fallback — 未命中任何词典的英文词整块挂 ruby
        # 配合下游 check_counts 覆写实现「首字=1 cp、其他字母=0 cp」
        ruby_results, english_fallback_covered = self._apply_english_fallback(
            text, ruby_results, dict_covered, e2k_covered
        )

        # 记录每个块的来源（用于 #10 连词判定）
        block_source: Dict[int, str] = {}
        for block_id, result in enumerate(ruby_results):
            span = set(range(result.start_idx, result.end_idx))
            if span & dict_covered:
                block_source[block_id] = "dict"
            elif span & e2k_covered:
                block_source[block_id] = "e2k"
            elif span & english_fallback_covered:
                block_source[block_id] = "english_fallback"
            else:
                block_source[block_id] = "library"

        # 创建字符到注音的映射（按 mora 分割到每个字符）
        char_to_ruby_raw: Dict[int, str] = {}
        char_to_block: Dict[int, int] = {}
        for block_id, result in enumerate(ruby_results):
            block_len = result.end_idx - result.start_idx
            # "干净拆分"标记：用户词典 reading 用逗号干净拆成每字独立读音时
            # （每段非空 + 段数 == 字符数），不应强制连词，让每字能被独立使用。
            # 例：`大空 → おお,そら` → 大[おお] 空[そら] 各自独立。
            # 反例：`可愛い → かわい,,い`（中间空段）仍需连词承载 mora。
            is_clean_per_char_split = False
            # 词典条目可能用逗号分隔各字符的读音（如 "だい,ぼう,けん"）
            if "," in (result.reading or "") and block_len > 1:
                parts = [p.strip() for p in result.reading.split(",")]
                # 补齐不足的部分
                while len(parts) < block_len:
                    parts.append("")
                split_parts = parts[:block_len]
                # 劣质拆分检测：仅当「最末尾 part 为空且对应字符是汉字」时，
                # 视为字典条目错漏（尾部汉字无注音承载对象）。走 fallback 重算。
                # 中间空 part 视为用户显式的「首字/前字承载 mora」连词语义，尊重之。
                # 末尾空 part 对应假名属送り仮名模式，由后续首尾剥离处理。
                has_empty_tail_kanji = False
                for pos in range(block_len - 1, -1, -1):
                    if split_parts[pos]:
                        # 从尾往前遇到非空即停（只看真正的尾部空）
                        break
                    idx = result.start_idx + pos
                    if idx >= len(chars):
                        continue
                    ch = chars[idx]
                    ct = get_char_type(ch) if len(ch) == 1 else CharType.OTHER
                    if ct not in (CharType.HIRAGANA, CharType.KATAKANA):
                        has_empty_tail_kanji = True
                        break
                if has_empty_tail_kanji:
                    # 从字典 reading 中剥离逗号，用完整读音重算 peel_kana
                    full_reading = result.reading.replace(",", "")
                    split_parts = self._fallback_split_peel_kana(
                        result.text, full_reading
                    )
                    # 升级来源让 apply_to_sentence 允许连续汉字间连词
                    block_source[block_id] = "fallback"
                else:
                    # 干净拆分判定：所有 part 非空 + 原始段数 == block_len
                    # （补齐逻辑产生的尾部空段算不干净）
                    if (
                        len(parts) >= block_len
                        and all(p for p in split_parts)
                    ):
                        is_clean_per_char_split = True
            else:
                if block_len > 1:
                    # library 来源：跳过 _try_split_to_chars，直接走 fallback
                    # （_try_split_to_chars 成功率高但粒度粗到单字，会阻止同块连词；
                    # 走 fallback 让头尾假名剥离 + 中间汉字连词生效）
                    if block_source.get(block_id) == "library":
                        split_parts = self._fallback_split_peel_kana(
                            result.text, result.reading
                        )
                        # 升级来源让 apply_to_sentence 允许连续汉字间连词
                        block_source[block_id] = "fallback"
                    else:
                        # 非 library（dict/e2k/english_fallback 残留）：尝试按单字读音拆分，
                        # 不可拆分则走「头尾假名剥离」回退
                        char_split = self._try_split_to_chars(result.text, result.reading)
                        if char_split is not None:
                            split_parts = char_split
                        else:
                            # 连词回退：剥离头尾非汉字的自注音，保留假名 ruby，
                            # 中间连续汉字由 apply_to_sentence 基于 check_count==0 自动连词
                            split_parts = self._fallback_split_peel_kana(
                                result.text, result.reading
                            )
                            # 升级来源为 "fallback"，让 apply_to_sentence 允许连续汉字间连词
                            block_source[block_id] = "fallback"
                else:
                    split_parts = split_ruby_for_checkpoints(result.reading, block_len)
            for idx in range(result.start_idx, result.end_idx):
                if idx < len(chars):
                    pos = idx - result.start_idx
                    if pos < len(split_parts) and split_parts[pos]:
                        char_to_ruby_raw[idx] = split_parts[pos]
                    # 干净拆分（每段非空 + 段数==字符数）→ 每字独立，
                    # 不写 char_to_block，使 origin_block_id 保持 -1，
                    # 从而跳过 L1094-1100 的连词判定，允许单字独立使用。
                    # 例：大空=おお,そら → 大[おお]+空[そら] 独立；
                    # 大冒険=だい,ぼう,けん → 大/冒/険 各自独立。
                    if not is_clean_per_char_split:
                        char_to_block[idx] = block_id

        # 首尾假名剥离：若连词块的首/尾字符是假名（送り仮名/接头假名模式），
        # 将它们从 char_to_block 中移除，使其成为独立自注音字符，
        # 避免 linked_to_next 把送り仮名吸入连词块（如 "可愛い" 字典条目
        # reading="かわい,,い" 使 char_to_block 覆盖全 3 字，导致末尾 い 错误连词）。
        # 剥离条件：对应 split_parts[pos] 为空字符串 或 等于字符本身（即明确表示
        # "该字符由自身注音，不应作为连词成员"）。
        for block_id, result in enumerate(ruby_results):
            block_len = result.end_idx - result.start_idx
            if block_len < 2:
                continue
            # 从末尾向前剥离
            for pos in range(block_len - 1, 0, -1):
                idx = result.start_idx + pos
                if idx >= len(chars):
                    continue
                char = chars[idx]
                ct = get_char_type(char) if len(char) == 1 else CharType.OTHER
                if ct not in (CharType.HIRAGANA, CharType.KATAKANA):
                    break
                part = char_to_ruby_raw.get(idx, "")
                if part and part != char:
                    break
                # 剥离：移出 block，让后续自注音兜底
                char_to_block.pop(idx, None)
                char_to_ruby_raw.pop(idx, None)
            # 从首部向后剥离（保留至少 1 个字符在块中）
            for pos in range(0, block_len - 1):
                idx = result.start_idx + pos
                if idx >= len(chars):
                    continue
                # 已被末尾剥离阶段移出的不再处理
                if idx not in char_to_block:
                    continue
                char = chars[idx]
                ct = get_char_type(char) if len(char) == 1 else CharType.OTHER
                if ct not in (CharType.HIRAGANA, CharType.KATAKANA):
                    break
                part = char_to_ruby_raw.get(idx, "")
                if part and part != char:
                    break
                char_to_block.pop(idx, None)
                char_to_ruby_raw.pop(idx, None)

        # 未被分析器覆盖的字符使用自注音（保证所有字符都有 ruby）
        # #11：连词块内 split_parts 为空的字符（如 e2k "hello" 的 e/l/l/o 位置）
        # 已归属某个 block（char_to_block 中有记录），不应再 fallback 到自注音，
        # 否则会在导出中出现 {hello|ヘロー,e,l,l,o} 的多余字符残留。
        for idx, char in enumerate(chars):
            if idx in char_to_ruby_raw:
                continue
            if idx in char_to_block:
                # 属于某连词块但自身无拆分读音（连词：读音由首字承载）
                continue
            char_to_ruby_raw[idx] = char

        # 根据注音更新 check_count（汉字按 mora 数分配节奏点）
        for block_id, result in enumerate(ruby_results):
            # 批 17 #1: 英文词组 fallback 块——首字母=1 cp、其他字母=0 cp
            # 必须在 `result.text == result.reading` 短路之前处理
            # （fallback 的 text 与 reading 完全相同，否则会被跳过保留默认每字母=1）
            if block_source.get(block_id) == "english_fallback":
                for idx in range(result.start_idx, result.end_idx):
                    if idx < len(check_counts):
                        check_counts[idx] = 1 if idx == result.start_idx else 0
                continue
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
                    # 与 analyze_sentence 一致：library/fallback 来源走头尾假名剥离；
                    # 其他来源先尝试单字拆分再回退
                    src = block_source.get(block_id, "library")
                    if src in ("library", "fallback"):
                        split_parts = self._fallback_split_peel_kana(
                            result.text, result.reading
                        )
                    else:
                        char_split = self._try_split_to_chars(result.text, result.reading)
                        if char_split is not None:
                            split_parts = char_split
                        else:
                            split_parts = self._fallback_split_peel_kana(
                                result.text, result.reading
                            )
                else:
                    split_parts = split_ruby_for_checkpoints(result.reading, block_len)
            for idx in range(result.start_idx, result.end_idx):
                if idx < len(check_counts):
                    pos = idx - result.start_idx
                    if pos < len(split_parts) and split_parts[pos]:
                        check_counts[idx] = len(split_into_moras(split_parts[pos]))
                    else:
                        check_counts[idx] = 0

        # 单一平假名/片假名封顶：单个假名字符最多 1 cp（可以是 0）。
        # 场景：`ロミオ → Ro,me,o` 经 e2k 路径，split_parts 是英文音节，
        # 被 split_into_moras 按字符计数误拿到 2/2/1，应统一封顶为 1/1/1。
        # 汉字/英文字母不受限，允许按 mora 分配。
        for i, ch in enumerate(chars):
            if i >= len(check_counts):
                break
            if len(ch) == 1 and get_char_type(ch) in (
                CharType.HIRAGANA,
                CharType.KATAKANA,
            ):
                if check_counts[i] > 1:
                    check_counts[i] = 1

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

            # 空行（text.strip() 为空）不应被 check_line_start 强制打 CP
            if (
                self._flags.get("check_line_start", False)
                and check_counts
                and text.strip()
            ):
                check_counts[0] = max(check_counts[0], 1)

        # 标点符号：默认不参与节奏点；开关启用时强制 1
        _enable_punct_cp = (
            self._flags.get("checkpoint_on_punctuation", False) if self._flags else False
        )
        for i, ch in enumerate(chars):
            if i < len(check_counts) and ch in PUNCTUATION_SET:
                check_counts[i] = max(check_counts[i], 1) if _enable_punct_cp else 0

        # 构建结果
        results = []
        for i, (char, count) in enumerate(zip(chars, check_counts)):
            block_id = char_to_block.get(i, -1)
            source = block_source.get(block_id, "self")
            # 无注音块时 fallback 为 "self"（由后续 per-char 自注音补上）
            if block_id < 0:
                source = "self"
            results.append(
                AutoCheckResult(
                    line_idx=0,  # 将在 analyze_project 中设置
                    char_idx=i,
                    char=char,
                    check_count=count,
                    ruby=(
                        _group_reading_for_character(
                            char_to_ruby_raw[i],
                            check_counts[i] if i < len(check_counts) else 1,
                        )
                        if i in char_to_ruby_raw
                        else None
                    ),
                    origin_block_id=block_id,
                    origin_source=source,
                )
            )

        return results

    def _is_char_already_rubied(
        self, sentence: Sentence, idx: int
    ) -> bool:
        """判断指定位置的字符是否已被注音。

        规则：
        - char.ruby 非 None 视为已注音。
        - 若前一个字符 linked_to_next=True 且前一个字符已注音，则视为已注音（连词传递）。

        Args:
            sentence: 句子
            idx: 字符索引

        Returns:
            是否已注音
        """
        if idx < 0 or idx >= len(sentence.characters):
            return False
        char = sentence.characters[idx]
        if char.ruby is not None:
            return True
        if idx > 0:
            prev = sentence.characters[idx - 1]
            if prev.linked_to_next and self._is_char_already_rubied(sentence, idx - 1):
                return True
        return False

    def apply_to_sentence(
        self,
        sentence: Sentence,
        split_config: Optional[SplitConfig] = None,
        keep_existing_timetags: bool = True,
        only_noruby: bool = False,
    ) -> None:
        """分析并应用自动检查结果到句子

        构建新的 Character 对象列表，每个字符直接携带自己的 Ruby。
        相比旧的多字符 Ruby 合并方式更简洁。

        Args:
            sentence: 句子
            split_config: 拆分配置
            keep_existing_timetags: 是否保留现有时间标签
            only_noruby: 仅对未注音字符应用（已注音字符的 Ruby/check_count/linked_to_next 保留）
        """
        # only_noruby 模式：预先快照已注音字符的状态
        preserved: Dict[int, Tuple[Optional[Ruby], int, bool]] = {}
        if only_noruby:
            for i in range(len(sentence.characters)):
                if self._is_char_already_rubied(sentence, i):
                    c = sentence.characters[i]
                    preserved[i] = (c.ruby, c.check_count, c.linked_to_next)
            # 全部已注音 → 无事可做
            if len(preserved) == len(sentence.characters) and sentence.characters:
                return

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
        # 空行（text.strip() 为空）不应被 check_line_end/check_space_as_line_end 强制打句尾 CP
        _is_blank_line = not sentence.text.strip()
        add_line_end = self._flags.get("check_line_end", True) and not _is_blank_line
        check_space_as_line_end = (
            self._flags.get("check_space_as_line_end", True) and not _is_blank_line
        )
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
            # #11：ruby 为空、或与字符本身相同时不生成 Ruby 对象，
            # 避免 Ruby.__post_init__ 触发空文本异常，并避免导出残留 {a|a}。
            # Stage 0: result.ruby 为 List[str]（来自 _group_reading_for_character），
            # 映射为 Ruby(parts=[RubyPart(text=s), ...])。
            ruby_groups = result.ruby  # List[str] | None
            if ruby_groups and not (
                len(ruby_groups) == 1 and ruby_groups[0] == result.char
            ):
                ruby_obj = Ruby(parts=[RubyPart(text=g) for g in ruby_groups if g])
                if not ruby_obj.parts:
                    ruby_obj = None
            else:
                ruby_obj = None

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
        # #10: 仅当注音来源是「用户词典」或「e2k 英语词典」或「英文词组 fallback」
        #      时才允许连词；库函数（Sudachi/pykakasi）和自注音的结果一律不连词。
        # 补丁：library 来源走了「头尾假名剥离」回退（block_source="fallback"）时
        #      也允许连词（此时连续汉字间必须连词以保证 ruby 正确显示）。
        # 连词规则：同一 origin_block_id 内相邻字符自动 linked_to_next，
        # 不再要求 next.check_count == 0。后字继续展示自己的 ruby。
        # 空格字符不参与连词。
        _LINKABLE_SOURCES = {"dict", "e2k", "english_fallback", "fallback"}
        for i in range(len(new_characters) - 1):
            next_ch = new_characters[i + 1]
            if next_ch.char and next_ch.char.isspace():
                continue
            cur_ch = new_characters[i]
            if cur_ch.char and cur_ch.char.isspace():
                continue
            cur_src = results[i].origin_source if i < len(results) else "self"
            next_src = (
                results[i + 1].origin_source if i + 1 < len(results) else "self"
            )
            # 仅可连词来源且属于同一个注音块时，才建立连词关系
            if (
                cur_src in _LINKABLE_SOURCES
                and next_src in _LINKABLE_SOURCES
                and results[i].origin_block_id >= 0
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

        # only_noruby 模式：对已注音字符恢复原 Ruby/check_count/linked_to_next。
        # 注意：analyze 过程可能改变字符数量时（当前流程下不会），此覆盖按原位置对齐。
        if only_noruby and preserved:
            for i, (old_ruby, old_cc, old_link) in preserved.items():
                if i < len(sentence.characters):
                    sentence.characters[i].ruby = old_ruby
                    sentence.characters[i].check_count = old_cc
                    sentence.characters[i].linked_to_next = old_link

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
        only_noruby: bool = False,
    ) -> None:
        """分析并应用到整个项目

        Args:
            project: 项目
            split_config: 拆分配置
            keep_existing_timetags: 是否保留现有时间标签
            only_noruby: 仅对未注音字符应用
        """
        for sentence in project.sentences:
            self.apply_to_sentence(
                sentence, split_config, keep_existing_timetags, only_noruby
            )

        # check_count 变更后，自动顺延越界的选中 cp
        project.shift_selected_checkpoint_if_lost()

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
        # 规则：汉字的 cp 严格由它自己的 ruby parts 决定
        #   - 无 ruby → cp=0（典型场景：连词块内后字，mora 已压在首字上）
        #   - 有 ruby 且非自注音 → 按 parts 的 mora 总数
        #   - 自注音（ruby==char）→ 保留默认 cp（走下游过滤规则）
        # 非汉字（假名/字母/符号）：保留默认，由下游过滤规则处理。
        for i, char in enumerate(sentence.characters):
            if len(char.char) != 1 or get_char_type(char.char) != CharType.KANJI:
                continue  # 只对汉字按 ruby 重算
            if not char.ruby:
                # 空 ruby 的汉字：cp 必须为 0（连词块内后字不打拍）
                check_counts[i] = 0
                continue
            ruby_groups = [p.text for p in char.ruby.parts]
            if len(ruby_groups) == 1 and char.char == ruby_groups[0]:
                continue  # 自注音汉字（罕见），保留默认
            check_counts[i] = sum(len(split_into_moras(group)) for group in ruby_groups)

        # 单一平假名/片假名封顶：最多 1 cp（同 analyze_sentence）
        chars_for_cap = [c.char for c in sentence.characters]
        for i, ch in enumerate(chars_for_cap):
            if i >= len(check_counts):
                break
            if len(ch) == 1 and get_char_type(ch) in (
                CharType.HIRAGANA,
                CharType.KATAKANA,
            ):
                if check_counts[i] > 1:
                    check_counts[i] = 1

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

            # 空行（text.strip() 为空）不应被 check_line_start 强制打 CP
            if (
                self._flags.get("check_line_start", False)
                and check_counts
                and sentence.text.strip()
            ):
                check_counts[0] = max(check_counts[0], 1)

        # 标点符号：默认不参与节奏点；开关启用时强制 1
        _enable_punct_cp2 = (
            self._flags.get("checkpoint_on_punctuation", False) if self._flags else False
        )
        for i, ch in enumerate(chars):
            if i < len(check_counts) and ch in PUNCTUATION_SET:
                check_counts[i] = max(check_counts[i], 1) if _enable_punct_cp2 else 0

        # 批 18 #9：英文词组节奏点规则（首字=1，其余=0，末字母标句尾）
        # find_english_words 基于 sentence.text 的字符索引，与 sentence.characters 一一对应
        # （文本拆分器对英文走逐字符路径，保持字符-文本索引对齐）。
        english_sentence_end_idx: set[int] = set()
        for start, end, word in find_english_words(sentence.text):
            if end - start <= 1:
                continue  # 单字母词：无词组概念
            for idx in range(start, end):
                if idx < len(check_counts):
                    check_counts[idx] = 1 if idx == start else 0
            if end - 1 < len(sentence.characters):
                english_sentence_end_idx.add(end - 1)

        # 更新字符属性
        # 空行（text.strip() 为空）不应被 check_line_end/check_space_as_line_end 强制打句尾 CP
        _is_blank_line = not sentence.text.strip()
        add_line_end = self._flags.get("check_line_end", True) and not _is_blank_line
        check_space_as_line_end = (
            self._flags.get("check_space_as_line_end", True) and not _is_blank_line
        )
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
            if i in english_sentence_end_idx:
                is_sentence_end = True
            char.check_count = check_counts[i]
            char.is_line_end = is_last and add_line_end
            char.is_sentence_end = is_sentence_end
            if not char.is_sentence_end:
                char.clear_sentence_end_ts()

        # #10: 此函数仅更新节奏点，不改变 linked_to_next。
        # linked_to_next 已由 analyze_sentence/apply_to_sentence 根据注音来源
        # （用户词典/e2k/库函数）正确设置，不应被此函数覆盖。
        # （历史：曾有 "next_ch.check_count != 0 时断开 linked" 的清理逻辑，
        #  但新规则允许"连词不强制后字 cc==0；后字继续展示自己的 ruby"，
        #  该清理会错误断开合法连词 [可,愛]→[い] 此类链，已移除。）

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
