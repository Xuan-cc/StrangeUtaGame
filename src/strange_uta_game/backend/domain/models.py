"""领域层核心数据结构定义。

数据层次（自底向上）：
  Ruby → Character → Word → Sentence → Project

- Ruby:      最小单元，存储假名文本；checkpoint 时间戳和演唱者由母对象推送
- Character: 主要单元，存储单字、Ruby、节奏点配置、时间戳、连词/句尾标记、演唱者
- Word:      逻辑单元，由连词字符组成；不存储 Ruby，但收集字符的 Ruby 用于渲染和输出
"""

from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum, auto


# ──────────────────────────────────────────────
# 错误类
# ──────────────────────────────────────────────


class DomainError(Exception):
    """领域层错误基类"""

    pass


class ValidationError(DomainError):
    """验证错误"""

    pass


# ──────────────────────────────────────────────
# 枚举
# ──────────────────────────────────────────────


class TimeTagType(Enum):
    """时间标签类型（用于导出兼容）

    在新层次模型中，tag_type 由上下文推导：
      - CHAR_START   : Character.timestamps[0]
      - CHAR_MIDDLE  : Character.timestamps[1:]（非句尾字符）
      - LINE_END     : is_line_end 字符的最后一个 timestamp
      - SENTENCE_END : is_sentence_end 字符的最后一个 timestamp
      - REST         : is_rest 字符的 timestamp
    """

    CHAR_START = auto()
    CHAR_MIDDLE = auto()
    LINE_END = auto()
    SENTENCE_END = auto()
    REST = auto()


# ──────────────────────────────────────────────
# Ruby — 最小数据结构单元
# ──────────────────────────────────────────────


@dataclass
class Ruby:
    """注音/振り仮名 — 最小数据结构单元

    存储 Ruby 的假名文本。除假名文本以外的所有属性均由母对象
    (Character) 推送更新，用于 Ruby 绘制和 Ruby 输出。

    Attributes:
        text: 注音文本（如 "あか"、"きのう"）
        timestamps: checkpoint 时间戳列表（从 Character 推送，毫秒）
        singer_id: 演唱者 ID（从 Character 推送）

    Example:
        >>> ruby = Ruby(text="あか")
        >>> ruby.text
        'あか'
    """

    text: str
    timestamps: List[int] = field(default_factory=list)
    singer_id: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            raise ValidationError("注音文本不能为空")
        if "#" in self.text and any(not group for group in self.text.split("#")):
            raise ValidationError(f"Ruby 分组存在空组: {self.text!r}")

    def groups(self) -> List[str]:
        """按 '#' 分组返回；无 '#' 时返回单组。"""
        return self.text.split("#") if "#" in self.text else [self.text]

    def group_count(self) -> int:
        """返回 Ruby 分组数量。"""
        return len(self.groups()) if self.text else 0

    def display_text(self) -> str:
        """渲染/导出层使用的用户可见文本（剥离内部 '#' 分组标记）。

        '#' 仅是内部存储用于标记 checkpoint 切分的分组分隔符，
        任何面向用户的场景（karaoke 预览、playback 高亮、tooltip、
        导入预览表格、Nicokara/txt 导出等）都应使用本方法而非直接读 text。
        仅 inline_format 导出与编辑对话框原样保留 '#'。
        """
        return self.text.replace("#", "")


# ──────────────────────────────────────────────
# Character — 主要数据结构单元
# ──────────────────────────────────────────────


@dataclass
class Character:
    """字符 — 主要数据结构单元

    存储单个字符及其注音、节奏点配置、时间戳、连词/句尾标记、演唱者。
    当时间戳或演唱者更新时，主动将变更推送给 Ruby 对象。

    Attributes:
        char: 单个字符（如 "赤"、"い"）
        ruby: 注音对象，可以为空
        check_count: 节奏点数量（需要击打几次，默认 1，可以为 0）
        timestamps: checkpoint 时间戳列表（毫秒），索引 = checkpoint_idx
        linked_to_next: 是否与下一字符连词
        is_line_end: 是否是行尾字符（行级换行标记，一行只有一个）
        is_sentence_end: 是否是句尾字符（句尾标记，一行内可有多个，额外 +1 checkpoint）
        is_rest: 是否是休止符
        singer_id: 演唱者 UUID

    Example:
        >>> ch = Character(char="赤", check_count=2, singer_id="singer_1")
        >>> ch.set_ruby(Ruby(text="あか"))
        >>> ch.add_timestamp(1000)
        >>> ch.ruby.timestamps
        [1000]
    """

    char: str
    ruby: Optional[Ruby] = None
    check_count: int = 1
    timestamps: List[int] = field(default_factory=list)
    sentence_end_ts: Optional[int] = None
    linked_to_next: bool = False
    is_line_end: bool = False
    is_sentence_end: bool = False
    is_rest: bool = False
    singer_id: str = ""

    # 渲染/导出偏移量（内部管理，不参与构造和序列化）
    _render_offset_ms: int = field(default=0, init=False, repr=False)
    _export_offset_ms: int = field(default=0, init=False, repr=False)

    # 派生偏移时间戳（自动维护，不参与构造和序列化）
    render_timestamps: List[int] = field(default_factory=list, init=False, repr=False)
    render_sentence_end_ts: Optional[int] = field(default=None, init=False, repr=False)
    export_timestamps: List[int] = field(default_factory=list, init=False, repr=False)
    export_sentence_end_ts: Optional[int] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.char:
            raise ValidationError("字符不能为空")
        if self.check_count < 0:
            raise ValidationError(f"节奏点数量不能为负数: {self.check_count}")
        # 初始化渲染/导出偏移时间戳（确保从文件加载的 Character 也有正确的派生数据）
        self._update_offset_timestamps()

    # ── 时间戳管理 ──

    def push_to_ruby(self) -> None:
        """将自己的时间戳和演唱者推送给 Ruby 对象"""
        if self.ruby:
            self.ruby.timestamps = self.all_timestamps
            self.ruby.singer_id = self.singer_id

    def add_timestamp(self, timestamp_ms: int, checkpoint_idx: int = -1) -> None:
        """添加时间戳

        Args:
            timestamp_ms: 时间戳（毫秒）
            checkpoint_idx: 指定插入位置（-1 = 追加到末尾并排序）
        """
        if timestamp_ms < 0:
            raise ValidationError(f"时间戳不能为负数: {timestamp_ms}")
        if checkpoint_idx >= self.check_count:
            raise ValidationError("普通节奏点索引超出范围")
        if checkpoint_idx >= 0:
            # 指定位置插入
            while len(self.timestamps) <= checkpoint_idx:
                self.timestamps.append(0)
            self.timestamps[checkpoint_idx] = timestamp_ms
        else:
            self.timestamps.append(timestamp_ms)
            self.timestamps.sort()
        self._update_offset_timestamps()
        self.push_to_ruby()

    def remove_timestamp_at(self, checkpoint_idx: int) -> Optional[int]:
        """移除指定 checkpoint_idx 的时间戳

        Args:
            checkpoint_idx: checkpoint 索引

        Returns:
            被移除的时间戳，如果索引无效返回 None
        """
        if checkpoint_idx >= self.check_count:
            return None
        if 0 <= checkpoint_idx < len(self.timestamps):
            removed = self.timestamps.pop(checkpoint_idx)
            self._update_offset_timestamps()
            self.push_to_ruby()
            return removed
        return None

    def clear_timestamps(self) -> None:
        """清空所有时间戳"""
        self.timestamps.clear()
        self.sentence_end_ts = None
        self._update_offset_timestamps()
        self.push_to_ruby()

    def set_sentence_end_ts(self, ts: int) -> None:
        """设置句尾释放时间戳"""
        if ts < 0:
            raise ValidationError(f"时间戳不能为负数: {ts}")
        if not self.is_sentence_end:
            raise ValidationError("当前字符不是句尾字符")
        self.sentence_end_ts = ts
        self._update_offset_timestamps()
        self.push_to_ruby()

    def clear_sentence_end_ts(self) -> None:
        """清除句尾释放时间戳"""
        self.sentence_end_ts = None
        self._update_offset_timestamps()
        self.push_to_ruby()

    def get_timestamp(self, checkpoint_idx: int) -> Optional[int]:
        """获取指定 checkpoint_idx 的时间戳"""
        if 0 <= checkpoint_idx < len(self.timestamps):
            return self.timestamps[checkpoint_idx]
        return None

    # ── Ruby 管理 ──

    def set_ruby(self, ruby: Optional[Ruby]) -> None:
        """设置 Ruby 并推送当前时间戳和演唱者"""
        self.ruby = ruby
        self.push_to_ruby()

    # ── 查询 ──

    @property
    def is_fully_timed(self) -> bool:
        """检查是否所有节奏点都已打轴"""
        normal_done = len(self.timestamps) >= self.check_count
        if not normal_done:
            return False
        if self.is_sentence_end:
            return self.sentence_end_ts is not None
        return True

    @property
    def total_timing_points(self) -> int:
        """总打轴点数（普通 checkpoint + 句尾释放点）"""
        return self.check_count + (1 if self.is_sentence_end else 0)

    @property
    def all_timestamps(self) -> List[int]:
        """按打轴顺序返回所有时间戳（只读视图）"""
        sentence_end = (
            [self.sentence_end_ts]
            if self.is_sentence_end and self.sentence_end_ts is not None
            else []
        )
        return list(self.timestamps) + sentence_end

    @property
    def has_ruby(self) -> bool:
        """是否有注音"""
        return self.ruby is not None

    def get_tag_type(self, checkpoint_idx: int) -> TimeTagType:
        """根据上下文推导时间标签类型

        Args:
            checkpoint_idx: checkpoint 索引

        Returns:
            推导出的 TimeTagType
        """
        if self.is_rest:
            return TimeTagType.REST
        if self.is_sentence_end and checkpoint_idx == self.total_timing_points - 1:
            return TimeTagType.SENTENCE_END
        if self.is_line_end and checkpoint_idx == self.check_count - 1:
            return TimeTagType.LINE_END
        if checkpoint_idx == 0:
            return TimeTagType.CHAR_START
        return TimeTagType.CHAR_MIDDLE

    # ── 偏移时间戳管理 ──

    def set_offsets(self, render_offset_ms: int, export_offset_ms: int) -> None:
        """设置渲染/导出偏移量并重新计算派生时间戳

        Args:
            render_offset_ms: 渲染偏移量（毫秒），负值=提前渲染
            export_offset_ms: 导出偏移量（毫秒），负值=提前导出
        """
        self._render_offset_ms = render_offset_ms
        self._export_offset_ms = export_offset_ms
        self._update_offset_timestamps()

    def _update_offset_timestamps(self) -> None:
        """根据基础时间戳和偏移量重新计算渲染/导出时间戳"""
        self.render_timestamps = [
            max(0, ts + self._render_offset_ms) for ts in self.timestamps
        ]
        self.export_timestamps = [
            max(0, ts + self._export_offset_ms) for ts in self.timestamps
        ]
        self.render_sentence_end_ts = (
            max(0, self.sentence_end_ts + self._render_offset_ms)
            if self.sentence_end_ts is not None
            else None
        )
        self.export_sentence_end_ts = (
            max(0, self.sentence_end_ts + self._export_offset_ms)
            if self.sentence_end_ts is not None
            else None
        )

    @property
    def all_render_timestamps(self) -> List[int]:
        """按打轴顺序返回所有渲染时间戳"""
        sentence_end = (
            [self.render_sentence_end_ts]
            if self.is_sentence_end and self.render_sentence_end_ts is not None
            else []
        )
        return list(self.render_timestamps) + sentence_end

    @property
    def all_export_timestamps(self) -> List[int]:
        """按打轴顺序返回所有导出时间戳"""
        sentence_end = (
            [self.export_sentence_end_ts]
            if self.is_sentence_end and self.export_sentence_end_ts is not None
            else []
        )
        return list(self.export_timestamps) + sentence_end


# ──────────────────────────────────────────────
# Word — 逻辑单元（由连词字符组成）
# ──────────────────────────────────────────────


@dataclass
class Word:
    """词语 — 由连词字符组成的逻辑单元

    通过日语词典、英语词典自动语义分割，或用户手动 F3 toggle。
    如果字符的 linked_to_next=True，则与下一字符组成同一词语；
    如果没有连词，单个字符即为一个词语。

    Word 不存储 Ruby，但会把字符的 Ruby 收集起来，
    用于绘制连词框、解析和最终输出（逗号分隔）。

    Attributes:
        characters: 组成该词语的字符列表
    """

    characters: List[Character] = field(default_factory=list)

    @property
    def text(self) -> str:
        """词语文本"""
        return "".join(c.char for c in self.characters)

    @property
    def ruby_parts(self) -> List[str]:
        """各字符 Ruby 的分组文本列表（按 checkpoint 顺序展开）。"""
        return [group for c in self.characters if c.ruby for group in c.ruby.groups()]

    @property
    def ruby_text(self) -> str:
        """合并的 Ruby 文本（用于渲染连词框）"""
        return "".join(self.ruby_parts)

    @property
    def ruby_csv(self) -> str:
        """逗号分隔的 Ruby 文本（用于输出）"""
        return ",".join(self.ruby_parts)

    @property
    def has_ruby(self) -> bool:
        """词语中是否包含 Ruby"""
        return any(c.ruby for c in self.characters)

    @property
    def char_count(self) -> int:
        """字符数量"""
        return len(self.characters)
