"""领域层相关的具体命令实现。

所有可撤销操作均通过 Command 模式实现。
命令操作的对象是新层次化领域模型：Sentence → Character → Ruby。
"""

from typing import Optional, Dict, Any
from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Ruby,
)
from .base import Command


class AddTimeTagCommand(Command):
    """添加时间标签命令

    在指定句子的指定字符上添加一个时间戳。
    """

    def __init__(
        self,
        project: Project,
        sentence_id: str,
        char_idx: int,
        timestamp_ms: int,
        checkpoint_idx: int = -1,
    ):
        self.project = project
        self.sentence_id = sentence_id
        self.char_idx = char_idx
        self.timestamp_ms = timestamp_ms
        self.checkpoint_idx = checkpoint_idx
        self._old_timestamps: Optional[list] = None

    def execute(self) -> None:
        sentence = self.project.get_sentence(self.sentence_id)
        if not sentence:
            raise ValueError(f"句子 {self.sentence_id} 不存在")
        char = sentence.get_character(self.char_idx)
        if not char:
            raise ValueError(f"字符索引 {self.char_idx} 超出范围")

        self._old_timestamps = list(char.timestamps)
        char.add_timestamp(self.timestamp_ms, self.checkpoint_idx)

    def undo(self) -> None:
        if self._old_timestamps is not None:
            sentence = self.project.get_sentence(self.sentence_id)
            if sentence:
                char = sentence.get_character(self.char_idx)
                if char:
                    char.timestamps = list(self._old_timestamps)
                    char.push_to_ruby()

    @property
    def description(self) -> str:
        return f"添加时间标签 [{self.timestamp_ms}ms]"


class RemoveTimeTagCommand(Command):
    """删除时间标签命令

    移除指定字符上指定 checkpoint 位置的时间戳。
    """

    def __init__(
        self,
        project: Project,
        sentence_id: str,
        char_idx: int,
        checkpoint_idx: int,
    ):
        self.project = project
        self.sentence_id = sentence_id
        self.char_idx = char_idx
        self.checkpoint_idx = checkpoint_idx
        self._removed_ts: Optional[int] = None

    def execute(self) -> None:
        sentence = self.project.get_sentence(self.sentence_id)
        if not sentence:
            raise ValueError(f"句子 {self.sentence_id} 不存在")
        char = sentence.get_character(self.char_idx)
        if not char:
            raise ValueError(f"字符索引 {self.char_idx} 超出范围")

        self._removed_ts = char.remove_timestamp_at(self.checkpoint_idx)

    def undo(self) -> None:
        if self._removed_ts is not None:
            sentence = self.project.get_sentence(self.sentence_id)
            if sentence:
                char = sentence.get_character(self.char_idx)
                if char:
                    char.add_timestamp(self._removed_ts, self.checkpoint_idx)

    @property
    def description(self) -> str:
        ts = self._removed_ts if self._removed_ts is not None else "?"
        return f"删除时间标签 [{ts}ms]"


class ClearLineTimeTagsCommand(Command):
    """清空整行时间标签命令

    清除指定句子所有字符的时间戳。
    """

    def __init__(self, project: Project, sentence_id: str):
        self.project = project
        self.sentence_id = sentence_id
        self._old_timestamps: Optional[Dict[int, list]] = None

    def execute(self) -> None:
        sentence = self.project.get_sentence(self.sentence_id)
        if not sentence:
            raise ValueError(f"句子 {self.sentence_id} 不存在")

        # 保存所有字符的时间戳
        self._old_timestamps = {
            i: list(c.timestamps) for i, c in enumerate(sentence.characters)
        }
        sentence.clear_all_timestamps()

    def undo(self) -> None:
        if self._old_timestamps is not None:
            sentence = self.project.get_sentence(self.sentence_id)
            if sentence:
                for i, timestamps in self._old_timestamps.items():
                    char = sentence.get_character(i)
                    if char:
                        char.timestamps = list(timestamps)
                        char.push_to_ruby()

    @property
    def description(self) -> str:
        return "清空行时间标签"


class UpdateCharacterCommand(Command):
    """更新字符属性命令

    修改指定字符的属性（check_count、is_line_end、is_rest、
    linked_to_next、singer_id）。通过 kwargs 传入要修改的属性。
    """

    ALLOWED_ATTRS = {
        "check_count",
        "is_line_end",
        "is_rest",
        "linked_to_next",
        "singer_id",
    }

    def __init__(
        self,
        project: Project,
        sentence_id: str,
        char_idx: int,
        **kwargs: Any,
    ):
        self.project = project
        self.sentence_id = sentence_id
        self.char_idx = char_idx
        # 过滤非法属性
        self.updates = {k: v for k, v in kwargs.items() if k in self.ALLOWED_ATTRS}
        self._old_values: Dict[str, Any] = {}

    def execute(self) -> None:
        sentence = self.project.get_sentence(self.sentence_id)
        if not sentence:
            raise ValueError(f"句子 {self.sentence_id} 不存在")
        char = sentence.get_character(self.char_idx)
        if not char:
            raise ValueError(f"字符索引 {self.char_idx} 超出范围")

        # 保存旧值
        for key in self.updates:
            self._old_values[key] = getattr(char, key)

        # 应用新值
        for key, value in self.updates.items():
            setattr(char, key, value)

    def undo(self) -> None:
        if self._old_values:
            sentence = self.project.get_sentence(self.sentence_id)
            if sentence:
                char = sentence.get_character(self.char_idx)
                if char:
                    for key, value in self._old_values.items():
                        setattr(char, key, value)

    @property
    def description(self) -> str:
        attrs = ", ".join(f"{k}={v}" for k, v in self.updates.items())
        return f"更新字符属性 (char_idx={self.char_idx}, {attrs})"


class AddRubyCommand(Command):
    """添加注音命令

    为指定字符设置注音。
    """

    def __init__(
        self,
        project: Project,
        sentence_id: str,
        char_idx: int,
        ruby: Ruby,
    ):
        self.project = project
        self.sentence_id = sentence_id
        self.char_idx = char_idx
        self.ruby = ruby
        self._old_ruby: Optional[Ruby] = None

    def execute(self) -> None:
        sentence = self.project.get_sentence(self.sentence_id)
        if not sentence:
            raise ValueError(f"句子 {self.sentence_id} 不存在")
        char = sentence.get_character(self.char_idx)
        if not char:
            raise ValueError(f"字符索引 {self.char_idx} 超出范围")

        self._old_ruby = char.ruby
        char.set_ruby(self.ruby)

    def undo(self) -> None:
        sentence = self.project.get_sentence(self.sentence_id)
        if sentence:
            char = sentence.get_character(self.char_idx)
            if char:
                char.set_ruby(self._old_ruby)

    @property
    def description(self) -> str:
        return f"添加注音 [{self.ruby.text}]"


class RemoveRubyCommand(Command):
    """移除注音命令

    移除指定字符的注音。
    """

    def __init__(self, project: Project, sentence_id: str, char_idx: int):
        self.project = project
        self.sentence_id = sentence_id
        self.char_idx = char_idx
        self._removed_ruby: Optional[Ruby] = None

    def execute(self) -> None:
        sentence = self.project.get_sentence(self.sentence_id)
        if not sentence:
            raise ValueError(f"句子 {self.sentence_id} 不存在")

        self._removed_ruby = sentence.remove_ruby_from_char(self.char_idx)

    def undo(self) -> None:
        if self._removed_ruby:
            sentence = self.project.get_sentence(self.sentence_id)
            if sentence:
                char = sentence.get_character(self.char_idx)
                if char:
                    char.set_ruby(self._removed_ruby)

    @property
    def description(self) -> str:
        text = self._removed_ruby.text if self._removed_ruby else "?"
        return f"移除注音 [{text}]"


class AddSentenceCommand(Command):
    """添加句子命令"""

    def __init__(
        self,
        project: Project,
        sentence: Sentence,
        after_sentence_id: Optional[str] = None,
    ):
        self.project = project
        self.sentence = sentence
        self.after_sentence_id = after_sentence_id
        self._added = False

    def execute(self) -> None:
        self.project.add_sentence(self.sentence, self.after_sentence_id)
        self._added = True

    def undo(self) -> None:
        if self._added:
            try:
                self.project.remove_sentence(self.sentence.id)
            except Exception:
                pass

    @property
    def description(self) -> str:
        return f"添加歌词行 [{self.sentence.text[:10]}...]"


class RemoveSentenceCommand(Command):
    """删除句子命令"""

    def __init__(self, project: Project, sentence_id: str):
        self.project = project
        self.sentence_id = sentence_id
        self._sentence: Optional[Sentence] = None
        self._index: int = -1

    def execute(self) -> None:
        self._sentence = self.project.get_sentence(self.sentence_id)
        if not self._sentence:
            raise ValueError(f"句子 {self.sentence_id} 不存在")

        self._index = self.project.sentences.index(self._sentence)
        self.project.remove_sentence(self.sentence_id)

    def undo(self) -> None:
        if self._sentence:
            if 0 <= self._index <= len(self.project.sentences):
                self.project.sentences.insert(self._index, self._sentence)
            else:
                self.project.add_sentence(self._sentence)

    @property
    def description(self) -> str:
        return "删除歌词行"


class AddSingerCommand(Command):
    """添加演唱者命令"""

    def __init__(self, project: Project, singer):
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

    def __init__(
        self, project: Project, singer_id: str, transfer_to: Optional[str] = None
    ):
        self.project = project
        self.singer_id = singer_id
        self.transfer_to = transfer_to
        self._singer = None
        self._sentences = []

    def execute(self) -> None:
        self._singer = self.project.get_singer(self.singer_id)
        if self._singer:
            self._sentences = [
                s for s in self.project.sentences if s.singer_id == self.singer_id
            ]
            self.project.remove_singer(self.singer_id, self.transfer_to)

    def undo(self) -> None:
        if self._singer:
            self.project.add_singer(self._singer)
            for sentence in self._sentences:
                sentence.singer_id = self.singer_id
                if sentence not in self.project.sentences:
                    self.project.add_sentence(sentence)

    @property
    def description(self) -> str:
        return "删除演唱者"
