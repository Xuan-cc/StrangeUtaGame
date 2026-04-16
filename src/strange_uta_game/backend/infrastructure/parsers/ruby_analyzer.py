"""注音分析器 - 为日文文本提供假名注音。

使用 pykakasi 库进行汉字转假名。
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
    """创建注音分析器"""
    if use_pykakasi:
        try:
            return PykakasiAnalyzer()
        except ImportError:
            print("Warning: pykakasi not available, using DummyAnalyzer")
            return DummyAnalyzer()
    return DummyAnalyzer()
