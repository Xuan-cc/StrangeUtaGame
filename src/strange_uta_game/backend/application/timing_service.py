"""打轴服务 (TimingService)

管理打轴流程、checkpoint 导航、音频协调、多演唱者切换。

核心功能：
1. 全局 Checkpoint 管理 - 维护跨所有演唱者的打轴位置
2. 打轴按键处理 - Space/F1-F9 通过统一入口 on_key_changed 路由
3. 角色化 cp 过滤 - 普通 cp 仅响应 pressed；句尾末尾 cp 仅响应 released
4. 多演唱者自动切换 - 后台管理，用户无感知
5. 音频协调 - 播放控制、变速、位置同步
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Protocol, Literal
from enum import Enum, auto
import time

from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Singer,
)
from strange_uta_game.backend.infrastructure.audio import IAudioEngine

from .command_manager import CommandManager


class TimingError(Exception):
    """打轴相关错误"""

    pass


class RecordingState(Enum):
    """录制状态"""

    STOPPED = auto()
    PLAYING = auto()
    RECORDING = auto()  # 保留枚举以维持向后兼容（不再实际使用）


@dataclass
class CheckpointPosition:
    """Checkpoint 位置信息"""

    line_idx: int = 0
    char_idx: int = 0
    checkpoint_idx: int = 0
    singer_id: str = ""

    def __str__(self) -> str:
        return f"Line{self.line_idx}:Char{self.char_idx}:CP{self.checkpoint_idx}"


class TimingCallbacks(Protocol):
    """TimingService 回调接口"""

    def on_timetag_added(
        self,
        singer_id: str,
        line_idx: int,
        char_idx: int,
        checkpoint_idx: int,
        timestamp_ms: int,
    ) -> None:
        """时间标签添加时回调"""
        ...

    def on_position_changed(
        self, position_ms: int, duration_ms: int, singer_positions: Dict[str, int]
    ) -> None:
        """播放位置变化时回调
        singer_positions: {singer_id: line_idx} 各演唱者当前行索引
        """
        ...

    def on_singer_changed(self, new_singer_id: str, prev_singer_id: str) -> None:
        """演唱者切换时回调（自动管理触发）"""
        ...

    def on_checkpoint_moved(self, position: CheckpointPosition) -> None:
        """Checkpoint 位置移动时回调"""
        ...

    def on_timing_error(self, error_type: str, message: str) -> None:
        """打轴错误回调（如时间倒退警告）"""
        ...


class TimingService:
    """打轴服务

    协调音频播放、用户输入、歌词数据，实现打轴核心流程。
    管理全局 Checkpoint 序列，自动处理多演唱者切换。
    """

    # 常量
    DEFAULT_TIMING_OFFSET_MS = 0  # 默认打轴偏移量

    def __init__(
        self,
        audio_engine: IAudioEngine,
        command_manager: Optional[CommandManager] = None,
    ):
        """
        Args:
            audio_engine: 音频引擎实例
        """
        self._audio_engine = audio_engine
        self._command_manager = command_manager
        self._project: Optional[Project] = None
        self._callbacks: Optional[TimingCallbacks] = None

        # 当前位置状态
        self._current_position = CheckpointPosition()

        # 录制状态
        self._recording_state = RecordingState.STOPPED

        # 打轴偏移（补偿反应延迟）
        self._timing_offset_ms = self.DEFAULT_TIMING_OFFSET_MS

        # 全局 Checkpoint 缓存
        self._global_checkpoints: List[CheckpointPosition] = []
        self._global_checkpoint_idx = 0

        # 音频播放位置回调
        self._audio_engine.set_position_callback(self._on_audio_position_changed)

    def set_project(self, project: Project) -> None:
        """设置当前项目"""
        self._project = project
        self._rebuild_global_checkpoints()
        self._current_position = CheckpointPosition()
        if self._global_checkpoints:
            self._current_position = self._global_checkpoints[0]
        self._notify_checkpoint_moved()

    def set_callbacks(self, callbacks: TimingCallbacks) -> None:
        """设置回调接口"""
        self._callbacks = callbacks

    def set_timing_offset(self, offset_ms: int) -> None:
        """设置打轴偏移量（补偿反应延迟）"""
        self._timing_offset_ms = offset_ms

    def load_audio(self, file_path: str) -> None:
        """Load audio file. Raises AudioLoadError on failure."""
        try:
            self._audio_engine.stop()
        except Exception:
            pass
        self._audio_engine.load(file_path)

    def get_audio_info(self):
        return self._audio_engine.get_audio_info()

    def get_position_ms(self) -> int:
        return self._audio_engine.get_position_ms()

    def get_duration_ms(self) -> int:
        return self._audio_engine.get_duration_ms()

    def is_playing(self) -> bool:
        return self._audio_engine.is_playing()

    def set_volume(self, volume_percent: int) -> None:
        """Set volume 0-100 (converts to 0.0-1.0 for engine)"""
        self._audio_engine.set_volume(volume_percent / 100.0)

    def can_undo(self) -> bool:
        """是否可以撤销打轴命令"""
        return self._command_manager.can_undo() if self._command_manager else False

    def can_redo(self) -> bool:
        """是否可以重做打轴命令"""
        return self._command_manager.can_redo() if self._command_manager else False

    def undo(self) -> Optional[str]:
        """撤销上一个打轴命令"""
        if not self._command_manager:
            return None
        return self._command_manager.undo()

    def redo(self) -> Optional[str]:
        """重做上一个打轴命令"""
        if not self._command_manager:
            return None
        return self._command_manager.redo()

    # ==================== Checkpoint 管理 ====================

    def _rebuild_global_checkpoints(self) -> None:
        """重建全局 Checkpoint 序列

        遍历所有句子的所有字符，为每个 check_count > 0 的字符
        生成对应数量的 CheckpointPosition。
        """
        self._global_checkpoints = []

        if not self._project:
            return

        for line_idx, sentence in enumerate(self._project.sentences):
            for char_idx, char in enumerate(sentence.characters):
                for cp_idx in range(char.check_count):
                    pos = CheckpointPosition(
                        line_idx=line_idx,
                        char_idx=char_idx,
                        checkpoint_idx=cp_idx,
                        singer_id=char.singer_id,
                    )
                    self._global_checkpoints.append(pos)
                if char.is_sentence_end:
                    self._global_checkpoints.append(
                        CheckpointPosition(
                            line_idx=line_idx,
                            char_idx=char_idx,
                            checkpoint_idx=char.check_count,
                            singer_id=char.singer_id,
                        )
                    )

        # 按行、字符、checkpoint_idx 排序
        self._global_checkpoints.sort(
            key=lambda p: (p.line_idx, p.char_idx, p.checkpoint_idx)
        )

    def rebuild_global_checkpoints(self) -> None:
        """公开封装：重建全局 Checkpoint 序列。"""
        self._rebuild_global_checkpoints()

    def _get_current_checkpoint_info(self) -> tuple:
        """获取当前 checkpoint 的详细信息

        Returns:
            (Sentence, Character) 或 (None, None)
        """
        if not self._project or not self._global_checkpoints:
            return None, None

        if self._global_checkpoint_idx >= len(self._global_checkpoints):
            return None, None

        pos = self._global_checkpoints[self._global_checkpoint_idx]

        if pos.line_idx >= len(self._project.sentences):
            return None, None

        sentence = self._project.sentences[pos.line_idx]
        char = sentence.get_character(pos.char_idx)

        if char and (
            pos.checkpoint_idx < char.check_count
            or (char.is_sentence_end and pos.checkpoint_idx == char.check_count)
        ):
            return sentence, char

        return sentence, None

    def _notify_checkpoint_moved(self) -> None:
        """通知 checkpoint 移动"""
        if self._callbacks:
            self._callbacks.on_checkpoint_moved(self._current_position)

    def _notify_singer_changed(self, new_singer_id: str, prev_singer_id: str) -> None:
        """通知演唱者切换"""
        if self._callbacks:
            self._callbacks.on_singer_changed(new_singer_id, prev_singer_id)

    # ==================== 位置导航 ====================

    def move_to_next_checkpoint(self) -> bool:
        """移动到下一个 checkpoint

        Returns:
            是否成功移动
        """
        if not self._global_checkpoints:
            return False

        prev_singer_id = self._current_position.singer_id

        self._global_checkpoint_idx = min(
            self._global_checkpoint_idx + 1, len(self._global_checkpoints) - 1
        )

        self._current_position = self._global_checkpoints[self._global_checkpoint_idx]

        # 检查演唱者是否变化
        if self._current_position.singer_id != prev_singer_id:
            self._notify_singer_changed(
                self._current_position.singer_id, prev_singer_id
            )

        self._notify_checkpoint_moved()
        return True

    def move_to_prev_checkpoint(self) -> bool:
        """移动到上一个 checkpoint

        Returns:
            是否成功移动
        """
        if not self._global_checkpoints:
            return False

        prev_singer_id = self._current_position.singer_id

        self._global_checkpoint_idx = max(0, self._global_checkpoint_idx - 1)
        self._current_position = self._global_checkpoints[self._global_checkpoint_idx]

        # 检查演唱者是否变化
        if self._current_position.singer_id != prev_singer_id:
            self._notify_singer_changed(
                self._current_position.singer_id, prev_singer_id
            )

        self._notify_checkpoint_moved()
        return True

    def move_to_checkpoint(
        self, line_idx: int, char_idx: int, checkpoint_idx: int = 0
    ) -> bool:
        """移动到指定 checkpoint

        如果目标字符的 check_count=0（无 checkpoint），自动跳到该位置之后
        最近的有效 checkpoint。

        Args:
            line_idx: 行索引
            char_idx: 字符索引
            checkpoint_idx: checkpoint 索引（默认 0）

        Returns:
            是否成功移动
        """
        if not self._global_checkpoints:
            return False

        target = (line_idx, char_idx, checkpoint_idx)

        # 查找精确匹配或最近的下一个有效 checkpoint
        best_idx: Optional[int] = None
        for i, pos in enumerate(self._global_checkpoints):
            pos_key = (pos.line_idx, pos.char_idx, pos.checkpoint_idx)
            if pos_key == target:
                # 精确匹配
                best_idx = i
                break
            if pos_key >= target and best_idx is None:
                # 目标不存在（check_count=0）→ 取最近的下一个
                best_idx = i

        if best_idx is not None:
            pos = self._global_checkpoints[best_idx]
            prev_singer_id = self._current_position.singer_id
            self._global_checkpoint_idx = best_idx
            self._current_position = pos

            # 检查演唱者是否变化
            if pos.singer_id != prev_singer_id:
                self._notify_singer_changed(pos.singer_id, prev_singer_id)

            self._notify_checkpoint_moved()
            return True

        return False

    def get_current_position(self) -> CheckpointPosition:
        """获取当前位置"""
        return self._current_position

    def get_progress(self) -> tuple:
        """获取打轴进度

        Returns:
            (current_idx, total_count)
        """
        return (self._global_checkpoint_idx, len(self._global_checkpoints))

    # ==================== 打轴功能 ====================

    def on_key_changed(
        self, timestamp_ms: int, key_type: Literal["pressed", "released"]
    ) -> None:
        """打轴按键状态变更统一入口（按下/抬起均触发）。

        路由规则（角色化过滤）：
          - 普通 cp：仅响应 'pressed'，写入时间戳并推进；忽略 'released'
          - 句尾末尾 cp（is_sentence_end 且 cp_idx == check_count）：
            仅响应 'released'，写入 sentence_end_ts 并推进；忽略 'pressed'
        写入后单次推进到下一个 cp。

        Args:
            timestamp_ms: 时间戳（毫秒，已含 timing_offset 补偿）
            key_type: 'pressed' 或 'released'
        """
        if not self._project:
            return

        sentence, char = self._get_current_checkpoint_info()
        if not sentence or not char:
            # 当前位置无效（如 check_count=0 中段）→ 尝试跳到下一个有效 checkpoint
            if self.move_to_next_checkpoint():
                sentence, char = self._get_current_checkpoint_info()
            if not sentence or not char:
                return

        cp_idx = self._current_position.checkpoint_idx
        is_tail = char.is_sentence_end_tail_cp(cp_idx)

        # 角色化过滤
        if is_tail and key_type != "released":
            return
        if not is_tail and key_type != "pressed":
            return

        # 写入 + 单次推进
        self._add_timetag_at_current_checkpoint(timestamp_ms)
        self.move_to_next_checkpoint()

    def on_timing_key_pressed(self, key: str) -> None:
        """打轴按键按下处理（Space 或 F1-F9）

        薄 shim：自动启播 + 计算时间戳 + 转发 on_key_changed('pressed')

        Args:
            key: 按键名称（"SPACE", "F1", "F2", ...）
        """
        if not self._project:
            self._notify_error("NO_PROJECT", "未加载项目")
            return

        if not self._audio_engine.is_playing():
            self._audio_engine.play()

        timestamp_ms = max(0, self._audio_engine.get_position_ms() + self._timing_offset_ms)
        self.on_key_changed(timestamp_ms, "pressed")

    def on_timing_key_released(self, key: str) -> None:
        """打轴按键抬起处理

        薄 shim：计算时间戳 + 转发 on_key_changed('released')

        Args:
            key: 按键名称（"SPACE", "F1", "F2", ...）
        """
        if not self._project:
            return

        timestamp_ms = max(0, self._audio_engine.get_position_ms() + self._timing_offset_ms)
        self.on_key_changed(timestamp_ms, "released")

    def _add_timetag_at_current_checkpoint(self, timestamp_ms: int) -> None:
        """在当前 checkpoint 添加时间标签

        Args:
            timestamp_ms: 时间戳（毫秒）
        """
        sentence, char = self._get_current_checkpoint_info()
        if not sentence or not char:
            return

        if self._command_manager and self._project:
            from strange_uta_game.backend.application.commands import AddTimeTagCommand

            cmd = AddTimeTagCommand(
                project=self._project,
                sentence_id=sentence.id,
                char_idx=self._current_position.char_idx,
                timestamp_ms=timestamp_ms,
                checkpoint_idx=self._current_position.checkpoint_idx,
            )
            self._command_manager.execute(cmd)
        else:
            checkpoint_idx = self._current_position.checkpoint_idx
            if char.is_sentence_end and checkpoint_idx == char.check_count:
                char.set_sentence_end_ts(timestamp_ms)
            else:
                char.add_timestamp(timestamp_ms, checkpoint_idx)

        # 通知回调
        if self._callbacks:
            self._callbacks.on_timetag_added(
                char.singer_id,
                self._current_position.line_idx,
                self._current_position.char_idx,
                self._current_position.checkpoint_idx,
                timestamp_ms,
            )

    def _notify_error(self, error_type: str, message: str) -> None:
        """通知错误"""
        if self._callbacks:
            self._callbacks.on_timing_error(error_type, message)

    def adjust_current_timestamp(self, delta_ms: int) -> bool:
        """微调当前选中 checkpoint 的时间戳（批 18 #8）。

        TimingService 作为时间戳唯一写入入口，统一处理普通 cp / 句尾 cp
        两分支：
          - 句尾 cp（is_sentence_end 且 cp_idx == check_count）走
            Character.set_sentence_end_ts，内部已 _update_offset_timestamps +
            push_to_ruby。
          - 普通 cp 直接覆写 Character.timestamps[cp_idx]，必须显式调
            _update_offset_timestamps() 重算 render/export，再 push_to_ruby()
            同步 Ruby.timestamps 和 RubyPart.offset_ms。

        Args:
            delta_ms: 时间戳增量（毫秒，可正可负）

        Returns:
            True 表示写入成功，False 表示当前位置无可调时间戳
        """
        if not self._project:
            return False
        sentence, char = self._get_current_checkpoint_info()
        if not sentence or not char:
            return False
        cp_idx = self._current_position.checkpoint_idx
        if char.is_sentence_end and cp_idx == char.check_count:
            if char.sentence_end_ts is None:
                return False
            char.set_sentence_end_ts(max(0, char.sentence_end_ts + delta_ms))
        else:
            if cp_idx >= len(char.timestamps):
                return False
            char.timestamps[cp_idx] = max(0, char.timestamps[cp_idx] + delta_ms)
            char._update_offset_timestamps()
            char.push_to_ruby()
        return True

    # ==================== 音频控制 ====================

    def play(self) -> None:
        """开始播放"""
        self._audio_engine.play()
        self._recording_state = RecordingState.PLAYING

    def pause(self) -> None:
        """暂停播放"""
        self._audio_engine.pause()
        self._recording_state = RecordingState.STOPPED

    def stop(self) -> None:
        """停止播放"""
        self._audio_engine.stop()
        self._recording_state = RecordingState.STOPPED

    def seek(self, position_ms: int) -> None:
        """跳转到指定位置"""
        self._audio_engine.set_position_ms(position_ms)

    def set_speed(self, speed: float) -> None:
        """设置播放速度"""
        self._audio_engine.set_speed(speed)

    def release(self) -> None:
        self.stop()
        self._audio_engine.release()

    def _on_audio_position_changed(self, position_ms: int) -> None:
        """音频位置变化回调（由音频引擎调用）"""
        if not self._callbacks:
            return

        # 构建各演唱者的当前行位置
        singer_positions: Dict[str, int] = {}

        if self._project:
            for singer in self._project.singers:
                # 找到该演唱者在当前播放位置应该显示的行
                line_idx = self._find_line_for_singer_at_time(singer.id, position_ms)
                singer_positions[singer.id] = line_idx

        duration_ms = self._audio_engine.get_duration_ms()

        self._callbacks.on_position_changed(position_ms, duration_ms, singer_positions)

    def _find_line_for_singer_at_time(self, singer_id: str, time_ms: int) -> int:
        """查找指定演唱者在指定时间应该显示的歌词行

        Args:
            singer_id: 演唱者 ID
            time_ms: 时间（毫秒）

        Returns:
            行索引
        """
        if not self._project:
            return 0

        # 获取该演唱者的所有句子
        singer_lines = [
            (idx, sentence)
            for idx, sentence in enumerate(self._project.sentences)
            if sentence.singer_id == singer_id
        ]

        if not singer_lines:
            return 0

        first_timed_line_idx: Optional[int] = None
        first_time_ms: Optional[int] = None

        # 找到当前时间对应的行
        for i, (line_idx, sentence) in enumerate(singer_lines):
            if not sentence.has_timetags:
                continue

            first_time = sentence.timing_start_ms
            last_time = sentence.timing_end_ms

            if first_timed_line_idx is None:
                first_timed_line_idx = line_idx
                first_time_ms = first_time

            # 检查是否在当前行的时间范围内
            if first_time is not None and last_time is not None:
                if first_time <= time_ms <= last_time:
                    return line_idx

                # 检查是否在下一行之前
                if i + 1 < len(singer_lines):
                    _, next_sentence = singer_lines[i + 1]
                    if next_sentence.has_timetags:
                        next_first = next_sentence.timing_start_ms
                        if next_first is not None and last_time < time_ms < next_first:
                            # 在行间间隙中，显示上一行
                            return line_idx

        # 默认显示第一行或最后一行
        if first_timed_line_idx is None or first_time_ms is None:
            return singer_lines[0][0]

        if time_ms < first_time_ms:
            return first_timed_line_idx

        return singer_lines[-1][0]

    # ==================== 批量打轴功能 ====================

    def add_timetag_batch(
        self, timestamps_ms: List[int], line_indices: Optional[List[int]] = None
    ) -> int:
        """批量添加时间标签

        Args:
            timestamps_ms: 时间戳列表
            line_indices: 对应的行索引列表（可选，默认为当前行开始）

        Returns:
            成功添加的数量
        """
        if not self._project:
            return 0

        added_count = 0

        for i, timestamp_ms in enumerate(timestamps_ms):
            if line_indices and i < len(line_indices):
                line_idx = line_indices[i]
            else:
                line_idx = self._current_position.line_idx + i

            if line_idx >= len(self._project.sentences):
                break

            sentence = self._project.sentences[line_idx]

            # 找到第一个未打轴的 checkpoint
            found = False
            for char in sentence.characters:
                for cp_idx in range(char.check_count):
                    if cp_idx >= len(char.timestamps):
                        # 该 checkpoint 尚未打轴
                        char.add_timestamp(timestamp_ms, cp_idx)
                        added_count += 1
                        found = True
                        break
                if not found and char.is_sentence_end and char.sentence_end_ts is None:
                    char.set_sentence_end_ts(timestamp_ms)
                    added_count += 1
                    found = True
                if found:
                    break

        return added_count

    def clear_timetags_for_current_line(self) -> int:
        """清除当前行的所有时间标签

        Returns:
            清除的数量
        """
        if not self._project:
            return 0

        line_idx = self._current_position.line_idx
        if line_idx >= len(self._project.sentences):
            return 0

        sentence = self._project.sentences[line_idx]
        count = sum(len(c.all_timestamps) for c in sentence.characters)

        if self._command_manager and self._project:
            from strange_uta_game.backend.application.commands import (
                ClearLineTimeTagsCommand,
            )

            cmd = ClearLineTimeTagsCommand(
                project=self._project,
                sentence_id=sentence.id,
            )
            self._command_manager.execute(cmd)
        else:
            sentence.clear_all_timestamps()

        return count
