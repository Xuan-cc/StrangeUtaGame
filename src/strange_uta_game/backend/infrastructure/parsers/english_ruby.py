"""英语单词 → 片假名注音查询。

使用 e2k.txt（英语到片假名词典，CMU-based，BSD-3-Clause）做单词级查询。
优先级：用户自定义词典 → 本模块 → 库函数（Sudachi/pykakasi）。
"""

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class EnglishRubyLookup:
    """英语单词 → 片假名查询。

    数据来源：e2k.txt（TSV 格式：english_word\\tカタカナ），小写键。
    """

    _instance: Optional["EnglishRubyLookup"] = None

    def __init__(self):
        self._dict: Dict[str, str] = {}
        self._loaded = False

    @classmethod
    def instance(cls) -> "EnglishRubyLookup":
        """单例模式，避免重复加载大词典。"""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        """从 e2k.txt 加载词典（打包环境与开发环境均可用）。"""
        if self._loaded:
            return
        path = self._resolve_path()
        if path is None or not path.exists():
            self._loaded = True
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\r\n")
                    if not line or "\t" not in line:
                        continue
                    word, reading = line.split("\t", 1)
                    word = word.strip().lower()
                    reading = reading.strip()
                    if word and reading:
                        # 首次出现的读音优先
                        if word not in self._dict:
                            self._dict[word] = reading
        except Exception as e:
            print(f"加载 e2k 英语词典失败: {e}")
        self._loaded = True

    @staticmethod
    def _resolve_path() -> Optional[Path]:
        """解析 e2k.txt 路径（兼容 PyInstaller 打包环境）。"""
        base = getattr(sys, "_MEIPASS", None)
        if base:
            p = Path(base) / "strange_uta_game" / "config" / "e2k.txt"
            if p.exists():
                return p
        # 开发环境 (parsers/ → infrastructure/ → backend/ → strange_uta_game/)
        dev_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "config"
            / "e2k.txt"
        )
        if dev_path.exists():
            return dev_path
        return None

    def lookup(self, word: str) -> Optional[str]:
        """查询单词的片假名读音，不存在返回 None。"""
        if not word:
            return None
        return self._dict.get(word.lower())

    def has(self) -> bool:
        """词典是否加载成功且非空。"""
        return bool(self._dict)


# 英文单词匹配（连续英文字母，允许内部 ' 和 .）
_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]+(?:['.][A-Za-z]+)*")


def find_english_words(text: str) -> List[Tuple[int, int, str]]:
    """在文本中定位所有英文单词。

    Returns:
        (start_idx, end_idx, word) 元组列表（end 为 exclusive）。
    """
    results = []
    for m in _ENGLISH_WORD_RE.finditer(text):
        results.append((m.start(), m.end(), m.group()))
    return results
