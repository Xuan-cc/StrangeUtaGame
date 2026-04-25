"""Project 聚合根定义。

Project 作为聚合根，管理所有句子和演唱者的一致性。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from uuid import uuid4
from datetime import datetime

from .models import Character, DomainError, ValidationError
from .entities import Singer, Sentence


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
    language: str = "ja"
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

    作为聚合根，管理所有句子和演唱者的一致性。

    Attributes:
        id: 唯一标识符（UUID）
        sentences: 句子列表（所有演唱者的歌词混合存储，通过 singer_id 关联）
        singers: 演唱者列表
        metadata: 元数据（标题、艺术家、语言等）
        audio_duration_ms: 音频时长（毫秒）

    Business Rules:
        1. 句子按时间顺序排列
        2. 每个句子有唯一 ID，并关联到特定演唱者
        3. 必须至少有一个演唱者（default = true）
        4. 不存储音频路径 — 音频由用户每次使用时单独选择
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    sentences: List[Sentence] = field(default_factory=list)
    singers: List[Singer] = field(default_factory=list)
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    audio_duration_ms: int = 0

    def __post_init__(self) -> None:
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

    # ── 兼容别名 ──

    @property
    def lines(self) -> List[Sentence]:
        """兼容旧接口：sentences 的别名"""
        return self.sentences

    @lines.setter
    def lines(self, value: List[Sentence]) -> None:
        """兼容旧接口：设置 sentences"""
        self.sentences = value

    # ==================== 演唱者管理 ====================

    def add_singer(self, singer: Singer) -> None:
        """添加演唱者"""
        if singer.is_default:
            for s in self.singers:
                s.is_default = False

        self.singers.append(singer)
        self.singers.sort(key=lambda s: s.display_priority)
        self._update_timestamp()

    def remove_singer(self, singer_id: str, transfer_to: Optional[str] = None) -> None:
        """删除演唱者

        Args:
            singer_id: 要删除的演唱者ID
            transfer_to: 将其歌词转移到的演唱者ID（如果为 None 则级联删除歌词）
        """
        if len(self.singers) <= 1:
            raise ValidationError("必须至少保留一个演唱者")

        singer = self.get_singer(singer_id)
        if not singer:
            raise DomainError(f"演唱者 {singer_id} 不存在")

        if transfer_to:
            target = self.get_singer(transfer_to)
            if not target:
                raise DomainError(f"目标演唱者 {transfer_to} 不存在")

            for sentence in self.sentences:
                if sentence.singer_id == singer_id:
                    sentence.singer_id = transfer_to
        else:
            self.sentences = [s for s in self.sentences if s.singer_id != singer_id]

        self.singers = [s for s in self.singers if s.id != singer_id]
        self._update_timestamp()

    def get_singer(self, singer_id: str) -> Optional[Singer]:
        """根据ID获取演唱者"""
        for singer in self.singers:
            if singer.id == singer_id:
                return singer
        return None

    def get_default_singer(self) -> Singer:
        """获取默认演唱者"""
        for singer in self.singers:
            if singer.is_default:
                return singer
        raise DomainError("没有默认演唱者")

    # ==================== 句子管理 ====================

    def add_sentence(
        self, sentence: Sentence, after_sentence_id: Optional[str] = None
    ) -> None:
        """添加句子

        Args:
            sentence: 句子对象
            after_sentence_id: 插入到指定句子之后
        """
        if not self.get_singer(sentence.singer_id):
            raise ValidationError(f"演唱者 {sentence.singer_id} 不存在，无法添加句子")

        if after_sentence_id:
            for i, s in enumerate(self.sentences):
                if s.id == after_sentence_id:
                    self.sentences.insert(i + 1, sentence)
                    break
            else:
                self.sentences.append(sentence)
        else:
            self.sentences.append(sentence)

        self._update_timestamp()

    # 兼容别名
    def add_line(self, line: Sentence, after_line_id: Optional[str] = None) -> None:
        """兼容旧接口：add_sentence 的别名"""
        self.add_sentence(line, after_line_id)

    def remove_sentence(self, sentence_id: str) -> None:
        """删除句子"""
        original_len = len(self.sentences)
        self.sentences = [s for s in self.sentences if s.id != sentence_id]

        if len(self.sentences) == original_len:
            raise DomainError(f"句子 {sentence_id} 不存在")

        self._update_timestamp()

    # 兼容别名
    def remove_line(self, line_id: str) -> None:
        """兼容旧接口：remove_sentence 的别名"""
        self.remove_sentence(line_id)

    def get_sentence(self, sentence_id: str) -> Optional[Sentence]:
        """根据ID获取句子"""
        for sentence in self.sentences:
            if sentence.id == sentence_id:
                return sentence
        return None

    # 兼容别名
    def get_line(self, line_id: str) -> Optional[Sentence]:
        """兼容旧接口：get_sentence 的别名"""
        return self.get_sentence(line_id)

    def get_sentences_by_singer(self, singer_id: str) -> List[Sentence]:
        """获取指定演唱者的所有句子"""
        return [s for s in self.sentences if s.singer_id == singer_id]

    # 兼容别名
    def get_lines_by_singer(self, singer_id: str) -> List[Sentence]:
        """兼容旧接口：get_sentences_by_singer 的别名"""
        return self.get_sentences_by_singer(singer_id)

    def move_sentence(self, sentence_id: str, new_position: int) -> None:
        """移动句子到指定位置"""
        sentence = self.get_sentence(sentence_id)
        if not sentence:
            raise DomainError(f"句子 {sentence_id} 不存在")

        if new_position < 0 or new_position >= len(self.sentences):
            raise ValidationError(f"位置 {new_position} 超出范围")

        self.sentences = [s for s in self.sentences if s.id != sentence_id]
        self.sentences.insert(new_position, sentence)
        self._update_timestamp()

    def merge_line_into_previous(self, line_idx: int) -> bool:
        """将指定行合并到上一行。"""
        if line_idx <= 0 or line_idx >= len(self.sentences):
            return False

        prev_sentence = self.sentences[line_idx - 1]
        current_sentence = self.sentences[line_idx]

        if not current_sentence.characters:
            self.sentences.pop(line_idx)
            self._update_timestamp()
            return True

        if prev_sentence.characters:
            prev_tail = prev_sentence.characters[-1]
            tail_singer = prev_tail.singer_id
            prev_tail.is_line_end = False
            prev_sentence.characters.append(
                Character(
                    char=" ",
                    singer_id=tail_singer,
                    ruby=None,
                    check_count=0,
                    is_line_end=False,
                    is_sentence_end=False,
                    timestamps=[],
                )
            )

        prev_sentence.characters.extend(current_sentence.characters)
        prev_sentence.singer_id = prev_sentence.characters[0].singer_id or prev_sentence.singer_id
        self.sentences.pop(line_idx)
        self._update_timestamp()
        return True

    def delete_line(self, line_idx: int) -> None:
        """按索引删除整行。"""
        if line_idx < 0 or line_idx >= len(self.sentences):
            raise ValidationError(f"行索引 {line_idx} 超出范围")

        self.sentences.pop(line_idx)
        self._update_timestamp()

    def insert_blank_line(self, after_line_idx: int, singer_id: str = "") -> int:
        """在指定行后插入空行，返回新行索引。"""
        if after_line_idx < -1 or after_line_idx >= len(self.sentences):
            raise ValidationError(f"行索引 {after_line_idx} 超出范围")

        if singer_id:
            resolved_singer_id = singer_id
        elif 0 <= after_line_idx < len(self.sentences):
            resolved_singer_id = self.sentences[after_line_idx].singer_id
        else:
            resolved_singer_id = self.get_default_singer().id

        sentence = Sentence(
            singer_id=resolved_singer_id,
            characters=[
                Character(
                    char=" ",
                    singer_id=resolved_singer_id,
                    ruby=None,
                    check_count=0,
                    is_line_end=True,
                    is_sentence_end=False,
                    timestamps=[],
                )
            ],
        )
        new_idx = after_line_idx + 1
        self.sentences.insert(new_idx, sentence)
        self._update_timestamp()
        return new_idx

    def insert_line_break(self, line_idx: int, char_idx: int) -> None:
        """在指定字符后插入换行。"""
        if line_idx < 0 or line_idx >= len(self.sentences):
            raise ValidationError(f"行索引 {line_idx} 超出范围")

        sentence = self.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            raise ValidationError(f"字符索引 {char_idx} 超出范围")

        new_sentence = sentence.split_at(char_idx)
        if new_sentence.characters:
            inherit_singer_id = sentence.characters[char_idx].singer_id or sentence.singer_id
            new_sentence.singer_id = inherit_singer_id
            for moved_char in new_sentence.characters:
                if not moved_char.singer_id:
                    moved_char.singer_id = inherit_singer_id
        else:
            new_sentence.singer_id = sentence.characters[char_idx].singer_id or sentence.singer_id

        self.sentences.insert(line_idx + 1, new_sentence)
        self._update_timestamp()

    # ==================== 全局查询 ====================

    def get_all_timestamps(self) -> List[tuple[str, int, int, int, int]]:
        """获取所有时间戳（用于全局排序和导航）

        Returns:
            列表项: (sentence_id, sentence_idx, char_idx, checkpoint_idx, timestamp_ms)
        """
        result = []
        for s_idx, sentence in enumerate(self.sentences):
            for c_idx, char in enumerate(sentence.characters):
                for cp_idx, ts in enumerate(char.all_timestamps):
                    result.append((sentence.id, s_idx, c_idx, cp_idx, ts))
        result.sort(key=lambda x: x[4])
        return result

    def collect_all_timestamp_ms(self) -> List[int]:
        """收集所有字符 checkpoint 的时间戳（毫秒），按原始顺序返回，未排序。

        用于前端时间轴显示等只关心毫秒值的场景。如需附带位置信息请使用
        :py:meth:`get_all_timestamps`。
        """
        tags_ms: List[int] = []
        for sentence in self.sentences:
            for ch in sentence.characters:
                tags_ms.extend(ch.all_timestamps)
        return tags_ms

    def find_prev_line_with_checkpoints(self, current_idx: int) -> int:
        """从 current_idx 之前向上查找第一个存在 checkpoint 的行索引。

        判定一行"有 checkpoint" 的条件：任一字符 ``check_count > 0`` 或
        ``is_sentence_end``（与前端打轴导航语义保持一致）。

        Args:
            current_idx: 当前行索引，将从 ``current_idx - 1`` 开始向上扫描。

        Returns:
            匹配的行索引；若不存在则返回 ``-1``。
        """
        cand = current_idx - 1
        while cand >= 0:
            s = self.sentences[cand]
            if any(ch.check_count > 0 or ch.is_sentence_end for ch in s.characters):
                return cand
            cand -= 1
        return -1

    def find_next_line_with_characters(self, current_idx: int) -> int:
        """从 current_idx 之后向下查找第一个存在字符的行索引。

        Q4 字符级跨行导航：与 ``find_prev_line_with_checkpoints`` 对称，
        但只要求行内有字符即可（不要求已打 checkpoint），因为字符级导航
        的目标只是把光标定位到下一行的"第一个字符"，无需该字符已打轴。

        Args:
            current_idx: 当前行索引，将从 ``current_idx + 1`` 开始向下扫描。

        Returns:
            匹配的行索引；若不存在则返回 ``-1``。
        """
        cand = current_idx + 1
        while cand < len(self.sentences):
            if len(self.sentences[cand].characters) > 0:
                return cand
            cand += 1
        return -1

    def find_prev_line_with_characters(self, current_idx: int) -> int:
        """从 current_idx 之前向上查找第一个存在字符的行索引。

        Q4 字符级跨行导航的反向版本（用于 LEFT 键在行首跨行）。

        Args:
            current_idx: 当前行索引，将从 ``current_idx - 1`` 开始向上扫描。

        Returns:
            匹配的行索引；若不存在则返回 ``-1``。
        """
        cand = current_idx - 1
        while cand >= 0:
            if len(self.sentences[cand].characters) > 0:
                return cand
            cand -= 1
        return -1

    def get_timing_statistics(self) -> Dict[str, Any]:
        """获取打轴统计信息"""
        total_chars = sum(len(s.characters) for s in self.sentences)
        total_timetags = sum(
            sum(len(c.all_timestamps) for c in s.characters) for s in self.sentences
        )
        total_checkpoints = sum(
            sum(c.total_timing_points for c in s.characters) for s in self.sentences
        )

        completed = sum(1 for s in self.sentences if s.is_fully_timed())

        return {
            "total_lines": len(self.sentences),
            "total_singers": len(self.singers),
            "total_chars": total_chars,
            "total_timetags": total_timetags,
            "total_checkpoints": total_checkpoints,
            "completed_lines": completed,
            "completion_rate": (
                completed / len(self.sentences) if self.sentences else 0
            ),
            "timing_progress": f"{total_timetags}/{total_checkpoints}",
        }

    # ==================== 验证 ====================

    def validate(self) -> List[str]:
        """验证项目数据有效性"""
        errors = []

        if not self.singers:
            errors.append("必须至少有一个演唱者")

        if not any(s.is_default for s in self.singers):
            errors.append("必须有一个默认演唱者")

        singer_ids = {s.id for s in self.singers}
        for sentence in self.sentences:
            if sentence.singer_id not in singer_ids:
                errors.append(
                    f"句子 {sentence.id} 的 singer_id {sentence.singer_id} 无效"
                )

        return errors

    def is_valid(self) -> bool:
        """检查项目是否有效"""
        return len(self.validate()) == 0

    # ==================== Checkpoint 全局选中管理 ====================
    #
    # 不变量（Invariants）：
    #   I1. 全局单选：整个 Project 中所有 Character 的 selected_checkpoint_idx
    #       非 None 的至多 1 个。
    #   I2. 默认选中：项目非空打开/创建后，应调用 select_default_checkpoint()
    #       选中首行首字首 cp（由 application 层负责触发）。
    #   I3. 增删对称：每次 set_selected_checkpoint 必先清旧再设新。
    #
    # 选中态定位 (line_idx, char_pos, cp_idx) — cp_idx 指 all_timestamps
    # 域（含 sentence_end 虚拟 cp），与 KaraokePreview._current_checkpoint_idx
    # 语义一致。
    #
    # 不做运行时校验：cp_idx 可能因 F5/F6 增减 cp 而失效，按用户约定不校验，
    # 由调用方（事件处理器）保证传入有效值。

    def clear_selected_checkpoint(self) -> None:
        """清除当前选中的 checkpoint（扫全局，O(N)）。"""
        for sentence in self.sentences:
            for char in sentence.characters:
                if char.selected_checkpoint_idx is not None:
                    char.selected_checkpoint_idx = None

    def set_selected_checkpoint(
        self, line_idx: int, char_pos: int, cp_idx: int
    ) -> bool:
        """设置全局唯一选中的 checkpoint。

        先扫全局清掉旧选中，再设新选中。不做边界校验——
        调用方需保证 (line_idx, char_pos, cp_idx) 是有效定位。

        Args:
            line_idx: 句子索引
            char_pos: 字符索引
            cp_idx: checkpoint 索引（all_timestamps 域）

        Returns:
            True 表示设置成功，False 表示目标无效（line/char 越界）。
        """
        if line_idx < 0 or line_idx >= len(self.sentences):
            return False
        sentence = self.sentences[line_idx]
        if char_pos < 0 or char_pos >= len(sentence.characters):
            return False

        # 先清旧（维持 I1）
        self.clear_selected_checkpoint()
        # 设新
        sentence.characters[char_pos].selected_checkpoint_idx = cp_idx
        return True

    def get_selected_checkpoint(self) -> Optional[tuple[int, int, int]]:
        """查询当前选中 checkpoint 定位，未选中返回 None。"""
        for line_idx, sentence in enumerate(self.sentences):
            for char_pos, char in enumerate(sentence.characters):
                if char.selected_checkpoint_idx is not None:
                    return (line_idx, char_pos, char.selected_checkpoint_idx)
        return None

    def select_default_checkpoint(self) -> bool:
        """选中首行首字的 cp 0（用于项目打开后的默认态）。

        Returns:
            True 表示成功选中，False 表示无句子/无字符。
        """
        if not self.sentences:
            return False
        first = self.sentences[0]
        if not first.characters:
            return False
        return self.set_selected_checkpoint(0, 0, 0)

    def shift_selected_checkpoint_if_lost(self) -> bool:
        """若当前选中 cp 因 check_count 缩减而失效，则顺延到下一个有效 cp。

        触发场景：自动注音/标点开关变更后，原选中位置的 check_count 变小，
        导致 cp_idx 越界（含选中标点 cp 后标点开关被关闭的情况）。

        顺延策略：
            1. 同字内：cp_idx 截断到 check_count - 1。
            2. 同行内向后：跳到首个 check_count > 0 的字符 cp 0。
            3. 跨行向后：下一行首个 check_count > 0 的字符 cp 0。
            4. 全部失效：清除选中。

        Returns:
            True 表示发生了顺延或清除；False 表示选中仍有效。
        """
        loc = self.get_selected_checkpoint()
        if loc is None:
            return False
        line_idx, char_pos, cp_idx = loc

        if line_idx >= len(self.sentences):
            self.clear_selected_checkpoint()
            return True
        sentence = self.sentences[line_idx]
        if char_pos >= len(sentence.characters):
            self.clear_selected_checkpoint()
            return True

        char = sentence.characters[char_pos]
        # 仍有效：在 [0, check_count) 区间内
        if char.check_count > 0 and 0 <= cp_idx < char.check_count:
            return False

        # 同字截断
        if char.check_count > 0:
            sentence.characters[char_pos].selected_checkpoint_idx = char.check_count - 1
            return True

        # 同行向后
        for next_pos in range(char_pos + 1, len(sentence.characters)):
            if sentence.characters[next_pos].check_count > 0:
                self.set_selected_checkpoint(line_idx, next_pos, 0)
                return True

        # 跨行向后
        for next_line in range(line_idx + 1, len(self.sentences)):
            sent = self.sentences[next_line]
            for next_pos in range(len(sent.characters)):
                if sent.characters[next_pos].check_count > 0:
                    self.set_selected_checkpoint(next_line, next_pos, 0)
                    return True

        # 全部失效
        self.clear_selected_checkpoint()
        return True

    # ==================== 内部方法 ====================

    def _update_timestamp(self) -> None:
        """更新修改时间"""
        self.metadata.updated_at = datetime.now()
