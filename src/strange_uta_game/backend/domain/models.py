"""领域层核心值对象定义。

值对象(Value Object)由属性值决定相等性，创建后不可变。
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum, auto


class DomainError(Exception):
    """领域层错误基类"""

    pass


class ValidationError(DomainError):
    """验证错误"""

    pass


class TimeTagType(Enum):
    """时间标签类型"""

    CHAR_START = auto()  # 字符开始
    CHAR_MIDDLE = auto()  # 字符中间（连打场景）
    LINE_END = auto()  # 行尾
    REST = auto()  # 休止符


@dataclass(frozen=True)
class Checkpoint:
    """节奏点/检查点

    表示一个需要击打的时间点，对应用户一次按键操作。
    属于值对象，创建后不可变。

    Attributes:
        timestamp_ms: 时间戳（毫秒）
        char_idx: 对应字符索引
        checkpoint_idx: 在该字符内的第几个节奏点（从0开始）

    Example:
        >>> cp = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        >>> cp.timestamp_ms
        1000
    """

    timestamp_ms: int
    char_idx: int
    checkpoint_idx: int = 0

    def __post_init__(self) -> None:
        """验证属性值"""
        if self.timestamp_ms < 0:
            raise ValidationError(f"时间戳不能为负数: {self.timestamp_ms}")
        if self.char_idx < 0:
            raise ValidationError(f"字符索引不能为负数: {self.char_idx}")
        if self.checkpoint_idx < 0:
            raise ValidationError(f"节奏点索引不能为负数: {self.checkpoint_idx}")


@dataclass(frozen=True)
class CheckpointConfig:
    """节奏点配置

    配置每个字符的节奏点数量和类型。
    与 LyricLine.chars 一一对应。

    Attributes:
        char_idx: 字符索引
        check_count: 节奏点数量（需要击打几次，默认1，可以为0）
        is_line_end: 是否是句尾字符（行末，默认False）
        is_rest: 是否是休止符（默认False）

    Examples:
        普通字符：
        >>> config = CheckpointConfig(char_idx=0, check_count=1)

        连打场景（如「赤」=あ+か）：
        >>> config = CheckpointConfig(char_idx=0, check_count=2)

        长音连唱：
        >>> config = CheckpointConfig(char_idx=1, check_count=0)  # 长音"ー"

        句尾字符：
        >>> config = CheckpointConfig(char_idx=5, check_count=1, is_line_end=True)
    """

    char_idx: int
    check_count: int = 1
    is_line_end: bool = False
    is_rest: bool = False
    linked_to_next: bool = False
    singer_id: str = ""  # 每字符演唱者ID，空串表示继承行级 singer_id

    def __post_init__(self) -> None:
        """验证属性值"""
        if self.char_idx < 0:
            raise ValidationError(f"字符索引不能为负数: {self.char_idx}")
        if self.check_count < 0:
            raise ValidationError(f"节奏点数量不能为负数: {self.check_count}")


@dataclass(frozen=True)
class TimeTag:
    """时间标签

    表示一个时间点，允许时间倒退（用户修改时可能出现）。
    仅标记异常，不阻止操作。

    Attributes:
        timestamp_ms: 绝对时间（毫秒）
        singer_id: 所属演唱者ID（必填，用于多声部）
        char_idx: 对应字符索引
        checkpoint_idx: 在该字符内的第几个节奏点
        tag_type: 标签类型

    Example:
        >>> tag = TimeTag(
        ...     timestamp_ms=10000,
        ...     singer_id="singer_1",
        ...     char_idx=0,
        ...     checkpoint_idx=0,
        ...     tag_type=TimeTagType.CHAR_START
        ... )
    """

    timestamp_ms: int
    singer_id: str
    char_idx: int
    checkpoint_idx: int = 0
    tag_type: TimeTagType = TimeTagType.CHAR_START

    def __post_init__(self) -> None:
        """验证属性值"""
        if self.timestamp_ms < 0:
            raise ValidationError(f"时间戳不能为负数: {self.timestamp_ms}")
        if not self.singer_id:
            raise ValidationError("singer_id 不能为空")
        if self.char_idx < 0:
            raise ValidationError(f"字符索引不能为负数: {self.char_idx}")
        if self.checkpoint_idx < 0:
            raise ValidationError(f"节奏点索引不能为负数: {self.checkpoint_idx}")


@dataclass(frozen=True)
class Ruby:
    """注音/振り仮名

    表示汉字的注音（ルビ）。
    支持一个注音覆盖多个连续字符。

    Attributes:
        text: 注音文本（如 "あか"）
        start_idx: 起始字符索引（包含）
        end_idx: 结束字符索引（不包含）

    Example:
        「赤」的注音：
        >>> ruby = Ruby(text="あか", start_idx=0, end_idx=1)

        「昨日」的注音（多对一）：
        >>> ruby = Ruby(text="きのう", start_idx=0, end_idx=2)
    """

    text: str
    start_idx: int
    end_idx: int

    def __post_init__(self) -> None:
        """验证属性值"""
        if not self.text:
            raise ValidationError("注音文本不能为空")
        if self.start_idx < 0:
            raise ValidationError(f"起始索引不能为负数: {self.start_idx}")
        if self.end_idx < 0:
            raise ValidationError(f"结束索引不能为负数: {self.end_idx}")
        if self.start_idx >= self.end_idx:
            raise ValidationError(
                f"起始索引必须小于结束索引: {self.start_idx} >= {self.end_idx}"
            )

    def covers_char(self, char_idx: int) -> bool:
        """检查注音是否覆盖指定字符

        Args:
            char_idx: 字符索引

        Returns:
            是否覆盖该字符
        """
        return self.start_idx <= char_idx < self.end_idx


@dataclass(frozen=True)
class LineTimingInfo:
    """歌词行时间信息

    记录歌词行的时间范围，用于性能优化和显示。

    Attributes:
        start_ms: 行开始时间（该行第一个时间标签）
        end_ms: 行结束时间（该行最后一个时间标签或下一行开始）
    """

    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if self.start_ms < 0:
            raise ValidationError(f"开始时间不能为负数: {self.start_ms}")
        if self.end_ms < 0:
            raise ValidationError(f"结束时间不能为负数: {self.end_ms}")
        if self.start_ms > self.end_ms:
            raise ValidationError(
                f"开始时间不能大于结束时间: {self.start_ms} > {self.end_ms}"
            )

    def contains(self, timestamp_ms: int) -> bool:
        """检查时间戳是否在该行时间范围内

        Args:
            timestamp_ms: 时间戳（毫秒）

        Returns:
            是否在该行时间范围内
        """
        return self.start_ms <= timestamp_ms < self.end_ms
