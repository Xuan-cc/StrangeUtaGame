"""领域层实体定义。

实体(Entity)具有唯一标识和生命周期，可以被创建、修改、持久化。
"""

from dataclasses import dataclass, field
from typing import List, Optional
from uuid import uuid4

from .models import (
    Character,
    Ruby,
    Word,
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
        backend_number: 后台编号（从1开始递增，不随显示名改变）

    Example:
        >>> singer = Singer(name="初音ミク", color="#FF6B6B")
        >>> singer.name
        '初音ミク'
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "未命名"
    color: str = "#FF6B6B"
    backend_number: int = 0
    is_default: bool = False
    display_priority: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise ValidationError("演唱者ID不能为空")
        if not self.name:
            raise ValidationError("演唱者名称不能为空")
        if not self.color.startswith("#") or len(self.color) != 7:
            raise ValidationError(f"颜色格式无效: {self.color} (应为 #RRGGBB)")

    def rename(self, new_name: str) -> None:
        """重命名演唱者"""
        if not new_name:
            raise ValidationError("演唱者名称不能为空")
        self.name = new_name

    def change_color(self, new_color: str) -> None:
        """修改显示颜色"""
        if not new_color.startswith("#") or len(new_color) != 7:
            raise ValidationError(f"颜色格式无效: {new_color}")
        self.color = new_color

    def set_enabled(self, enabled: bool) -> None:
        """设置启用状态"""
        self.enabled = enabled


@dataclass
class Sentence:
    """句子 — 一行歌词

    由字符列表构成，按 linked_to_next 标记自动分组为词语。
    句子是歌词的基本行单位，对应一行文本。

    Attributes:
        id: 唯一标识符（UUID）
        singer_id: 所属演唱者ID（行级默认演唱者）
        characters: 字符列表

    Example:
        >>> s = Sentence(
        ...     singer_id="singer_1",
        ...     characters=[
        ...         Character(char="赤", singer_id="singer_1"),
        ...         Character(char="い", singer_id="singer_1"),
        ...     ]
        ... )
        >>> s.text
        '赤い'
    """

    singer_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    characters: List[Character] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValidationError("句子ID不能为空")
        if not self.singer_id:
            raise ValidationError("singer_id 不能为空")

    # ── 文本属性 ──

    @property
    def text(self) -> str:
        """歌词文本"""
        return "".join(c.char for c in self.characters)

    @property
    def chars(self) -> List[str]:
        """字符列表（兼容旧接口）"""
        return [c.char for c in self.characters]

    # ── 词语计算 ──

    @property
    def words(self) -> List[Word]:
        """从字符的 linked_to_next 标志计算词语

        拥有 linked_to_next=True 的字符与下一字符组成同一词语。
        没有连词的单个字符独立为一个词语。

        Returns:
            词语列表
        """
        words: List[Word] = []
        current: List[Character] = []
        for char in self.characters:
            current.append(char)
            if not char.linked_to_next:
                words.append(Word(characters=current))
                current = []
        if current:
            words.append(Word(characters=current))
        return words

    # ── 字符管理 ──

    def get_character(self, char_idx: int) -> Optional[Character]:
        """获取指定索引的字符"""
        if 0 <= char_idx < len(self.characters):
            return self.characters[char_idx]
        return None

    def get_ruby_for_char(self, char_idx: int) -> Optional[Ruby]:
        """获取指定字符的注音"""
        char = self.get_character(char_idx)
        return char.ruby if char else None

    def get_word_for_char(self, char_idx: int) -> Optional[Word]:
        """获取包含指定字符的词语"""
        idx = 0
        for word in self.words:
            end_idx = idx + len(word.characters)
            if idx <= char_idx < end_idx:
                return word
            idx = end_idx
        return None

    def get_word_char_range(self, char_idx: int) -> tuple[int, int]:
        """获取包含指定字符的词语的字符范围

        Returns:
            (start_idx, end_idx) — 左闭右开
        """
        idx = 0
        for word in self.words:
            end_idx = idx + len(word.characters)
            if idx <= char_idx < end_idx:
                return idx, end_idx
            idx = end_idx
        return char_idx, char_idx + 1

    # ── 时间戳管理 ──

    def push_all_timestamps(self) -> None:
        """将所有字符的时间戳推送给各自的 Ruby"""
        for char in self.characters:
            char.push_to_ruby()

    def get_timetags_for_char(self, char_idx: int) -> List[int]:
        """获取指定字符的所有时间戳

        Returns:
            时间戳列表（按 checkpoint_idx 顺序）
        """
        char = self.get_character(char_idx)
        return list(char.timestamps) if char else []

    def clear_all_timestamps(self) -> None:
        """清空所有字符的时间戳"""
        for char in self.characters:
            char.clear_timestamps()

    # ── 查询 ──

    def is_fully_timed(self) -> bool:
        """检查是否所有字符的节奏点都已打轴"""
        if not self.characters:
            return False
        return all(c.is_fully_timed for c in self.characters)

    def get_timing_progress(self) -> tuple[int, int]:
        """获取打轴进度

        Returns:
            (已完成数量, 总共需要数量)
        """
        done = sum(len(c.timestamps) for c in self.characters)
        total = sum(c.check_count for c in self.characters)
        return done, total

    @property
    def timing_start_ms(self) -> Optional[int]:
        """句子最早时间戳（毫秒），如果无时间标签返回 None"""
        all_ts = [ts for c in self.characters for ts in c.timestamps]
        return min(all_ts) if all_ts else None

    @property
    def timing_end_ms(self) -> Optional[int]:
        """句子最晚时间戳（毫秒），如果无时间标签返回 None"""
        all_ts = [ts for c in self.characters for ts in c.timestamps]
        return max(all_ts) if all_ts else None

    @property
    def has_timetags(self) -> bool:
        """是否有任何时间标签"""
        return any(c.timestamps for c in self.characters)

    # ── Ruby 管理 ──

    @property
    def rubies(self) -> List[Ruby]:
        """收集所有字符的 Ruby 对象"""
        return [c.ruby for c in self.characters if c.ruby]

    def add_ruby_to_char(self, char_idx: int, ruby: Ruby) -> None:
        """为指定字符添加注音

        Args:
            char_idx: 字符索引
            ruby: 注音对象

        Raises:
            ValidationError: 如果字符索引无效或字符已有注音
        """
        char = self.get_character(char_idx)
        if not char:
            raise ValidationError(
                f"字符索引 {char_idx} 超出范围 [0, {len(self.characters)})"
            )
        if char.ruby:
            raise ValidationError(f"字符 {char_idx} 已有注音")
        char.set_ruby(ruby)

    def remove_ruby_from_char(self, char_idx: int) -> Optional[Ruby]:
        """移除指定字符的注音

        Returns:
            被移除的 Ruby 对象，如果没有返回 None
        """
        char = self.get_character(char_idx)
        if char and char.ruby:
            removed = char.ruby
            char.set_ruby(None)
            return removed
        return None

    def clear_all_rubies(self) -> None:
        """清空所有字符的注音"""
        for char in self.characters:
            char.set_ruby(None)

    # ── 初始化辅助 ──

    @classmethod
    def from_text(
        cls,
        text: str,
        singer_id: str,
        id: Optional[str] = None,
    ) -> "Sentence":
        """从纯文本创建句子

        自动拆分为单字符，设置默认 checkpoint 配置。
        最后一个字符标记为句尾（check_count=2）。

        Args:
            text: 歌词文本
            singer_id: 演唱者 ID
            id: 可选的句子 ID

        Returns:
            Sentence 实例
        """
        if not text:
            raise ValidationError("歌词文本不能为空")

        chars_list = list(text)
        characters = []
        for i, ch in enumerate(chars_list):
            is_last = i == len(chars_list) - 1
            characters.append(
                Character(
                    char=ch,
                    check_count=2 if is_last else 1,
                    is_line_end=is_last,
                    singer_id=singer_id,
                )
            )

        kwargs = {"singer_id": singer_id, "characters": characters}
        if id is not None:
            kwargs["id"] = id
        return cls(**kwargs)
