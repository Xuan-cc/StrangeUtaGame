"""注音分析器 - 为日文文本提供假名注音。

主引擎为 WinRT IME（Windows.Globalization.JapanesePhoneticAnalyzer，上下文
感知复合词分析）；不可用时降级 pykakasi（单字分析），最后 DummyAnalyzer。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

from strange_uta_game.backend.domain import Ruby, RubyPart, Sentence
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    split_ruby_for_checkpoints,
)


@dataclass
class RubyResult:
    """注音分析结果"""

    text: str  # 原始字符
    reading: str  # 注音（假名）
    start_idx: int  # 起始索引
    end_idx: int  # 结束索引
    # 所在 morpheme 的全局 span（start, end）—— 分析器一次返回的同一 (surface, reading)
    # 对里出来的所有 RubyResult 共享同一 morpheme_span。专供下游 Phase 5 用户词典
    # 「连词组保护」判定，避免单字词条把多字 morpheme 打散（如 日→にち 污染 ある日）。
    # 单字 morpheme（surface 长度 1）该字段为 (start_idx, end_idx)。
    morpheme_span: Optional[Tuple[int, int]] = None


def is_all_katakana(text: str) -> bool:
    """text 非空且全部为片假名（含长音 ー / 中点 ・ / 促音 ッ，码位 0x30A0–0x30FF）。"""
    if not text:
        return False
    return all(0x30A0 <= ord(c) <= 0x30FF for c in text)


def _arabic_to_kanji(num_str: str) -> str:
    """将阿拉伯数字字符串转换为漢数字（漢字表記）。

    供注音分析器将数字序列转为漢字后获取日语读音。如 ``"999"`` → ``"九百九十九"``。

    Examples:
        "0"    → "零"
        "10"   → "十"
        "100"  → "百"
        "999"  → "九百九十九"
        "2024" → "二千二十四"
        "10000" → "一万"
    """
    n = int(num_str)
    if n == 0:
        return "零"

    _kanji_digits = "零一二三四五六七八九"
    _units = [
        (10**12, "兆"),
        (10**8, "億"),
        (10**4, "万"),
        (1000, "千"),
        (100, "百"),
        (10, "十"),
        (1, ""),
    ]

    result: List[str] = []
    remaining = n

    for unit_val, unit_name in _units:
        if remaining >= unit_val:
            count = remaining // unit_val
            if count > 1 or unit_val >= 10000 or unit_val == 1:
                if count < 10:
                    result.append(_kanji_digits[count])
                else:
                    result.append(_arabic_to_kanji(str(count)))
            if unit_name:
                result.append(unit_name)
            remaining %= unit_val

    return "".join(result)


def _arabic_to_kanji_segments(
    num_str: str,
) -> Tuple[str, List[Tuple[int, int, int]]]:
    """将阿拉伯数字字符串转换为漢数字，同时返回每位的汉字片段边界。

    与 :func:`_arabic_to_kanji` 逻辑一致，额外记录每个非零数字位
    产出的汉字片段在结果串中的 ``(start, end, orig_digit_idx)``。

    Returns:
        (kanji_string, segments)
        segments 每项为 (kanji_start, kanji_end, orig_digit_idx)，
        其中 orig_digit_idx 是对应原数字字符串中的字符索引。
        零位（如 ``"105"`` 中间的 0）不产生片段。

    Examples:
        >>> _arabic_to_kanji_segments("12345")
        ("一万二千三百四十五", [(0,2,0), (2,4,1), (4,6,2), (6,8,3), (8,9,4)])
        >>> _arabic_to_kanji_segments("105")
        ("一百五", [(0,2,0), (2,3,2)])  # 索引 1 的 0 无片段
    """
    import math

    n = int(num_str)
    if n == 0:
        return "零", [(0, 1, 0)]

    _kanji_digits = "零一二三四五六七八九"
    _units = [
        (10**12, "兆"),
        (10**8, "億"),
        (10**4, "万"),
        (1000, "千"),
        (100, "百"),
        (10, "十"),
        (1, ""),
    ]

    num_digits = len(num_str)
    result: List[str] = []
    segments: List[Tuple[int, int, int]] = []
    remaining = n
    char_pos = 0  # 当前汉字字符位置（非 list 元素索引）

    for unit_val, unit_name in _units:
        if remaining >= unit_val:
            count = remaining // unit_val
            count_digits = len(str(count))
            # 计算该组最高位对应的原始数字索引
            if unit_val >= 10000:
                exp = int(math.log10(unit_val))
            elif unit_val > 1:
                exp = int(math.log10(unit_val))
            else:
                exp = 0
            group_orig_idx = num_digits - exp - count_digits

            seg_start = char_pos

            if count < 10:
                # 单数字：直接添加
                if count > 1 or unit_val >= 10000 or unit_val == 1:
                    digit_kanji = _kanji_digits[count]
                    result.append(digit_kanji)
                    char_pos += len(digit_kanji)
                if unit_name:
                    result.append(unit_name)
                    char_pos += len(unit_name)
                segments.append((seg_start, char_pos, group_orig_idx))
            else:
                # 多数字：递归拆分，组内每位映射到独立的原始数字索引
                sub_kanji, sub_segments = _arabic_to_kanji_segments(
                    str(count)
                )
                result.append(sub_kanji)
                char_pos += len(sub_kanji)
                unit_len = len(unit_name)
                if unit_name:
                    result.append(unit_name)
                    char_pos += unit_len
                for i, (sub_start, sub_end, sub_orig_idx) in enumerate(
                    sub_segments
                ):
                    mapped_idx = group_orig_idx + sub_orig_idx
                    # 末段延展到包含单位汉字（如 九万 而非仅 九）
                    is_last = i == len(sub_segments) - 1
                    adj_end = sub_end + (unit_len if is_last else 0)
                    segments.append(
                        (seg_start + sub_start, seg_start + adj_end, mapped_idx)
                    )
            remaining %= unit_val

    return "".join(result), segments


def is_english_reading(reading: str) -> bool:
    """reading 是英文读音（ASCII 字母，可含空格 / ' / -），且至少含一个字母。

    用于识别「片假名外来语 → 英文标注」场景（如 ギター→guitar）。
    """
    r = (reading or "").strip()
    if not r:
        return False
    if not all(c.isascii() and (c.isalpha() or c in " '-") for c in r):
        return False
    return any(c.isalpha() for c in r)


class RubyAnalyzer(ABC):
    """注音分析器抽象基类"""

    @abstractmethod
    def analyze(self, text: str) -> List[RubyResult]:
        """分析文本并返回注音结果"""
        pass

    @abstractmethod
    def get_reading(self, text: str) -> str:
        """获取文本的完整读音"""
        pass


# ──────────────────────────────────────────────
# 假名分配基类（分词器无关，Sudachi / WinRT 共用）
# ──────────────────────────────────────────────


class KanaDistributingAnalyzer(RubyAnalyzer):
    """把 (surface, 平假名读音) 序列分配为逐字/逐块注音的共享基类。

    不依赖任何具体分词器；子类只需产出 (surface, reading) 序列并复用
    :meth:`_results_from_pairs`，保证下游 block 分组逻辑完全一致。

    对于含漢字的形態素：
    1. 先用假名字符作为锚点分配读音（如 迷い → 迷{まよ}い）
    2. 对纯漢字块，尝试用 pykakasi 的单字读音作参考进行分配
       （如 世界{せかい} → 世{せ}界{かい}）
    3. 分配失败时保持复合词读音不拆分（如 今日{きょう}）

    数字处理：analyze() 会在分析前将阿拉伯数字序列转为漢数字
    （如 ``"20日"`` → ``"二十日"``），使分析器能产出正确读音
    （``"はつか"`` 而非 ``"にじゅうにち"``）。分析后映射回原始位置。
    """

    def analyze(self, text: str) -> List[RubyResult]:
        """分析文本，对阿拉伯数字序列做漢数字前置转换。

        子类无需重写本办法，只需实现 :meth:`_get_pairs`。
        """
        if not text:
            return []
        try:
            modified_text, replacements = _replace_digits_with_kanji(text)
            pairs = self._get_pairs(modified_text)
            results = self._results_from_pairs(pairs)
            if replacements:
                results = _map_results_to_original(results, replacements, text)
            return results
        except Exception:
            return [
                RubyResult(text=c, reading=c, start_idx=i, end_idx=i + 1)
                for i, c in enumerate(text)
            ]

    def _results_from_pairs(
        self, pairs: List[Tuple[str, str]]
    ) -> List[RubyResult]:
        """将 (surface, 平假名读音) 序列分配为逐块 RubyResult。

        供各分词器实现（如 WinRTAnalyzer）共用，保证下游 block 分组逻辑一致。
        """
        results: List[RubyResult] = []
        pos = 0

        for surface, reading in pairs:
            start = pos
            end = pos + len(surface)
            # 同一 pair 派生出的所有 RubyResult 共享同一 morpheme_span；
            # 即便 distribute 拆成多个 block 也保留 pair 边界，下游 Phase 5 用此判定
            # 连词组完整性（如 ある日 整词读音 あるひ 被拆成 あ/る/日 三段，但 morpheme
            # 仍是 (0,3)，使单字词条 日→にち 不能覆盖其中的 日）。
            morpheme_span = (start, end) if end - start > 1 else None

            has_kanji = any(self._is_kanji(c) for c in surface)

            if not has_kanji or not reading or surface == reading:
                # 纯假名/符号/无読音: 逐字处理，片假名转平假名
                for i, c in enumerate(surface):
                    results.append(
                        RubyResult(
                            text=c,
                            reading=self._kata_to_hira(c),
                            start_idx=start + i,
                            end_idx=start + i + 1,
                            morpheme_span=morpheme_span,
                        )
                    )
            else:
                # 含漢字：分配读音
                distributed = self._distribute_morpheme_reading(surface, reading)
                char_offset = 0
                for block_text, block_reading in distributed:
                    block_start = start + char_offset
                    block_end = block_start + len(block_text)
                    results.append(
                        RubyResult(
                            text=block_text,
                            reading=block_reading,
                            start_idx=block_start,
                            end_idx=block_end,
                            morpheme_span=morpheme_span,
                        )
                    )
                    char_offset += len(block_text)

            pos = end

        return results

    # ── 读音分配 ──

    def _distribute_morpheme_reading(
        self, surface: str, reading: str
    ) -> List[Tuple[str, str]]:
        """将形態素的读音分配到各个字符。

        利用假名字符作为锚点切分读音，纯漢字块再尝试单字分配。
        """
        # 将 surface 切成连续的漢字段和非漢字段
        segments: List[Tuple[str, bool]] = []
        i = 0
        while i < len(surface):
            if self._is_kanji(surface[i]):
                j = i + 1
                while j < len(surface) and self._is_kanji(surface[j]):
                    j += 1
                segments.append((surface[i:j], True))
                i = j
            else:
                j = i + 1
                while j < len(surface) and not self._is_kanji(surface[j]):
                    j += 1
                segments.append((surface[i:j], False))
                i = j

        matched = self._match_segments(segments, reading, 0, 0)
        if matched is None:
            # 匹配失败：整块返回
            return [(surface, reading)]
        return matched

    def _match_segments(
        self,
        segments: List[Tuple[str, bool]],
        reading: str,
        seg_idx: int,
        read_idx: int,
    ) -> Optional[List[Tuple[str, str]]]:
        """递归将 segments 与 reading 对齐。"""
        if seg_idx == len(segments):
            return [] if read_idx == len(reading) else None
        if read_idx > len(reading):
            return None

        seg_text, is_kanji = segments[seg_idx]

        if not is_kanji:
            # 非漢字段：转成平假名后字面匹配
            hira = self._kata_to_hira(seg_text)
            seg_len = len(hira)
            if reading[read_idx : read_idx + seg_len] == hira:
                rest = self._match_segments(
                    segments, reading, seg_idx + 1, read_idx + seg_len
                )
                if rest is not None:
                    per_char = [(c, c) for c in seg_text]
                    return per_char + rest
            return None

        # 漢字段：尝试不同长度
        remaining_literal = 0
        for s, k in segments[seg_idx + 1 :]:
            if not k:
                remaining_literal += len(self._kata_to_hira(s))

        min_len = len(seg_text)  # 每个漢字至少 1 假名
        max_len = len(reading) - read_idx - remaining_literal

        for try_len in range(min_len, max_len + 1):
            portion = reading[read_idx : read_idx + try_len]
            rest = self._match_segments(
                segments, reading, seg_idx + 1, read_idx + try_len
            )
            if rest is not None:
                # 多漢字段按 morpheme 整块返回（同一 RubyResult → 同一 block_id），
                # 由下游 auto_check 的 library→fallback 路径按库候选切分到单字。
                # 不在分析器内拆单字，避免同 morpheme 字符被打散到多个 block。
                return [(seg_text, portion)] + rest

        return None

    def _try_distribute_kanji_block(
        self, kanji_text: str, compound_reading: str
    ) -> Optional[List[Tuple[str, str]]]:
        """尝试将复合读音分配到各个漢字。

        使用 pykakasi 的单字读音作为参考：
        - 如果单字读音恰好是复合读音的前缀，则认为分配有效
        - 否则放弃参考约束进行无约束分配
        - 最终失败时放弃分配，保持整块
        """
        n = len(kanji_text)
        ref_readings: List[str] = []
        if self._pykakasi_conv:
            for k in kanji_text:
                try:
                    ref = self._pykakasi_conv.do(k)
                except Exception:
                    ref = ""
                ref_readings.append(ref)
        else:
            ref_readings = [""] * n

        # 第一轮：使用 pykakasi 参考约束
        result = self._partition_with_refs(
            kanji_text, compound_reading, ref_readings, 0, 0
        )
        if result is not None:
            return result

        # 第二轮：无约束分配（参考读音全部清空）
        empty_refs = [""] * n
        return self._partition_with_refs(kanji_text, compound_reading, empty_refs, 0, 0)

    def _partition_with_refs(
        self,
        kanji_text: str,
        reading: str,
        ref_readings: List[str],
        ki: int,
        ri: int,
    ) -> Optional[List[Tuple[str, str]]]:
        """递归分区：利用 pykakasi 参考读音约束搜索。

        三级匹配策略：
        1. 参考读音精确匹配
        2. 参考读音前缀匹配
        3. 无约束匹配（当参考读音不适用时，放宽限制）
        """
        if ki == len(kanji_text):
            return [] if ri == len(reading) else None
        if ri > len(reading):
            return None

        remaining_kanji = len(kanji_text) - ki
        remaining_chars = len(reading) - ri
        if remaining_chars < remaining_kanji:
            return None

        max_len = remaining_chars - (remaining_kanji - 1)
        ref = ref_readings[ki]

        tried: set = set()

        # 优先尝试参考读音精确匹配
        if ref:
            ref_len = len(ref)
            if ref_len <= max_len:
                portion = reading[ri : ri + ref_len]
                if portion == ref:
                    rest = self._partition_with_refs(
                        kanji_text, reading, ref_readings, ki + 1, ri + ref_len
                    )
                    if rest is not None:
                        return [(kanji_text[ki], portion)] + rest
                    tried.add(ref_len)

        # 其次尝试前缀匹配：分配部分是参考读音的前缀
        for try_len in range(1, max_len + 1):
            if try_len in tried:
                continue
            portion = reading[ri : ri + try_len]
            if ref and not ref.startswith(portion):
                continue  # 不符合参考约束
            rest = self._partition_with_refs(
                kanji_text, reading, ref_readings, ki + 1, ri + try_len
            )
            if rest is not None:
                return [(kanji_text[ki], portion)] + rest
            tried.add(try_len)

        # 最后无约束匹配：当参考读音不匹配时，尝试所有未试过的长度
        for try_len in range(1, max_len + 1):
            if try_len in tried:
                continue
            rest = self._partition_with_refs(
                kanji_text, reading, ref_readings, ki + 1, ri + try_len
            )
            if rest is not None:
                return [(kanji_text[ki], reading[ri : ri + try_len])] + rest

        return None

    # ── 工具方法 ──

    @staticmethod
    def _kata_to_hira(text: str) -> str:
        """片假名 → 平假名"""
        result = []
        for ch in text:
            code = ord(ch)
            if 0x30A1 <= code <= 0x30F6:
                result.append(chr(code - 0x60))
            else:
                result.append(ch)
        return "".join(result)

    @staticmethod
    def _is_kanji(char: str) -> bool:
        code = ord(char)
        return (
            (0x4E00 <= code <= 0x9FFF)
            or (0x3400 <= code <= 0x4DBF)
            or (0xF900 <= code <= 0xFAFF)
            or code == 0x3005  # 々 IDEOGRAPHIC ITERATION MARK
        )

    @staticmethod
    def _is_kana(char: str) -> bool:
        code = ord(char)
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)


# ──────────────────────────────────────────────
# WinRT IME 分析器（日语注音主引擎）
# ──────────────────────────────────────────────


class WinRTJapaneseUnavailable(ImportError):
    """WinRT 日语注音引擎不可用（通常缺少日语 IME 功能）。

    继承 ImportError 使 create_analyzer 的回退路径可统一捕获；
    ``reason`` 为机器可读原因，``guidance`` 为面向用户的安装引导文案。
    """

    def __init__(self, reason: str, guidance: str):
        self.reason = reason
        self.guidance = guidance
        super().__init__(f"WinRT Japanese engine unavailable ({reason})")


class WinRTAnalyzer(KanaDistributingAnalyzer):
    """基于 Windows.Globalization.JapanesePhoneticAnalyzer 的注音分析器。

    复用 KanaDistributingAnalyzer 的读音分配逻辑（_results_from_pairs /
    _distribute_morpheme_reading），仅把"分词 + 读音获取"换成 WinRT IME 接口。

    要点（来自调研结论）：
    - WinRT 默认粒度≈Sudachi Mode A，依赖上下文消歧 → 必须按整段输入。
    - GetWords 单次上限 100 字符，超长返回空 → 此处按 ≤100 字切块。
    - display_text 会半角→全角归一，但字符数 1:1 → surface 取原文切片、不用 display_text。
    - yomi_text 已是平假名。
    """

    _MAX_LEN = 100

    def __init__(self):
        available, reason = winrt_japanese_status()
        if not available:
            if reason == "no_winrt_package":
                raise ImportError(
                    "winrt-Windows.Globalization is required. Install with: "
                    "pip install winrt-Windows.Globalization"
                )
            # 引擎缺失（缺日语 IME 功能）或其他异常：抛带安装引导的错误，
            # 供 create_analyzer 优雅回退，调用方可捕获后向用户展示引导。
            raise WinRTJapaneseUnavailable(reason, winrt_install_guidance())

        from winrt._winrt import init_apartment, STA

        try:
            init_apartment(STA)
        except OSError:
            # 线程已初始化为某 apartment（如 PyQt 主线程已是 STA）→ 忽略
            pass
        from winrt.windows.globalization import JapanesePhoneticAnalyzer

        self._jpa = JapanesePhoneticAnalyzer
        # 预热：首次调用有冷启开销，启动时空跑一次
        try:
            self._jpa.get_words("予熱")
        except Exception:
            pass
        # pykakasi 用于单字读音参考查询
        self._pykakasi_conv = None
        try:
            import pykakasi

            kks = pykakasi.kakasi()
            kks.setMode("J", "H")
            self._pykakasi_conv = kks.getConverter()
        except ImportError:
            pass

    def _get_pairs(self, text: str) -> List[Tuple[str, str]]:
        """整段 → [(原文 surface, 平假名读音)]，按 ≤100 字切块。"""
        pairs: List[Tuple[str, str]] = []
        for off in range(0, len(text), self._MAX_LEN):
            chunk = text[off : off + self._MAX_LEN]
            words = self._jpa.get_words(chunk)
            cursor = 0
            for w in words:
                disp_len = len(w.display_text)
                # surface 取原文切片（display_text 已全角归一，不可信）
                surface = chunk[cursor : cursor + disp_len]
                reading = w.yomi_text or surface
                pairs.append((surface, reading))
                cursor += disp_len
            # 兜底：若 GetWords 返回空（超长或异常），逐字回退
            if cursor < len(chunk):
                for c in chunk[cursor:]:
                    pairs.append((c, c))
        return pairs

    def get_reading(self, text: str) -> str:
        if not text:
            return ""
        try:
            return "".join(r for _, r in self._get_pairs(text))
        except Exception:
            return text


# ──────────────────────────────────────────────
# Sudachi 分析器（noWinIME / mac 变体主引擎）
# ──────────────────────────────────────────────


class SudachiAnalyzer(KanaDistributingAnalyzer):
    """基于 sudachipy 的注音分析器，供不含 WinRT 的变体使用。

    字典优先级：sudachidict_small（打包体积最小）→ 默认。
    打包时只包含 sudachidict_small 以控制体积。
    分词粒度采用 Mode A（最短形态素，与 WinRT 默认粒度相近）。

    reading_form() 返回表层形读音（非字典形），可直接用于假名注音。
    """

    def __init__(self):
        try:
            import sudachipy  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "sudachipy is required. Install with: pip install sudachipy sudachidict_small"
            )
        # 字典优先级：small（体积小）→ 默认（首个已安装字典）
        _dict_obj = None
        for _dict_name in ("small", None):
            try:
                _dict_obj = (
                    sudachipy.Dictionary(dict=_dict_name).create()
                    if _dict_name
                    else sudachipy.Dictionary().create()
                )
                break
            except Exception:
                continue
        if _dict_obj is None:
            raise ImportError(
                "sudachi dictionary unavailable. "
                "Install with: pip install sudachidict_small"
            )
        self._tokenizer = _dict_obj
        self._split_mode = sudachipy.SplitMode.A

        self._pykakasi_conv = None
        try:
            import pykakasi  # type: ignore[import-untyped]

            kks = pykakasi.kakasi()
            kks.setMode("J", "H")
            self._pykakasi_conv = kks.getConverter()
        except ImportError:
            pass

    def _get_pairs(self, text: str) -> List[Tuple[str, str]]:
        morphemes = self._tokenizer.tokenize(text, self._split_mode)
        pairs: List[Tuple[str, str]] = []
        for m in morphemes:
            surface = m.surface()
            # reading_form() 返回表层读音（片假名），转平假名后使用
            # 对于 ASCII / OOV 词，reading_form() 可能返回小写原文，此时读音 = 原文
            reading = self._kata_to_hira(m.reading_form() or surface)
            pairs.append((surface, reading))
        return pairs

    def get_reading(self, text: str) -> str:
        if not text:
            return ""
        try:
            return "".join(r for _, r in self._get_pairs(text))
        except Exception:
            return text


# ──────────────────────────────────────────────
# pykakasi 分析器（回退用）
# ──────────────────────────────────────────────


class PykakasiAnalyzer(RubyAnalyzer):
    """基于 pykakasi 的注音分析器"""

    def __init__(self):
        """初始化 pykakasi 转换器"""
        try:
            import pykakasi

            self.kakasi = pykakasi.kakasi()
            self.kakasi.setMode("J", "H")  # 汉字 → 平假名
            self.conv = self.kakasi.getConverter()
        except ImportError:
            raise ImportError(
                "pykakasi is required. Install with: pip install pykakasi"
            )

    def get_reading(self, text: str) -> str:
        """获取文本的平假名读音"""
        if not text:
            return ""
        try:
            return self.conv.do(text)
        except Exception:
            return text

    def analyze(self, text: str) -> List[RubyResult]:
        """分析文本并返回注音结果"""
        if not text:
            return []

        results = []
        i = 0
        n = len(text)

        while i < n:
            char = text[i]

            if self._is_kanji(char):
                kanji_block = char
                j = i + 1
                while j < n and self._is_kanji(text[j]):
                    kanji_block += text[j]
                    j += 1

                reading = self.conv.do(kanji_block)
                results.append(
                    RubyResult(
                        text=kanji_block, reading=reading, start_idx=i, end_idx=j
                    )
                )
                i = j
            else:
                if self._is_kana(char):
                    # 片假名转平假名
                    reading = self._kata_to_hira(char)
                    results.append(
                        RubyResult(
                            text=char, reading=reading, start_idx=i, end_idx=i + 1
                        )
                    )
                else:
                    results.append(
                        RubyResult(text=char, reading=char, start_idx=i, end_idx=i + 1)
                    )
                i += 1

        return results

    @staticmethod
    def _is_kanji(char: str) -> bool:
        code = ord(char)
        return (
            (0x4E00 <= code <= 0x9FFF)
            or (0x3400 <= code <= 0x4DBF)
            or (0xF900 <= code <= 0xFAFF)
            or code == 0x3005  # 々 IDEOGRAPHIC ITERATION MARK
        )

    @staticmethod
    def _is_kana(char: str) -> bool:
        code = ord(char)
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)

    @staticmethod
    def _kata_to_hira(text: str) -> str:
        """片假名 → 平假名"""
        result = []
        for ch in text:
            code = ord(ch)
            if 0x30A1 <= code <= 0x30F6:
                result.append(chr(code - 0x60))
            else:
                result.append(ch)
        return "".join(result)


class DummyAnalyzer(RubyAnalyzer):
    """虚拟注音分析器（用于测试）"""

    def analyze(self, text: str) -> List[RubyResult]:
        return [
            RubyResult(text=char, reading=char, start_idx=i, end_idx=i + 1)
            for i, char in enumerate(text)
        ]

    def get_reading(self, text: str) -> str:
        return text


# ──────────────────────────────────────────────
# 中文拼音分析器
# ──────────────────────────────────────────────


class PinyinAnalyzer(RubyAnalyzer):
    """基于 pypinyin + jieba 的中文拼音注音分析器。

    使用 jieba 分词后调用 pypinyin，利用词组上下文提升多音字准确度。
    输出带声调符号的拼音（如 ``zhōng``、``hǎo``）。
    """

    def __init__(self):
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message=".*pkg_resources.*", category=UserWarning
                )
                import jieba
            import logging

            jieba.setLogLevel(logging.WARNING)
        except ImportError as e:
            raise ImportError(
                "jieba is required for pinyin analysis. "
                "Install with: pip install jieba"
            ) from e
        try:
            from pypinyin import Style, pinyin
        except ImportError as e:
            raise ImportError(
                "pypinyin is required for pinyin analysis. "
                "Install with: pip install pypinyin"
            ) from e
        self._jieba = jieba
        self._pinyin = pinyin
        self._style = Style.TONE
        # 歌曲常用口语读音覆盖（仅对口语中几乎从不读文读音的字做全局覆盖）
        from pypinyin import load_single_dict
        load_single_dict({
            ord('谁'): 'shéi',   # 流行歌曲中几乎不用 shuí
            ord('熟'): 'shóu',   # 口语/歌词里读 shóu 而非 shú
            ord('血'): 'xiě',    # 口语/歌词里多读 xiě
        })

    def analyze(self, text: str) -> List[RubyResult]:
        if not text:
            return []

        chars = list(text)
        words = list(self._jieba.cut(text))
        py_result = self._pinyin(words, style=self._style)

        results: List[RubyResult] = []
        for i, ch in enumerate(chars):
            if i < len(py_result) and py_result[i]:
                reading = py_result[i][0]
            else:
                reading = ch
            results.append(
                RubyResult(
                    text=ch,
                    reading=reading if self._is_chinese_char(ch) else ch,
                    start_idx=i,
                    end_idx=i + 1,
                )
            )

        return results

    def get_reading(self, text: str) -> str:
        if not text:
            return ""
        results = self.analyze(text)
        return " ".join(r.reading for r in results)

    @staticmethod
    def _is_chinese_char(char: str) -> bool:
        code = ord(char)
        return (
            (0x4E00 <= code <= 0x9FFF)
            or (0x3400 <= code <= 0x4DBF)
            or (0xF900 <= code <= 0xFAFF)
            or code == 0x3005
        )


def create_pinyin_analyzer() -> PinyinAnalyzer:
    """创建中文拼音注音分析器。"""
    return PinyinAnalyzer()


# ──────────────────────────────────────────────
# WinRT 日语注音引擎：可用性探测 + 安装引导
# ──────────────────────────────────────────────

# 日语「Basic」语言功能（含微软日语 IME），JapanesePhoneticAnalyzer 的注音引擎来源
WINRT_JA_CAPABILITY = "Language.Basic~~~ja-JP~0.0.1.0"


def winrt_japanese_status() -> Tuple[bool, str]:
    """探测 WinRT 日语注音引擎是否可用。

    返回 (available, reason)。reason 取值：
      - "ok"                  引擎可用
      - "no_winrt_package"    未安装 winrt-Windows.Globalization
      - "engine_unavailable"  缺少日语 IME 功能（GetWords 返回空/无假名）
      - "error:<类型>"        其他异常

    探测方式：对确定含汉字读音的 "日本語" 调 GetWords，引擎缺失时会返回空
    或读音等于原文（无假名），据此判定。
    """
    try:
        from winrt._winrt import init_apartment, STA  # type: ignore
    except ImportError:
        return (False, "no_winrt_package")
    try:
        try:
            init_apartment(STA)
        except OSError:
            pass  # 线程已初始化为某 apartment
        from winrt.windows.globalization import JapanesePhoneticAnalyzer  # type: ignore

        words = JapanesePhoneticAnalyzer.get_words("日本語")
        reading = "".join(w.yomi_text or "" for w in words)
        has_kana = any("぀" <= c <= "ヿ" for c in reading)
        if words and reading and reading != "日本語" and has_kana:
            return (True, "ok")
        return (False, "engine_unavailable")
    except Exception as e:  # noqa: BLE001
        return (False, f"error:{type(e).__name__}")


def winrt_install_guidance() -> str:
    """缺少日语 IME 功能时的安装引导文案（面向用户）。"""
    return (
        "WinRT 日语注音需要 Windows 的日语功能（含日语 IME）。当前系统未安装。\n"
        "\n"
        "方式一（命令行，需管理员）：以管理员身份运行 PowerShell，执行\n"
        f"    Add-WindowsCapability -Online -Name {WINRT_JA_CAPABILITY}\n"
        "需联网，约几十 MB，从 Windows Update 下载（非完整语言包）。\n"
        "\n"
        "方式二（图形界面）：设置 → 时间和语言 → 语言和区域 → 添加语言 →\n"
        "搜索「日本語」→ 安装（勾选「基本键入/Basic typing」即可）。\n"
        "\n"
        "安装后无需把日语设为显示语言，也无需加入语言列表，重启应用即可生效。"
    )


def install_winrt_japanese(timeout: int = 600) -> Tuple[bool, str]:
    """通过 UAC 提权安装日语 IME 功能（Add-WindowsCapability）。

    用 ``Start-Process -Verb RunAs`` 触发 UAC 弹窗提权运行 PowerShell；
    用户拒绝提权或安装失败时返回 (False, 原因)，调用方应转为展示
    :func:`winrt_install_guidance` 引导用户手动安装。

    注意：本函数会弹出 UAC，**调用前应先向用户说明用途并征得同意**。

    返回 (success, message)。
    """
    import subprocess

    # 子进程以管理员身份执行安装并按结果设置退出码；-Verb RunAs 触发 UAC。
    inner = (
        f"$ErrorActionPreference='Stop';"
        f"try{{Add-WindowsCapability -Online -Name {WINRT_JA_CAPABILITY};exit 0}}"
        f"catch{{exit 2}}"
    )
    launcher = (
        "$p=Start-Process powershell "
        "-ArgumentList '-NoProfile','-NonInteractive','-Command',"
        f"'{inner}' -Verb RunAs -Wait -PassThru;"
        "exit $p.ExitCode"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", launcher],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return (False, "powershell_not_found")
    except subprocess.TimeoutExpired:
        return (False, "timeout")

    if proc.returncode == 0:
        # 安装命令成功；复测引擎确认可用
        ok, reason = winrt_japanese_status()
        return (ok, "ok" if ok else f"installed_but_{reason}")
    # 1603/RunAs 取消等：UAC 被拒或安装失败
    if "拒绝" in (proc.stderr or "") or proc.returncode in (1223, -1):
        return (False, "uac_declined")
    return (False, f"install_failed:{proc.returncode}")


def create_analyzer(use_pykakasi: bool = True) -> RubyAnalyzer:
    """创建注音分析器。

    回退链：WinRTAnalyzer → SudachiAnalyzer → PykakasiAnalyzer → DummyAnalyzer。

    - main 变体：WinRT 可用则直接使用；缺日语 IME 由 UI 引导安装后再用。
    - noWinIME / mac 变体：winrt 包不存在，直接跳至 Sudachi。
    """
    try:
        return WinRTAnalyzer()
    except WinRTJapaneseUnavailable:
        # 缺日语 IME 引擎：UI 层引导安装；此处先降级
        pass
    except ImportError:
        # 缺 winrt 包（noWinIME / mac 变体）：直接降级
        pass

    try:
        return SudachiAnalyzer()
    except ImportError:
        pass

    if use_pykakasi:
        try:
            return PykakasiAnalyzer()
        except ImportError:
            pass

    print("Warning: all analyzers unavailable, using DummyAnalyzer")
    return DummyAnalyzer()


def _replace_digits_with_kanji(text: str) -> Tuple[str, List[Tuple[int, int, int, int]]]:
    """将文本中的阿拉伯数字序列替换为漢数字表記。

    供 :meth:`KanaDistributingAnalyzer.analyze` 前置处理，
    使分析器在完整日文上下文中处理数字（如 ``"20日"`` → ``"二十日"``
    可让分析器产出 ``"はつか"`` 而非 ``"にじゅうにち"``）。

    Returns:
        (modified_text, replacements)
        replacements 每项为 (orig_start, orig_end, kanji_start, kanji_end)
    """
    import re

    replacements: List[Tuple[int, int, int, int]] = []
    result: List[str] = []
    orig_cursor = 0
    kanji_cursor = 0

    for m in re.finditer(r"\d+", text):
        prefix = text[orig_cursor : m.start()]
        result.append(prefix)
        kanji_cursor += len(prefix)

        kanji = _arabic_to_kanji(m.group())
        kanji_len = len(kanji)
        replacements.append((m.start(), m.end(), kanji_cursor, kanji_cursor + kanji_len))

        result.append(kanji)
        kanji_cursor += kanji_len
        orig_cursor = m.end()

    result.append(text[orig_cursor:])
    return "".join(result), replacements


def _map_results_to_original(
    results: List[RubyResult],
    replacements: List[Tuple[int, int, int, int]],
    original_text: str,
) -> List[RubyResult]:
    """将分析器在漢数字文本上的结果映射回原始阿拉伯数字文本。

    - 落入替换区间内的结果合并为单个结果，span 覆盖原始数字区间
    - 其余结果的索引按累计偏移量调整
    """
    if not replacements:
        return results

    # 按替换区间归组
    replacement_groups: List[List[RubyResult]] = [[] for _ in replacements]
    other_results: List[RubyResult] = []

    for r in results:
        placed = False
        for i, (_os, _oe, kanji_s, kanji_e) in enumerate(replacements):
            if r.start_idx >= kanji_s and r.end_idx <= kanji_e:
                replacement_groups[i].append(r)
                placed = True
                break
        if not placed:
            other_results.append(r)

    new_results: List[RubyResult] = []

    # 合并每个替换区间内的结果为单个结果
    for i, (orig_s, orig_e, _ks, _ke) in enumerate(replacements):
        group = replacement_groups[i]
        if group:
            merged_reading = "".join(r.reading for r in group)
            new_results.append(
                RubyResult(
                    text=original_text[orig_s:orig_e],
                    reading=merged_reading,
                    start_idx=orig_s,
                    end_idx=orig_e,
                )
            )

    # 调整落在替换区间外的结果的索引
    for r in other_results:
        adj_start = r.start_idx
        adj_end = r.end_idx
        for orig_s, orig_e, kanji_s, kanji_e in replacements:
            offset = (kanji_e - kanji_s) - (orig_e - orig_s)
            if kanji_e <= adj_start:
                adj_start -= offset
                adj_end -= offset
        new_results.append(
            RubyResult(
                text=r.text,
                reading=r.reading,
                start_idx=adj_start,
                end_idx=adj_end,
                morpheme_span=r.morpheme_span,
            )
        )

    new_results.sort(key=lambda x: x.start_idx)
    return new_results


def _group_reading_for_character(reading: str, checkpoint_count: int) -> List[str]:
    """按字符 checkpoint 数量拆分读音为分段列表。

    入参: reading 读音串; checkpoint_count 节奏点数量。
    出参: 长度为 checkpoint_count 的分段列表（或单段列表）。

    #1: 纯 ASCII 英文 reading 不参与 mora 切分，整体作为一个 part。
    """
    if not reading:
        return []
    # 英文 reading：整体一个 part，不按 mora 切
    if all(c.isascii() and c.isalpha() for c in reading):
        return [reading]
    if checkpoint_count <= 1:
        return [reading]
    return split_ruby_for_checkpoints(reading, checkpoint_count)


def analyze_sentence_ruby(
    sentence: Sentence,
    analyzer: Optional[RubyAnalyzer] = None,
) -> None:
    """重新分析句子的 Ruby，并按 checkpoint 生成分组。"""
    analyzer = analyzer or create_analyzer()

    for char in sentence.characters:
        char.set_ruby(None)

    results = analyzer.analyze(sentence.text)

    for result in results:
        block_len = result.end_idx - result.start_idx
        if block_len <= 0:
            continue

        if block_len == 1:
            split_parts = [result.reading]
        else:
            split_parts = split_ruby_for_checkpoints(result.reading, block_len)

        for offset in range(block_len):
            char_idx = result.start_idx + offset
            if char_idx >= len(sentence.characters):
                break

            part = split_parts[offset] if offset < len(split_parts) else ""
            if not part:
                continue

            character = sentence.characters[char_idx]
            grouped_parts = _group_reading_for_character(part, character.check_count)
            if grouped_parts and "".join(grouped_parts) != character.char:
                character.set_ruby(Ruby(parts=[RubyPart(text=p) for p in grouped_parts if p]))
