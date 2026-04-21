"""领域层实体定义。

实体(Entity)具有唯一标识和生命周期，可以被创建、修改、持久化。
"""

import colorsys
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


def _compute_complement_color(hex_color: str) -> str:
    """计算 HSV 色相旋转 180° 后的补色（保持 S/V 不变）。

    用于选中高亮渲染：和演唱者基础色在色相环上对立，保证对比度。
    纯灰色（S=0）无意义色相，直接返回原色。

    Args:
        hex_color: 形如 "#RRGGBB" 的十六进制色。

    Returns:
        形如 "#RRGGBB" 的补色；若输入无效或为灰度返回原值。
    """
    if not hex_color or not hex_color.startswith("#") or len(hex_color) != 7:
        return hex_color
    try:
        r = int(hex_color[1:3], 16) / 255.0
        g = int(hex_color[3:5], 16) / 255.0
        b = int(hex_color[5:7], 16) / 255.0
    except ValueError:
        return hex_color
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s <= 1e-6:
        return hex_color
    h2 = (h + 0.5) % 1.0
    r2, g2, b2 = colorsys.hsv_to_rgb(h2, s, v)
    return "#{:02X}{:02X}{:02X}".format(
        int(round(r2 * 255)), int(round(g2 * 255)), int(round(b2 * 255))
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
    complement_color: str = ""
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
        # 自动补算补色：持久化但不强制用户可见。
        # 仅在字段为空或与当前 color 不兼容时重算，保证 .sug 旧文件向后兼容。
        if not self.complement_color or not self.complement_color.startswith("#") or len(self.complement_color) != 7:
            self.complement_color = _compute_complement_color(self.color)

    def rename(self, new_name: str) -> None:
        """重命名演唱者"""
        if not new_name:
            raise ValidationError("演唱者名称不能为空")
        self.name = new_name

    def change_color(self, new_color: str) -> None:
        """修改显示颜色（同步更新补色）"""
        if not new_color.startswith("#") or len(new_color) != 7:
            raise ValidationError(f"颜色格式无效: {new_color}")
        self.color = new_color
        self.complement_color = _compute_complement_color(new_color)

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

    def insert_character(self, idx: int, ch: Character) -> None:
        """在指定位置前插入字符。"""
        if idx < 0 or idx > len(self.characters):
            raise ValidationError(
                f"字符索引 {idx} 超出范围 [0, {len(self.characters)}]"
            )

        if not self.characters:
            ch.is_line_end = True

        self.characters.insert(idx, ch)

    def delete_character(self, idx: int) -> bool:
        """删除指定字符，返回删除后该行是否为空。"""
        if idx < 0 or idx >= len(self.characters):
            raise ValidationError(
                f"字符索引 {idx} 超出范围 [0, {len(self.characters)})"
            )

        removed = self.characters.pop(idx)

        if not self.characters:
            return True

        if removed.is_line_end:
            promote_idx = idx - 1 if idx > 0 else len(self.characters) - 1
            self.characters[promote_idx].is_line_end = True

        if idx > 0 and idx - 1 < len(self.characters):
            prev = self.characters[idx - 1]
            if prev.linked_to_next and idx - 1 >= len(self.characters) - 1:
                prev.linked_to_next = False

        return False

    def toggle_sentence_end(self, idx: int) -> None:
        """切换指定字符的句尾标记。"""
        char = self.get_character(idx)
        if not char:
            raise ValidationError(
                f"字符索引 {idx} 超出范围 [0, {len(self.characters)})"
            )

        if char.is_sentence_end:
            char.clear_sentence_end_ts()
        char.is_sentence_end = not char.is_sentence_end

    def add_checkpoint(self, idx: int) -> None:
        """增加指定字符的普通节奏点。"""
        char = self.get_character(idx)
        if not char:
            raise ValidationError(
                f"字符索引 {idx} 超出范围 [0, {len(self.characters)})"
            )
        char.check_count += 1

    def remove_checkpoint(self, idx: int) -> None:
        """减少指定字符的普通节奏点。"""
        char = self.get_character(idx)
        if not char:
            raise ValidationError(
                f"字符索引 {idx} 超出范围 [0, {len(self.characters)})"
            )

        char.check_count = max(0, char.check_count - 1)
        if len(char.timestamps) > char.check_count:
            char.timestamps = char.timestamps[: char.check_count]
            char._update_offset_timestamps()
            char.push_to_ruby()

    def split_at(self, idx: int) -> "Sentence":
        """在指定字符后断行，返回后半句。"""
        if idx < 0 or idx >= len(self.characters):
            raise ValidationError(
                f"字符索引 {idx} 超出范围 [0, {len(self.characters)})"
            )

        current_chars = self.characters[: idx + 1]
        moved_chars = self.characters[idx + 1 :]
        self.characters = current_chars

        split_char = self.characters[idx]
        split_char.is_line_end = True
        split_char.linked_to_next = False

        if moved_chars:
            moved_singer_id = moved_chars[0].singer_id or split_char.singer_id or self.singer_id
            if not any(ch.is_line_end for ch in moved_chars):
                moved_chars[-1].is_line_end = True
            return Sentence(singer_id=moved_singer_id, characters=moved_chars)

        moved_singer_id = split_char.singer_id or self.singer_id
        return Sentence(singer_id=moved_singer_id, characters=[])

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
        return char.all_timestamps if char else []

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
        done = sum(
            len(c.timestamps) + (1 if c.sentence_end_ts is not None else 0)
            for c in self.characters
        )
        total = sum(c.total_timing_points for c in self.characters)
        return done, total

    @property
    def timing_start_ms(self) -> Optional[int]:
        """句子最早时间戳（毫秒），如果无时间标签返回 None"""
        all_ts = [ts for c in self.characters for ts in c.all_timestamps]
        return min(all_ts) if all_ts else None

    @property
    def timing_end_ms(self) -> Optional[int]:
        """句子最晚时间戳（毫秒），如果无时间标签返回 None"""
        all_ts = [ts for c in self.characters for ts in c.all_timestamps]
        return max(all_ts) if all_ts else None

    @property
    def export_timing_start_ms(self) -> Optional[int]:
        """句子最早导出时间戳（含偏移），如果无时间标签返回 None"""
        all_ts = [ts for c in self.characters for ts in c.all_export_timestamps]
        return min(all_ts) if all_ts else None

    @property
    def export_timing_end_ms(self) -> Optional[int]:
        """句子最晚导出时间戳（含偏移），如果无时间标签返回 None"""
        all_ts = [ts for c in self.characters for ts in c.all_export_timestamps]
        return max(all_ts) if all_ts else None

    @property
    def has_timetags(self) -> bool:
        """是否有任何时间标签"""
        return any(c.all_timestamps for c in self.characters)

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
        最后一个字符标记为句尾（check_count=1）。

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
                    check_count=1,
                    is_line_end=is_last,
                    is_sentence_end=is_last,
                    singer_id=singer_id,
                )
            )

        kwargs = {"singer_id": singer_id, "characters": characters}
        if id is not None:
            kwargs["id"] = id
        return cls(**kwargs)
