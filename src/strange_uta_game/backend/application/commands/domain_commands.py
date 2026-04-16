"""领域层相关的具体命令实现。"""

from typing import Optional
from strange_uta_game.backend.domain import (
    Project,
    LyricLine,
    TimeTag,
    CheckpointConfig,
    Ruby,
)
from .base import Command


class AddTimeTagCommand(Command):
    """添加时间标签命令"""

    def __init__(
        self,
        project: Project,
        line_id: str,
        timestamp_ms: int,
        char_idx: int,
        checkpoint_idx: int = 0,
    ):
        self.project = project
        self.line_id = line_id
        self.timestamp_ms = timestamp_ms
        self.char_idx = char_idx
        self.checkpoint_idx = checkpoint_idx
        self._tag: Optional[TimeTag] = None

    def execute(self) -> None:
        line = self.project.get_line(self.line_id)
        if not line:
            raise ValueError(f"行 {self.line_id} 不存在")

        singer_id = line.singer_id
        self._tag = TimeTag(
            timestamp_ms=self.timestamp_ms,
            singer_id=singer_id,
            char_idx=self.char_idx,
            checkpoint_idx=self.checkpoint_idx,
        )
        line.add_timetag(self._tag)

    def undo(self) -> None:
        if self._tag:
            line = self.project.get_line(self.line_id)
            if line:
                line.remove_timetag(self._tag)

    @property
    def description(self) -> str:
        return f"添加时间标签 [{self.timestamp_ms}ms]"


class RemoveTimeTagCommand(Command):
    """删除时间标签命令"""

    def __init__(self, project: Project, line_id: str, tag: TimeTag):
        self.project = project
        self.line_id = line_id
        self.tag = tag
        self._removed = False

    def execute(self) -> None:
        line = self.project.get_line(self.line_id)
        if not line:
            raise ValueError(f"行 {self.line_id} 不存在")

        line.remove_timetag(self.tag)
        self._removed = True

    def undo(self) -> None:
        if self._removed:
            line = self.project.get_line(self.line_id)
            if line:
                line.add_timetag(self.tag)

    @property
    def description(self) -> str:
        return f"删除时间标签 [{self.tag.timestamp_ms}ms]"


class SetCheckpointConfigCommand(Command):
    """设置节奏点配置命令"""

    def __init__(self, project: Project, line_id: str, config: CheckpointConfig):
        self.project = project
        self.line_id = line_id
        self.new_config = config
        self._old_config: Optional[CheckpointConfig] = None

    def execute(self) -> None:
        line = self.project.get_line(self.line_id)
        if not line:
            raise ValueError(f"行 {self.line_id} 不存在")

        self._old_config = line.get_checkpoint_config(self.new_config.char_idx)
        line.set_checkpoint_config(self.new_config)

    def undo(self) -> None:
        if self._old_config:
            line = self.project.get_line(self.line_id)
            if line:
                line.set_checkpoint_config(self._old_config)

    @property
    def description(self) -> str:
        return f"修改节奏点配置 (char_idx={self.new_config.char_idx})"


class AddRubyCommand(Command):
    """添加注音命令"""

    def __init__(self, project: Project, line_id: str, ruby: Ruby):
        self.project = project
        self.line_id = line_id
        self.ruby = ruby

    def execute(self) -> None:
        line = self.project.get_line(self.line_id)
        if not line:
            raise ValueError(f"行 {self.line_id} 不存在")

        line.add_ruby(self.ruby)

    def undo(self) -> None:
        # Ruby 没有 remove 方法，这里需要从 rubies 列表移除
        line = self.project.get_line(self.line_id)
        if line and self.ruby in line.rubies:
            line.rubies.remove(self.ruby)

    @property
    def description(self) -> str:
        return f"添加注音 [{self.ruby.text}]"


class AddLineCommand(Command):
    """添加歌词行命令"""

    def __init__(self, project: Project, line: LyricLine):
        self.project = project
        self.line = line
        self._added = False

    def execute(self) -> None:
        self.project.add_line(self.line)
        self._added = True

    def undo(self) -> None:
        if self._added:
            try:
                self.project.remove_line(self.line.id)
            except Exception:
                pass  # 可能已经被删除

    @property
    def description(self) -> str:
        return f"添加歌词行 [{self.line.text[:10]}...]"


class RemoveLineCommand(Command):
    """删除歌词行命令"""

    def __init__(self, project: Project, line_id: str):
        self.project = project
        self.line_id = line_id
        self._line: Optional[LyricLine] = None
        self._index: int = -1

    def execute(self) -> None:
        self._line = self.project.get_line(self.line_id)
        if not self._line:
            raise ValueError(f"行 {self.line_id} 不存在")

        # 保存位置
        self._index = self.project.lines.index(self._line)

        self.project.remove_line(self.line_id)

    def undo(self) -> None:
        if self._line:
            # 恢复到原来的位置
            if self._index >= 0 and self._index <= len(self.project.lines):
                # 手动插入到指定位置
                self.project.lines.insert(self._index, self._line)
            else:
                self.project.add_line(self._line)

    @property
    def description(self) -> str:
        return f"删除歌词行"


class AddSingerCommand(Command):
    """添加演唱者命令"""

    def __init__(self, project, singer):
        self.project = project
        self.singer = singer

    def execute(self) -> None:
        self.project.add_singer(self.singer)

    def undo(self) -> None:
        try:
            self.project.remove_singer(self.singer.id)
        except Exception:
            pass

    @property
    def description(self) -> str:
        return f"添加演唱者 [{self.singer.name}]"


class RemoveSingerCommand(Command):
    """删除演唱者命令"""

    def __init__(self, project, singer_id, transfer_to=None):
        self.project = project
        self.singer_id = singer_id
        self.transfer_to = transfer_to
        self._singer = None
        self._lines = []

    def execute(self) -> None:
        self._singer = self.project.get_singer(self.singer_id)
        if self._singer:
            # 保存该演唱者的所有歌词
            self._lines = [
                line for line in self.project.lines if line.singer_id == self.singer_id
            ]

            self.project.remove_singer(self.singer_id, self.transfer_to)

    def undo(self) -> None:
        # 撤销删除比较复杂，需要恢复演唱者和歌词
        # 这里简化处理：重新添加演唱者，然后恢复歌词
        if self._singer:
            self.project.add_singer(self._singer)
            for line in self._lines:
                # 恢复 singer_id
                line.singer_id = self.singer_id
                if line not in self.project.lines:
                    self.project.add_line(line)

    @property
    def description(self) -> str:
        return f"删除演唱者"
