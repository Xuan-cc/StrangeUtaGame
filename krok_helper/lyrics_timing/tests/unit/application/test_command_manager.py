"""CommandManager 测试。"""

import pytest
from strange_uta_game.backend.application import CommandManager, Command


class SimpleCommand(Command):
    """测试用简单命令"""

    def __init__(self, value: int):
        self.value = value
        self.executed = False
        self.undone = False

    def execute(self) -> None:
        self.executed = True

    def undo(self) -> None:
        self.undone = True

    @property
    def description(self) -> str:
        return f"Command {self.value}"


class TestCommandManager:
    """测试命令管理器"""

    def test_execute_command(self):
        manager = CommandManager()
        cmd = SimpleCommand(1)

        manager.execute(cmd)

        assert cmd.executed
        assert manager.can_undo()
        assert not manager.can_redo()

    def test_undo(self):
        manager = CommandManager()
        cmd = SimpleCommand(1)

        manager.execute(cmd)
        desc = manager.undo()

        assert cmd.undone
        assert desc == "Command 1"
        assert not manager.can_undo()
        assert manager.can_redo()

    def test_redo(self):
        manager = CommandManager()
        cmd = SimpleCommand(1)

        manager.execute(cmd)
        manager.undo()

        # 重新执行
        desc = manager.redo()

        assert cmd.executed  # redo 调用 execute
        assert desc == "Command 1"
        assert manager.can_undo()
        assert not manager.can_redo()

    def test_undo_empty_stack(self):
        manager = CommandManager()

        desc = manager.undo()

        assert desc is None

    def test_redo_empty_stack(self):
        manager = CommandManager()

        desc = manager.redo()

        assert desc is None

    def test_clear_redo_on_new_execute(self):
        """测试新命令执行后清空重做栈"""
        manager = CommandManager()

        cmd1 = SimpleCommand(1)
        cmd2 = SimpleCommand(2)

        manager.execute(cmd1)
        manager.undo()  # 撤销 cmd1

        assert manager.can_redo()  # 可以重做 cmd1

        manager.execute(cmd2)  # 执行新命令

        assert not manager.can_redo()  # 重做栈被清空

    def test_max_history(self):
        """测试最大历史记录限制"""
        manager = CommandManager(max_history=3)

        # 执行 5 个命令，但只保留最近 3 个
        for i in range(5):
            manager.execute(SimpleCommand(i))

        assert manager.get_undo_stack_size() == 3

    def test_clear(self):
        """测试清空所有历史"""
        manager = CommandManager()

        manager.execute(SimpleCommand(1))
        manager.undo()

        assert manager.can_undo() or manager.can_redo()

        manager.clear()

        assert not manager.can_undo()
        assert not manager.can_redo()

    def test_get_descriptions(self):
        """测试获取命令描述"""
        manager = CommandManager()

        manager.execute(SimpleCommand(1))
        manager.execute(SimpleCommand(2))

        assert manager.get_undo_description() == "Command 2"

        manager.undo()

        assert manager.get_undo_description() == "Command 1"
        assert manager.get_redo_description() == "Command 2"

    def test_state_changed_callback(self):
        """测试状态变更回调"""
        callback_count = 0

        def on_state_changed():
            nonlocal callback_count
            callback_count += 1

        manager = CommandManager()
        manager.set_on_state_changed(on_state_changed)

        manager.execute(SimpleCommand(1))

        assert callback_count == 1

        manager.undo()

        assert callback_count == 2
