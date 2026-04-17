"""领域层实体定义。

实体(Entity)具有唯一标识和生命周期，可以被创建、修改、持久化。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from uuid import uuid4, UUID

from .models import (
    CheckpointConfig,
    TimeTag,
    TimeTagType,
    Ruby,
    DomainError,
    ValidationError,
)


@dataclass
class Singer:
    """演唱者/角色

    表示一个演唱者或角色（用于多声部和声场景）。

    Attributes:
        id: 唯一标识符（UUID）
        name: 演唱者名称（如 "初音ミク"、"合唱"、"和声"）
        color: 显示颜色（用于区分不同演唱者，如 "#FF6B6B"）
        is_default: 是否为默认演唱者
        display_priority: 显示优先级（数字越小越优先显示）
        enabled: 是否启用（禁用的演唱者不参与全局序列）

    Example:
        >>> singer = Singer(name="初音ミク", color="#FF6B6B")
        >>> singer.name
        '初音ミク'
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "未命名"
    color: str = "#FF6B6B"  # 默认红色
    backend_number: int = 0  # 后台编号（从1开始递增，不随显示名改变）
    is_default: bool = False
    display_priority: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        """验证属性值"""
        if not self.id:
            raise ValidationError("演唱者ID不能为空")
        if not self.name:
            raise ValidationError("演唱者名称不能为空")
        if not self.color.startswith("#") or len(self.color) != 7:
            raise ValidationError(f"颜色格式无效: {self.color} (应为 #RRGGBB)")

    def rename(self, new_name: str) -> None:
        """重命名演唱者

        Args:
            new_name: 新名称

        Raises:
            ValidationError: 如果名称为空
        """
        if not new_name:
            raise ValidationError("演唱者名称不能为空")
        self.name = new_name

    def change_color(self, new_color: str) -> None:
        """修改显示颜色

        Args:
            new_color: 新颜色（#RRGGBB 格式）

        Raises:
            ValidationError: 如果颜色格式无效
        """
        if not new_color.startswith("#") or len(new_color) != 7:
            raise ValidationError(f"颜色格式无效: {new_color}")
        self.color = new_color

    def set_enabled(self, enabled: bool) -> None:
        """设置启用状态

        Args:
            enabled: 是否启用
        """
        self.enabled = enabled


@dataclass
class LyricLine:
    """歌词行

    表示一行歌词及其时间标签、注音信息、节奏点配置。

    Attributes:
        id: 唯一标识符（UUID）
        singer_id: 所属演唱者ID（必填，关联到 Singer）
        text: 原始文本
        chars: 拆分的字符列表（由 text 动态生成）
        timetags: 时间标签列表（按时间排序）
        checkpoints: 节奏点配置列表（与 chars 一一对应）
        rubies: 注音列表

    Note:
        chars 是 text 的拆分结果，运行时动态生成。
        持久化时两者都存储用于校验。

    Example:
        >>> line = LyricLine(
        ...     singer_id="singer_1",
        ...     text="赤い花",
        ...     chars=["赤", "い", "花"]
        ... )
        >>> len(line.chars)
        3
    """

    singer_id: str
    text: str
    id: str = field(default_factory=lambda: str(uuid4()))
    chars: List[str] = field(default_factory=list)
    timetags: List[TimeTag] = field(default_factory=list)
    checkpoints: List[CheckpointConfig] = field(default_factory=list)
    rubies: List[Ruby] = field(default_factory=list)

    def __post_init__(self) -> None:
        """验证属性值并初始化"""
        if not self.id:
            raise ValidationError("歌词行ID不能为空")
        if not self.singer_id:
            raise ValidationError("singer_id 不能为空")
        if not self.text:
            raise ValidationError("歌词文本不能为空")

        # 如果没有提供 chars，从 text 生成
        if not self.chars:
            self.chars = list(self.text)

        # 验证 chars 和 text 一致性
        if "".join(self.chars) != self.text:
            raise ValidationError("chars 和 text 不一致")

        # 初始化节奏点配置（如果未提供）
        if not self.checkpoints:
            self._init_default_checkpoints()

    def _init_default_checkpoints(self) -> None:
        """初始化默认节奏点配置"""
        self.checkpoints = [
            CheckpointConfig(
                char_idx=i,
                check_count=2 if i == len(self.chars) - 1 else 1,
                is_line_end=(i == len(self.chars) - 1),  # 最后一个字符标记为句尾
            )
            for i in range(len(self.chars))
        ]

    def add_timetag(self, tag: TimeTag) -> None:
        """添加时间标签

        自动按时间排序插入。

        Args:
            tag: 要添加的时间标签

        Raises:
            ValidationError: 如果 char_idx 或 checkpoint_idx 无效
        """
        # 验证索引
        if tag.char_idx < 0 or tag.char_idx >= len(self.chars):
            raise ValidationError(
                f"字符索引 {tag.char_idx} 超出范围 [0, {len(self.chars)})"
            )

        config = self.get_checkpoint_config(tag.char_idx)
        if tag.checkpoint_idx < 0 or tag.checkpoint_idx >= config.check_count:
            raise ValidationError(
                f"节奏点索引 {tag.checkpoint_idx} 超出范围 [0, {config.check_count})"
            )

        # 添加到列表并按时间排序
        self.timetags.append(tag)
        self.timetags.sort(key=lambda t: t.timestamp_ms)

    def remove_timetag(self, tag: TimeTag) -> None:
        """移除时间标签

        Args:
            tag: 要移除的时间标签

        Raises:
            DomainError: 如果时间标签不存在
        """
        if tag not in self.timetags:
            raise DomainError("时间标签不存在")
        self.timetags.remove(tag)

    def get_checkpoint_config(self, char_idx: int) -> CheckpointConfig:
        """获取指定字符的节奏点配置

        Args:
            char_idx: 字符索引

        Returns:
            节奏点配置

        Raises:
            ValidationError: 如果字符索引无效
        """
        if char_idx < 0 or char_idx >= len(self.checkpoints):
            raise ValidationError(
                f"字符索引 {char_idx} 超出范围 [0, {len(self.checkpoints)})"
            )
        return self.checkpoints[char_idx]

    def set_checkpoint_config(self, config: CheckpointConfig) -> None:
        """设置字符的节奏点配置

        Args:
            config: 节奏点配置
        """
        if config.char_idx < 0 or config.char_idx >= len(self.checkpoints):
            raise ValidationError(
                f"字符索引 {config.char_idx} 超出范围 [0, {len(self.checkpoints)})"
            )
        self.checkpoints[config.char_idx] = config

    def get_ruby_for_char(self, char_idx: int) -> Optional[Ruby]:
        """获取指定字符的注音

        Args:
            char_idx: 字符索引

        Returns:
            注音对象，如果没有则返回 None
        """
        for ruby in self.rubies:
            if ruby.covers_char(char_idx):
                return ruby
        return None

    def add_ruby(self, ruby: Ruby) -> None:
        """添加注音

        Args:
            ruby: 注音对象

        Raises:
            ValidationError: 如果索引范围无效
        """
        if ruby.end_idx > len(self.chars):
            raise ValidationError(
                f"注音结束索引 {ruby.end_idx} 超出字符数量 {len(self.chars)}"
            )

        # 检查是否有重叠的注音
        for existing in self.rubies:
            if ruby.start_idx < existing.end_idx and ruby.end_idx > existing.start_idx:
                raise ValidationError(
                    f"注音范围 [{ruby.start_idx}, {ruby.end_idx}) "
                    f"与现有注音 [{existing.start_idx}, {existing.end_idx}) 重叠"
                )

        self.rubies.append(ruby)
        # 按起始索引排序
        self.rubies.sort(key=lambda r: r.start_idx)

    def get_timetags_for_char(self, char_idx: int) -> List[TimeTag]:
        """获取指定字符的所有时间标签

        Args:
            char_idx: 字符索引

        Returns:
            时间标签列表（按 checkpoint_idx 排序）
        """
        tags = [t for t in self.timetags if t.char_idx == char_idx]
        return sorted(tags, key=lambda t: t.checkpoint_idx)

    def is_fully_timed(self) -> bool:
        """检查是否所有节奏点都已打轴

        Returns:
            是否所有需要的 time tag 都已存在
        """
        expected_count = sum(c.check_count for c in self.checkpoints)
        return len(self.timetags) >= expected_count

    def get_timing_progress(self) -> tuple[int, int]:
        """获取打轴进度

        Returns:
            (已完成数量, 总共需要数量)
        """
        expected = sum(c.check_count for c in self.checkpoints)
        return (len(self.timetags), expected)
