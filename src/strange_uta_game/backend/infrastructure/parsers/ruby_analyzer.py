"""注音分析器 - 为日文文本提供假名注音。

优先使用 SudachiPy（上下文感知复合词分析），回退到 pykakasi（单字分析）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class RubyResult:
    """注音分析结果"""

    text: str  # 原始字符
    reading: str  # 注音（假名）
    start_idx: int  # 起始索引
    end_idx: int  # 结束索引


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
# SudachiPy 上下文感知分析器
# ──────────────────────────────────────────────


class SudachiAnalyzer(RubyAnalyzer):
    """基于 SudachiPy 的上下文感知注音分析器。

    使用 SudachiPy Mode C（最长分割）获取复合词的正确读音，
    例如 迷い→まよい 而非 めい+い、世界→せかい 而非 せい+かい。

    对于含漢字的形態素：
    1. 先用假名字符作为锚点分配读音（如 迷い → 迷{まよ}い）
    2. 对纯漢字块，尝试用 pykakasi 的单字读音作参考进行分配
       （如 世界{せかい} → 世{せ}界{かい}）
    3. 分配失败时保持复合词读音不拆分（如 今日{きょう}）
    """

    def __init__(self):
        try:
            from sudachipy import Dictionary

            self._tokenizer = Dictionary().create()
            # 兼容不同版本的 SplitMode
            try:
                from sudachipy import SplitMode

                self._mode = SplitMode.C
            except ImportError:
                from sudachipy import Tokenizer as _Tok

                self._mode = _Tok.SplitMode.C
        except ImportError:
            raise ImportError(
                "sudachipy is required. Install with: "
                "pip install sudachipy sudachidict_core"
            )
        # pykakasi 用于单字读音参考查询
        self._pykakasi_conv = None
        try:
            import pykakasi

            kks = pykakasi.kakasi()
            kks.setMode("J", "H")
            self._pykakasi_conv = kks.getConverter()
        except ImportError:
            pass

    def get_reading(self, text: str) -> str:
        """获取文本的平假名读音"""
        if not text:
            return ""
        try:
            morphemes = self._tokenizer.tokenize(text, self._mode)
            return "".join(self._kata_to_hira(m.reading_form()) for m in morphemes)
        except Exception:
            return text

    def analyze(self, text: str) -> List[RubyResult]:
        """分析文本并返回注音结果"""
        if not text:
            return []

        try:
            morphemes = self._tokenizer.tokenize(text, self._mode)
        except Exception:
            return [
                RubyResult(text=c, reading=c, start_idx=i, end_idx=i + 1)
                for i, c in enumerate(text)
            ]

        results: List[RubyResult] = []
        pos = 0

        for m in morphemes:
            surface = m.surface()
            reading_kata = m.reading_form()
            reading = self._kata_to_hira(reading_kata)
            start = pos
            end = pos + len(surface)

            has_kanji = any(self._is_kanji(c) for c in surface)

            if not has_kanji or not reading or surface == reading:
                # 纯假名/符号/无读音: 逐字 identity
                for i, c in enumerate(surface):
                    results.append(
                        RubyResult(
                            text=c,
                            reading=c,
                            start_idx=start + i,
                            end_idx=start + i + 1,
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
                if len(seg_text) == 1:
                    return [(seg_text, portion)] + rest

                # 多漢字：尝试按单字分配
                per_kanji = self._try_distribute_kanji_block(seg_text, portion)
                if per_kanji is not None:
                    return per_kanji + rest

                # 分配失败：整块保留
                return [(seg_text, portion)] + rest

        return None

    def _try_distribute_kanji_block(
        self, kanji_text: str, compound_reading: str
    ) -> Optional[List[Tuple[str, str]]]:
        """尝试将复合读音分配到各个漢字。

        使用 pykakasi 的单字读音作为参考：
        - 如果单字读音恰好是复合读音的前缀，则认为分配有效
        - 否则放弃分配，保持整块
        """
        if not self._pykakasi_conv:
            return None

        n = len(kanji_text)
        ref_readings: List[str] = []
        for k in kanji_text:
            try:
                ref = self._pykakasi_conv.do(k)
            except Exception:
                ref = ""
            ref_readings.append(ref)

        return self._partition_with_refs(
            kanji_text, compound_reading, ref_readings, 0, 0
        )

    def _partition_with_refs(
        self,
        kanji_text: str,
        reading: str,
        ref_readings: List[str],
        ki: int,
        ri: int,
    ) -> Optional[List[Tuple[str, str]]]:
        """递归分区：利用 pykakasi 参考读音约束搜索。"""
        if ki == len(kanji_text):
            return [] if ri == len(reading) else None

        remaining_kanji = len(kanji_text) - ki
        remaining_chars = len(reading) - ri
        if remaining_chars < remaining_kanji:
            return None

        max_len = remaining_chars - (remaining_kanji - 1)
        ref = ref_readings[ki]

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

        # 其次尝试前缀匹配：分配部分是参考读音的前缀
        for try_len in range(1, max_len + 1):
            if ref and try_len == len(ref):
                continue  # 已尝试
            portion = reading[ri : ri + try_len]
            if ref and not ref.startswith(portion):
                continue  # 不符合参考约束
            rest = self._partition_with_refs(
                kanji_text, reading, ref_readings, ki + 1, ri + try_len
            )
            if rest is not None:
                return [(kanji_text[ki], portion)] + rest

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
        )

    @staticmethod
    def _is_kana(char: str) -> bool:
        code = ord(char)
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)


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
                    results.append(
                        RubyResult(text=char, reading=char, start_idx=i, end_idx=i + 1)
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
        )

    @staticmethod
    def _is_kana(char: str) -> bool:
        code = ord(char)
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)


class DummyAnalyzer(RubyAnalyzer):
    """虚拟注音分析器（用于测试）"""

    def analyze(self, text: str) -> List[RubyResult]:
        return [
            RubyResult(text=char, reading=char, start_idx=i, end_idx=i + 1)
            for i, char in enumerate(text)
        ]

    def get_reading(self, text: str) -> str:
        return text


def create_analyzer(use_pykakasi: bool = True) -> RubyAnalyzer:
    """创建注音分析器。

    优先使用 SudachiPy（上下文感知复合词分析），
    回退到 pykakasi（单字分析），最后使用 DummyAnalyzer。
    """
    # 优先尝试 SudachiPy
    try:
        return SudachiAnalyzer()
    except ImportError:
        pass

    # 回退到 pykakasi
    if use_pykakasi:
        try:
            return PykakasiAnalyzer()
        except ImportError:
            pass

    print("Warning: neither sudachipy nor pykakasi available, using DummyAnalyzer")
    return DummyAnalyzer()
