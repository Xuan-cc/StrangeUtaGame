"""命令模式基础类。

实现撤销/重做机制的核心基础设施。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class Command(ABC):
    """命令抽象基类

    所有可撤销/重做的操作都继承此类。
    """

    @abstractmethod
    def execute(self) -> None:
        """执行命令"""
        pass

    @abstractmethod
    def undo(self) -> None:
        """撤销命令"""
        pass

    def redo(self) -> None:
        """重做命令（默认与 execute 相同）

        子类可以覆盖此方法以实现特殊的重做逻辑。
        """
        self.execute()

    @property
    @abstractmethod
    def description(self) -> str:
        """命令描述（用于 UI 显示）"""
        pass


class BatchCommand(Command):
    """批量命令

    将多个命令组合成一个原子操作。
    批量执行和批量撤销。
    """

    def __init__(self, commands: list[Command], description: str):
        """
        Args:
            commands: 命令列表
            description: 批量操作描述
        """
        self.commands = commands
        self._description = description

    def execute(self) -> None:
        """批量执行所有命令"""
        for cmd in self.commands:
            cmd.execute()

    def undo(self) -> None:
        """批量撤销（逆序）"""
        for cmd in reversed(self.commands):
            cmd.undo()

    @property
    def description(self) -> str:
        return self._description


@dataclass
class CommandState:
    """命令状态快照

    用于保存命令执行前的状态，以便撤销时恢复。
    """

    data: dict

    @classmethod
    def capture(cls, obj) -> "CommandState":
        """捕获对象当前状态

        Args:
            obj: 要捕获状态的对象

        Returns:
            状态快照
        """
        # 简单实现：假设对象有 to_dict 方法
        if hasattr(obj, "to_dict"):
            return cls(data=obj.to_dict())
        return cls(data={})
