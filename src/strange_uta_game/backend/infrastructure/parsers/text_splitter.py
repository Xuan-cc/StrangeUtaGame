"""文本拆分器 - 将歌词文本拆分为字符列表。

支持日文（汉字、假名、长音、促音）和英文的拆分规则。
"""

import re
from abc import ABC, abstractmethod
from typing import List, Tuple
from enum import Enum, auto


class CharType(Enum):
    """字符类型"""

    KANJI = auto()  # 汉字
    HIRAGANA = auto()  # 平假名
    KATAKANA = auto()  # 片假名
    LONG_VOWEL = auto()  # 长音「ー」
    SOKUON = auto()  # 促音「っ/ッ」
    ALPHABET = auto()  # 英文字母
    NUMBER = auto()  # 数字
    SYMBOL = auto()  # 符号
    SPACE = auto()  # 空格
    OTHER = auto()  # 其他


def get_char_type(char: str) -> CharType:
    """获取字符类型

    Args:
        char: 单个字符

    Returns:
        字符类型
    """
    if len(char) != 1:
        raise ValueError(f"必须是单个字符: {char}")

    # 长音
    if char in ("ー", "－", "～", "〜"):
        return CharType.LONG_VOWEL

    # 促音
    if char in ("っ", "ッ"):
        return CharType.SOKUON

    # 平假名
    if "\u3040" <= char <= "\u309f":
        return CharType.HIRAGANA

    # 片假名
    if "\u30a0" <= char <= "\u30ff":
        return CharType.KATAKANA

    # CJK 统一表意文字（汉字）
    if (
        "\u4e00" <= char <= "\u9fff"
        or "\u3400" <= char <= "\u4dbf"
        or "\uf900" <= char <= "\ufaff"
    ):
        return CharType.KANJI

    # 英文字母
    if char.isalpha():
        return CharType.ALPHABET

    # 数字
    if char.isdigit():
        return CharType.NUMBER

    # 空格
    if char.isspace():
        return CharType.SPACE

    # 符号
    if char in '.,!?。、！？…―・「」『』（）［］｛｝"":;：；/／＼':
        return CharType.SYMBOL

    return CharType.OTHER


class TextSplitter(ABC):
    """文本拆分器抽象基类"""

    @abstractmethod
    def split(self, text: str) -> List[str]:
        """将文本拆分为字符列表

        Args:
            text: 输入文本

        Returns:
            字符列表
        """
        pass


class JapaneseSplitter(TextSplitter):
    """日文文本拆分器

    拆分规则：
    - 汉字：单独拆分
    - 假名：单独拆分
    - 长音「ー」：可配置是否独立拆分
    - 促音「っ/ッ」：可配置是否独立拆分
    - 符号：单独拆分
    - 连续空格：合并为单个空格
    """

    def __init__(
        self,
        split_long_vowel: bool = True,
        split_sokuon: bool = True,
        merge_spaces: bool = True,
    ):
        """
        Args:
            split_long_vowel: 是否将长音「ー」作为独立字符拆分
            split_sokuon: 是否将促音「っ/ッ」作为独立字符拆分
            merge_spaces: 是否合并连续空格
        """
        self.split_long_vowel = split_long_vowel
        self.split_sokuon = split_sokuon
        self.merge_spaces = merge_spaces

    def split(self, text: str) -> List[str]:
        """拆分日文文本"""
        if not text:
            return []

        chars = []
        prev_was_space = False

        for char in text:
            char_type = get_char_type(char)

            # 处理空格
            if char_type == CharType.SPACE:
                if self.merge_spaces:
                    if not prev_was_space:
                        chars.append(" ")
                        prev_was_space = True
                else:
                    chars.append(char)
                continue

            prev_was_space = False

            # 根据配置决定是否拆分长音
            if char_type == CharType.LONG_VOWEL and not self.split_long_vowel:
                # 将长音合并到前一个字符
                if chars:
                    # 这里只是标记，实际合并逻辑由调用者处理
                    chars.append(char)
                else:
                    chars.append(char)
                continue

            # 根据配置决定是否拆分促音
            if char_type == CharType.SOKUON and not self.split_sokuon:
                # 将促音合并到前一个字符
                if chars:
                    chars.append(char)
                else:
                    chars.append(char)
                continue

            # 其他字符直接添加
            chars.append(char)

        return chars


class EnglishSplitter(TextSplitter):
    """英文文本拆分器

    拆分规则：
    - 字母：单独拆分
    - 数字：单独拆分
    - 标点符号：单独拆分
    - 单词边界：保留空格用于识别
    - 连续空格：合并为单个空格
    """

    def __init__(self, merge_spaces: bool = True):
        """
        Args:
            merge_spaces: 是否合并连续空格
        """
        self.merge_spaces = merge_spaces

    def split(self, text: str) -> List[str]:
        """拆分英文文本"""
        if not text:
            return []

        chars = []
        prev_was_space = False

        for char in text:
            if char.isspace():
                if self.merge_spaces:
                    if not prev_was_space:
                        chars.append(" ")
                        prev_was_space = True
                else:
                    chars.append(char)
                continue

            prev_was_space = False
            chars.append(char)

        return chars


class AutoSplitter(TextSplitter):
    """自动文本拆分器

    根据文本内容自动选择合适的拆分策略。
    """

    def __init__(
        self,
        japanese_splitter: JapaneseSplitter = None,
        english_splitter: EnglishSplitter = None,
    ):
        self.japanese_splitter = japanese_splitter or JapaneseSplitter()
        self.english_splitter = english_splitter or EnglishSplitter()

    def detect_language(self, text: str) -> str:
        """检测文本主要语言

        Args:
            text: 输入文本

        Returns:
            语言代码: "ja" (日文), "en" (英文), "mixed" (混合), "other" (其他)
        """
        if not text:
            return "other"

        ja_chars = 0
        en_chars = 0
        other_chars = 0

        for char in text:
            char_type = get_char_type(char)

            if char_type in (
                CharType.KANJI,
                CharType.HIRAGANA,
                CharType.KATAKANA,
                CharType.LONG_VOWEL,
                CharType.SOKUON,
            ):
                ja_chars += 1
            elif char_type == CharType.ALPHABET:
                en_chars += 1
            elif not char.isspace():
                other_chars += 1

        total = ja_chars + en_chars + other_chars
        if total == 0:
            return "other"

        ja_ratio = ja_chars / total
        en_ratio = en_chars / total

        if ja_ratio > 0.5:
            return "ja"
        elif en_ratio > 0.5:
            return "en"
        elif ja_ratio > 0.2 or en_ratio > 0.2:
            return "mixed"
        else:
            return "other"

    def split(self, text: str) -> List[str]:
        """自动拆分文本"""
        lang = self.detect_language(text)

        if lang == "ja":
            return self.japanese_splitter.split(text)
        elif lang == "en":
            return self.english_splitter.split(text)
        else:
            # 混合或其他语言，使用通用拆分
            return list(text)


class SplitConfig:
    """拆分配置

    用于 AutoCheckService 的字符拆分配置。
    """

    def __init__(
        self,
        split_long_vowel: bool = True,
        split_sokuon: bool = True,
        count_sokuon: bool = True,
        count_long_vowel: bool = True,
    ):
        """
        Args:
            split_long_vowel: 是否拆分长音「ー」
            split_sokuon: 是否拆分促音「っ/ッ」
            count_sokuon: 促音是否计入节奏点数量
            count_long_vowel: 长音是否计入节奏点数量
        """
        self.split_long_vowel = split_long_vowel
        self.split_sokuon = split_sokuon
        self.count_sokuon = count_sokuon
        self.count_long_vowel = count_long_vowel


def split_text(text: str, config: SplitConfig = None) -> Tuple[List[str], List[int]]:
    """拆分文本并返回字符列表和建议的节奏点数量

    Args:
        text: 输入文本
        config: 拆分配置

    Returns:
        (字符列表, 每个字符的建议节奏点数量)
    """
    if not text:
        return [], []

    config = config or SplitConfig()

    splitter = AutoSplitter(
        japanese_splitter=JapaneseSplitter(
            split_long_vowel=config.split_long_vowel, split_sokuon=config.split_sokuon
        )
    )

    chars = splitter.split(text)
    check_counts = []

    for char in chars:
        char_type = get_char_type(char)

        # 计算节奏点数量
        if char_type == CharType.SOKUON:
            check_counts.append(1 if config.count_sokuon else 0)
        elif char_type == CharType.LONG_VOWEL:
            check_counts.append(1 if config.count_long_vowel else 0)
        elif char_type in (CharType.KANJI, CharType.HIRAGANA, CharType.KATAKANA):
            # 假名通常 1 个节奏点，汉字根据注音确定（这里默认 1）
            check_counts.append(1)
        elif char_type == CharType.ALPHABET:
            # 英文字母通常 1 个
            check_counts.append(1)
        elif char_type == CharType.SPACE:
            # 空格 0 个
            check_counts.append(0)
        else:
            # 其他默认 1 个
            check_counts.append(1)

    return chars, check_counts
