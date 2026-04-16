"""命令管理器 - 实现撤销/重做机制。"""

from typing import List, Optional, Callable
from .commands.base import Command, BatchCommand


class CommandManager:
    """命令管理器

    维护撤销栈和重做栈，支持命令的撤销和重做。
    支持状态变更回调。
    """

    def __init__(self, max_history: int = 100):
        """
        Args:
            max_history: 最大历史记录数量
        """
        self._undo_stack: List[Command] = []
        self._redo_stack: List[Command] = []
        self._max_history = max_history

        # 状态变更回调
        self._on_state_changed: Optional[Callable[[], None]] = None

    def set_on_state_changed(self, callback: Callable[[], None]) -> None:
        """设置状态变更回调

        Args:
            callback: 状态变更时调用的函数
        """
        self._on_state_changed = callback

    def execute(self, command: Command) -> None:
        """执行命令

        执行命令并将其加入撤销栈，清空重做栈。

        Args:
            command: 要执行的命令
        """
        # 执行命令
        command.execute()

        # 加入撤销栈
        self._undo_stack.append(command)

        # 清空重做栈（新操作后不能再重做之前的操作）
        self._redo_stack.clear()

        # 限制历史记录数量
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)

        # 触发状态变更回调
        self._notify_state_changed()

    def undo(self) -> Optional[str]:
        """撤销上一个命令

        Returns:
            撤销的命令描述，如果没有可撤销的命令则返回 None
        """
        if not self._undo_stack:
            return None

        command = self._undo_stack.pop()
        command.undo()

        # 加入重做栈
        self._redo_stack.append(command)

        self._notify_state_changed()

        return command.description

    def redo(self) -> Optional[str]:
        """重做下一个命令

        Returns:
            重做的命令描述，如果没有可重做的命令则返回 None
        """
        if not self._redo_stack:
            return None

        command = self._redo_stack.pop()
        command.redo()

        # 加入撤销栈
        self._undo_stack.append(command)

        self._notify_state_changed()

        return command.description

    def can_undo(self) -> bool:
        """是否可以撤销"""
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        """是否可以重做"""
        return len(self._redo_stack) > 0

    def get_undo_description(self) -> Optional[str]:
        """获取下一个可撤销命令的描述"""
        if not self._undo_stack:
            return None
        return self._undo_stack[-1].description

    def get_redo_description(self) -> Optional[str]:
        """获取下一个可重做命令的描述"""
        if not self._redo_stack:
            return None
        return self._redo_stack[-1].description

    def clear(self) -> None:
        """清空所有历史记录"""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._notify_state_changed()

    def clear_redo_stack(self) -> None:
        """清空重做栈

        通常在项目切换或重要操作后调用。
        """
        self._redo_stack.clear()
        self._notify_state_changed()

    def get_undo_stack_size(self) -> int:
        """获取撤销栈大小"""
        return len(self._undo_stack)

    def get_redo_stack_size(self) -> int:
        """获取重做栈大小"""
        return len(self._redo_stack)

    def get_undo_stack_descriptions(self, count: int = 10) -> List[str]:
        """获取撤销栈中最近命令的描述列表

        Args:
            count: 返回的最大数量

        Returns:
            命令描述列表（最新的在前）
        """
        return [cmd.description for cmd in reversed(self._undo_stack[-count:])]

    def execute_batch(self, commands: List[Command], description: str) -> None:
        """批量执行命令

        将多个命令包装为一个批量命令执行。

        Args:
            commands: 命令列表
            description: 批量操作描述
        """
        batch = BatchCommand(commands, description)
        self.execute(batch)

    def _notify_state_changed(self) -> None:
        """触发状态变更回调"""
        if self._on_state_changed:
            self._on_state_changed()
