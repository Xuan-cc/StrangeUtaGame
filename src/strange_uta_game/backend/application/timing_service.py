"""打轴服务 (TimingService)

管理打轴流程、checkpoint 导航、音频协调、多演唱者切换。

核心功能：
1. 全局 Checkpoint 管理 - 维护跨所有演唱者的打轴位置
2. 打轴按键处理 - Space/F1-F9 的时间标签记录
3. 句尾长按打轴 - 按下=开始时间，抬起=结束时间
4. 多演唱者自动切换 - 后台管理，用户无感知
5. 音频协调 - 播放控制、变速、位置同步
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Protocol
from enum import Enum, auto
import time

from strange_uta_game.backend.domain import (
    Project,
    LyricLine,
    TimeTag,
    Checkpoint,
    Singer,
)
from strange_uta_game.backend.infrastructure.audio import IAudioEngine


class TimingError(Exception):
    """打轴相关错误"""

    pass


class RecordingState(Enum):
    """录制状态"""

    STOPPED = auto()
    PLAYING = auto()
    RECORDING = auto()  # 正在录制（句尾长按中）


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

    def on_line_end_recording_started(
        self, line_idx: int, char_idx: int, start_time_ms: int
    ) -> None:
        """句尾长按录制开始时回调"""
        ...

    def on_line_end_recording_finished(
        self, line_idx: int, char_idx: int, start_time_ms: int, end_time_ms: int
    ) -> None:
        """句尾长按录制结束时回调"""
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
    LINE_END_RECORDING_TIMEOUT_MS = 5000  # 句尾长按超时（5秒）
    SHORT_PRESS_THRESHOLD_MS = 100  # 短按阈值（100ms）
    DEFAULT_TIMING_OFFSET_MS = 0  # 默认打轴偏移量

    def __init__(self, audio_engine: IAudioEngine):
        """
        Args:
            audio_engine: 音频引擎实例
        """
        self._audio_engine = audio_engine
        self._project: Optional[Project] = None
        self._callbacks: Optional[TimingCallbacks] = None

        # 当前位置状态
        self._current_position = CheckpointPosition()

        # 录制状态
        self._recording_state = RecordingState.STOPPED
        self._line_end_recording = False
        self._line_end_start_time_ms: Optional[int] = None

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

    # ==================== Checkpoint 管理 ====================

    def _rebuild_global_checkpoints(self) -> None:
        """重建全局 Checkpoint 序列"""
        self._global_checkpoints = []

        if not self._project:
            return

        # 遍历所有歌词行，构建全局 Checkpoint 序列
        for line_idx, line in enumerate(self._project.lines):
            # 遍历该行的所有 checkpoint
            for checkpoint in line.checkpoints:
                pos = CheckpointPosition(
                    line_idx=line_idx,
                    char_idx=checkpoint.char_idx,
                    checkpoint_idx=checkpoint.checkpoint_idx,
                    singer_id=line.singer_id,
                )
                self._global_checkpoints.append(pos)

        # 按行、字符、checkpoint_idx 排序
        self._global_checkpoints.sort(
            key=lambda p: (p.line_idx, p.char_idx, p.checkpoint_idx)
        )

    def _get_current_checkpoint_info(self) -> tuple:
        """获取当前 checkpoint 的详细信息

        Returns:
            (LyricLine, Checkpoint) 或 (None, None)
        """
        if not self._project or not self._global_checkpoints:
            return None, None

        if self._global_checkpoint_idx >= len(self._global_checkpoints):
            return None, None

        pos = self._global_checkpoints[self._global_checkpoint_idx]

        if pos.line_idx >= len(self._project.lines):
            return None, None

        line = self._project.lines[pos.line_idx]

        # 查找对应的 checkpoint
        for cp in line.checkpoints:
            if cp.char_idx == pos.char_idx and cp.checkpoint_idx == pos.checkpoint_idx:
                return line, cp

        return line, None

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

        Args:
            line_idx: 行索引
            char_idx: 字符索引
            checkpoint_idx: checkpoint 索引（默认 0）

        Returns:
            是否成功移动
        """
        if not self._global_checkpoints:
            return False

        # 查找目标 checkpoint
        for i, pos in enumerate(self._global_checkpoints):
            if (
                pos.line_idx == line_idx
                and pos.char_idx == char_idx
                and pos.checkpoint_idx == checkpoint_idx
            ):
                prev_singer_id = self._current_position.singer_id
                self._global_checkpoint_idx = i
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

    def on_timing_key_pressed(self, key: str) -> None:
        """打轴按键按下处理（Space 或 F1-F9）

        Args:
            key: 按键名称（"SPACE", "F1", "F2", ...）
        """
        if not self._project:
            self._notify_error("NO_PROJECT", "未加载项目")
            return

        if not self._audio_engine.is_playing():
            # 未播放，先开始播放
            self._audio_engine.play()

        # 获取当前 checkpoint
        line, checkpoint = self._get_current_checkpoint_info()
        if not line or not checkpoint:
            self._notify_error("NO_CHECKPOINT", "无效的 checkpoint 位置")
            return

        # 检查是否为句尾字符
        if checkpoint.is_line_end:
            # 句尾长按处理 - 记录开始时间
            self._start_line_end_recording()
        else:
            # 普通字符 - 立即打轴（在按键抬起时完成）
            pass

    def on_timing_key_released(self, key: str) -> None:
        """打轴按键抬起处理

        Args:
            key: 按键名称（"SPACE", "F1", "F2", ...）
        """
        if not self._project:
            return

        # 获取当前音频时间
        current_time_ms = self._audio_engine.get_position_ms()

        # 应用偏移量
        timestamp_ms = max(0, current_time_ms + self._timing_offset_ms)

        if self._line_end_recording:
            # 句尾长按结束
            self._finish_line_end_recording(timestamp_ms)
        else:
            # 普通打轴
            self._add_timetag_at_current_checkpoint(timestamp_ms)

        # 自动移动到下一个 checkpoint
        self.move_to_next_checkpoint()

    def _start_line_end_recording(self) -> None:
        """开始句尾长按录制"""
        self._line_end_recording = True
        self._line_end_start_time_ms = self._audio_engine.get_position_ms()

        if self._callbacks:
            self._callbacks.on_line_end_recording_started(
                self._current_position.line_idx,
                self._current_position.char_idx,
                self._line_end_start_time_ms,
            )

    def _finish_line_end_recording(self, end_time_ms: int) -> None:
        """完成句尾长按录制

        Args:
            end_time_ms: 结束时间（毫秒）
        """
        if self._line_end_start_time_ms is None:
            return

        start_time_ms = self._line_end_start_time_ms

        # 检查是否超时
        if end_time_ms - start_time_ms > self.LINE_END_RECORDING_TIMEOUT_MS:
            # 超时，使用超时时间点
            end_time_ms = start_time_ms + self.LINE_END_RECORDING_TIMEOUT_MS

        # 检查是否为短按（视为普通打轴）
        if end_time_ms - start_time_ms < self.SHORT_PRESS_THRESHOLD_MS:
            # 短按 - 只记录开始时间
            self._add_timetag_at_current_checkpoint(start_time_ms)
        else:
            # 正常长按 - 记录开始和结束两个时间
            line, checkpoint = self._get_current_checkpoint_info()
            if line and checkpoint:
                # 获取当前演唱者
                singer_id = line.singer_id
                char_idx = checkpoint.char_idx

                # 添加开始时间标签
                start_tag = TimeTag(
                    timestamp_ms=start_time_ms,
                    singer_id=singer_id,
                    char_idx=char_idx,
                    checkpoint_idx=checkpoint.checkpoint_idx,
                )
                line.add_timetag(start_tag)

                # 查找或创建结束 checkpoint（通常是下一个字符或同一个字符的下一个 checkpoint）
                # 这里简化处理：添加到当前 checkpoint
                # 实际应该在下一个 checkpoint 添加结束时间

                # 通知回调
                if self._callbacks:
                    self._callbacks.on_timetag_added(
                        singer_id,
                        self._current_position.line_idx,
                        char_idx,
                        checkpoint.checkpoint_idx,
                        start_time_ms,
                    )

                    self._callbacks.on_line_end_recording_finished(
                        self._current_position.line_idx,
                        char_idx,
                        start_time_ms,
                        end_time_ms,
                    )

        # 重置状态
        self._line_end_recording = False
        self._line_end_start_time_ms = None

    def _add_timetag_at_current_checkpoint(self, timestamp_ms: int) -> None:
        """在当前 checkpoint 添加时间标签

        Args:
            timestamp_ms: 时间戳（毫秒）
        """
        line, checkpoint = self._get_current_checkpoint_info()
        if not line or not checkpoint:
            return

        # 检查时间倒退
        existing_tags = line.get_timetags_for_char(checkpoint.char_idx)
        for tag in existing_tags:
            if tag.timestamp_ms > timestamp_ms:
                self._notify_error(
                    "TIME_BACKWARD",
                    f"时间倒退: 新时间 {timestamp_ms}ms < 已存在 {tag.timestamp_ms}ms",
                )
                return

        # 创建时间标签
        tag = TimeTag(
            timestamp_ms=timestamp_ms,
            singer_id=line.singer_id,
            char_idx=checkpoint.char_idx,
            checkpoint_idx=checkpoint.checkpoint_idx,
        )

        # 添加到歌词行
        line.add_timetag(tag)

        # 通知回调
        if self._callbacks:
            self._callbacks.on_timetag_added(
                line.singer_id,
                self._current_position.line_idx,
                checkpoint.char_idx,
                checkpoint.checkpoint_idx,
                timestamp_ms,
            )

    def _notify_error(self, error_type: str, message: str) -> None:
        """通知错误"""
        if self._callbacks:
            self._callbacks.on_timing_error(error_type, message)

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
        # 重置句尾录制状态
        if self._line_end_recording:
            self._line_end_recording = False
            self._line_end_start_time_ms = None

    def seek(self, position_ms: int) -> None:
        """跳转到指定位置"""
        self._audio_engine.set_position_ms(position_ms)

    def set_speed(self, speed: float) -> None:
        """设置播放速度"""
        self._audio_engine.set_speed(speed)

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

        # 获取该演唱者的所有歌词行
        singer_lines = self._project.get_lines_by_singer(singer_id)

        if not singer_lines:
            return 0

        # 找到当前时间对应的行
        for i, line_idx in enumerate(singer_lines):
            line = self._project.lines[line_idx]

            if not line.timetags:
                continue

            # 获取该行的第一个和最后一个时间标签
            sorted_tags = sorted(line.timetags, key=lambda t: t.timestamp_ms)
            first_time = sorted_tags[0].timestamp_ms
            last_time = sorted_tags[-1].timestamp_ms

            # 检查是否在当前行的时间范围内
            if first_time <= time_ms <= last_time:
                return line_idx

            # 检查是否在下一行之前
            if i + 1 < len(singer_lines):
                next_line = self._project.lines[singer_lines[i + 1]]
                if next_line.timetags:
                    next_first = min(t.timestamp_ms for t in next_line.timetags)
                    if last_time < time_ms < next_first:
                        # 在行间间隙中，显示上一行
                        return line_idx

        # 默认显示第一行或最后一行
        if time_ms < first_time:
            return singer_lines[0] if singer_lines else 0
        else:
            return singer_lines[-1] if singer_lines else 0

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

            if line_idx >= len(self._project.lines):
                break

            line = self._project.lines[line_idx]

            # 找到第一个未打轴的 checkpoint
            for checkpoint in line.checkpoints:
                existing = line.get_timetags_for_char(checkpoint.char_idx)
                if not existing:
                    tag = TimeTag(
                        timestamp_ms=timestamp_ms,
                        singer_id=line.singer_id,
                        char_idx=checkpoint.char_idx,
                        checkpoint_idx=checkpoint.checkpoint_idx,
                    )
                    line.add_timetag(tag)
                    added_count += 1
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
        if line_idx >= len(self._project.lines):
            return 0

        line = self._project.lines[line_idx]
        count = len(line.timetags)
        line.timetags.clear()

        return count
