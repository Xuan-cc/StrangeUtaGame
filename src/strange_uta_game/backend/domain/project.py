"""Project 聚合根定义。

Project 作为聚合根，管理所有歌词行和演唱者的一致性。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from uuid import uuid4
from datetime import datetime

from .models import DomainError, ValidationError, TimeTag
from .entities import Singer, LyricLine


@dataclass
class ProjectMetadata:
    """项目元数据

    Attributes:
        title: 歌曲标题
        artist: 艺术家
        album: 专辑
        language: 语言代码（如 "ja", "zh", "en"）
        created_at: 创建时间
        updated_at: 更新时间
    """

    title: str = ""
    artist: str = ""
    album: str = ""
    language: str = "ja"  # 默认日语
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "language": self.language,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectMetadata":
        """从字典创建"""
        return cls(
            title=data.get("title", ""),
            artist=data.get("artist", ""),
            album=data.get("album", ""),
            language=data.get("language", "ja"),
            created_at=datetime.fromisoformat(data.get("created_at", "")),
            updated_at=datetime.fromisoformat(data.get("updated_at", "")),
        )


@dataclass
class Project:
    """项目聚合根

    作为聚合根，管理所有歌词行和演唱者的一致性。

    Attributes:
        id: 唯一标识符（UUID）
        lines: 歌词行列表（所有演唱者的歌词混合存储，通过 singer_id 关联）
        singers: 演唱者列表
        metadata: 元数据（标题、艺术家、语言等）
        audio_duration_ms: 音频时长（毫秒，用于验证用户选择的音频是否匹配）

    Business Rules:
        1. 歌词行按时间顺序排列（可选，允许乱序以支持和声）
        2. 每行有唯一 ID，并关联到特定演唱者（singer_id 必填）
        3. 必须至少有一个演唱者（default = true）
        4. 所有时间标签必须携带 singer_id，确保多演唱者场景数据完整
        5. 不存储音频路径 - 音频由用户每次使用时单独选择

    Example:
        >>> project = Project()
        >>> project.add_singer(Singer(name="初音ミク", color="#FF6B6B"))
        >>> singer = project.singers[0]
        >>> project.add_line(LyricLine(singer_id=singer.id, text="测试歌词"))
        >>> len(project.lines)
        1
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    lines: List[LyricLine] = field(default_factory=list)
    singers: List[Singer] = field(default_factory=list)
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    audio_duration_ms: int = 0

    def __post_init__(self) -> None:
        """验证并初始化"""
        if not self.id:
            raise ValidationError("项目ID不能为空")

        # 确保至少有一个默认演唱者
        if not self.singers:
            default_singer = Singer(
                name="未命名",
                color="#FF6B6B",
                is_default=True,
                display_priority=0,
                backend_number=1,
            )
            self.singers.append(default_singer)

        # 确保只有一个默认演唱者
        default_singers = [s for s in self.singers if s.is_default]
        if len(default_singers) > 1:
            raise ValidationError("只能有一个默认演唱者")

    # ==================== 演唱者管理 ====================

    def add_singer(self, singer: Singer) -> None:
        """添加演唱者

        Args:
            singer: 演唱者对象
        """
        # 如果新演唱者是默认演唱者，取消现有默认
        if singer.is_default:
            for s in self.singers:
                s.is_default = False

        self.singers.append(singer)
        # 按显示优先级排序
        self.singers.sort(key=lambda s: s.display_priority)
        self._update_timestamp()

    def remove_singer(self, singer_id: str, transfer_to: Optional[str] = None) -> None:
        """删除演唱者

        Args:
            singer_id: 要删除的演唱者ID
            transfer_to: 将其歌词转移到的演唱者ID（如果为 None 则级联删除歌词）

        Raises:
            ValidationError: 如果试图删除最后一个演唱者
            DomainError: 如果演唱者不存在
        """
        if len(self.singers) <= 1:
            raise ValidationError("必须至少保留一个演唱者")

        singer = self.get_singer(singer_id)
        if not singer:
            raise DomainError(f"演唱者 {singer_id} 不存在")

        if transfer_to:
            # 转移歌词到另一个演唱者
            target = self.get_singer(transfer_to)
            if not target:
                raise DomainError(f"目标演唱者 {transfer_to} 不存在")

            for line in self.lines:
                if line.singer_id == singer_id:
                    line.singer_id = transfer_to
        else:
            # 级联删除该演唱者的所有歌词
            self.lines = [l for l in self.lines if l.singer_id != singer_id]

        self.singers = [s for s in self.singers if s.id != singer_id]
        self._update_timestamp()

    def get_singer(self, singer_id: str) -> Optional[Singer]:
        """根据ID获取演唱者

        Args:
            singer_id: 演唱者ID

        Returns:
            演唱者对象，如果不存在则返回 None
        """
        for singer in self.singers:
            if singer.id == singer_id:
                return singer
        return None

    def get_default_singer(self) -> Singer:
        """获取默认演唱者

        Returns:
            默认演唱者

        Raises:
            DomainError: 如果没有默认演唱者
        """
        for singer in self.singers:
            if singer.is_default:
                return singer
        raise DomainError("没有默认演唱者")

    # ==================== 歌词行管理 ====================

    def add_line(self, line: LyricLine, after_line_id: Optional[str] = None) -> None:
        """添加歌词行

        Args:
            line: 歌词行对象
            after_line_id: 插入到指定行之后（如果为 None 则添加到末尾）

        Raises:
            ValidationError: 如果 singer_id 无效
        """
        # 验证 singer_id
        if not self.get_singer(line.singer_id):
            raise ValidationError(f"演唱者 {line.singer_id} 不存在，无法添加歌词行")

        if after_line_id:
            # 插入到指定位置
            for i, l in enumerate(self.lines):
                if l.id == after_line_id:
                    self.lines.insert(i + 1, line)
                    break
            else:
                # 未找到指定行，添加到末尾
                self.lines.append(line)
        else:
            # 添加到末尾
            self.lines.append(line)

        self._update_timestamp()

    def remove_line(self, line_id: str) -> None:
        """删除歌词行

        Args:
            line_id: 歌词行ID

        Raises:
            DomainError: 如果歌词行不存在
        """
        original_len = len(self.lines)
        self.lines = [l for l in self.lines if l.id != line_id]

        if len(self.lines) == original_len:
            raise DomainError(f"歌词行 {line_id} 不存在")

        self._update_timestamp()

    def get_line(self, line_id: str) -> Optional[LyricLine]:
        """根据ID获取歌词行

        Args:
            line_id: 歌词行ID

        Returns:
            歌词行对象，如果不存在则返回 None
        """
        for line in self.lines:
            if line.id == line_id:
                return line
        return None

    def get_lines_by_singer(self, singer_id: str) -> List[LyricLine]:
        """获取指定演唱者的所有歌词行

        Args:
            singer_id: 演唱者ID

        Returns:
            该演唱者的歌词行列表
        """
        return [l for l in self.lines if l.singer_id == singer_id]

    def move_line(self, line_id: str, new_position: int) -> None:
        """移动歌词行到指定位置

        Args:
            line_id: 歌词行ID
            new_position: 新位置索引（0-based）
        """
        line = self.get_line(line_id)
        if not line:
            raise DomainError(f"歌词行 {line_id} 不存在")

        if new_position < 0 or new_position >= len(self.lines):
            raise ValidationError(f"位置 {new_position} 超出范围")

        # 移除并重新插入
        self.lines = [l for l in self.lines if l.id != line_id]
        self.lines.insert(new_position, line)
        self._update_timestamp()

    # ==================== 全局查询 ====================

    def get_all_timetags(self) -> List[tuple[str, int, TimeTag]]:
        """获取所有时间标签（用于全局排序和导航）

        Returns:
            列表项: (line_id, line_index, timetag)
        """
        result = []
        for i, line in enumerate(self.lines):
            for tag in line.timetags:
                result.append((line.id, i, tag))
        # 按时间排序
        result.sort(key=lambda x: x[2].timestamp_ms)
        return result

    def get_timing_statistics(self) -> Dict[str, Any]:
        """获取打轴统计信息

        Returns:
            统计信息字典
        """
        total_chars = sum(len(line.chars) for line in self.lines)
        total_timetags = sum(len(line.timetags) for line in self.lines)
        total_checkpoints = sum(
            sum(c.check_count for c in line.checkpoints) for line in self.lines
        )

        completed_lines = sum(1 for line in self.lines if line.is_fully_timed())

        return {
            "total_lines": len(self.lines),
            "total_singers": len(self.singers),
            "total_chars": total_chars,
            "total_timetags": total_timetags,
            "total_checkpoints": total_checkpoints,
            "completed_lines": completed_lines,
            "completion_rate": completed_lines / len(self.lines) if self.lines else 0,
            "timing_progress": f"{total_timetags}/{total_checkpoints}",
        }

    # ==================== 验证 ====================

    def validate(self) -> List[str]:
        """验证项目数据有效性

        Returns:
            错误信息列表（为空表示验证通过）
        """
        errors = []

        # 验证至少有一个演唱者
        if not self.singers:
            errors.append("必须至少有一个演唱者")

        # 验证至少有一个默认演唱者
        if not any(s.is_default for s in self.singers):
            errors.append("必须有一个默认演唱者")

        # 验证所有歌词行的 singer_id 有效
        singer_ids = {s.id for s in self.singers}
        for line in self.lines:
            if line.singer_id not in singer_ids:
                errors.append(f"歌词行 {line.id} 的 singer_id {line.singer_id} 无效")

        return errors

    def is_valid(self) -> bool:
        """检查项目是否有效

        Returns:
            是否通过验证
        """
        return len(self.validate()) == 0

    # ==================== 内部方法 ====================

    def _update_timestamp(self) -> None:
        """更新修改时间"""
        self.metadata.updated_at = datetime.now()
