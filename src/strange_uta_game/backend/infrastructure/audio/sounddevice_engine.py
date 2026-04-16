"""SoundDevice 音频引擎实现。

基于 sounddevice + soundfile 库实现音频播放。
特点：
- 基于 PortAudio，跨平台
- 低延迟音频回调
- 支持 NumPy 数组操作
"""

import numpy as np
import sounddevice as sd
import soundfile as sf
from threading import Thread, Event, Lock
from typing import Callable, Optional
import time

from .base import (
    IAudioEngine,
    AudioLoadError,
    AudioPlaybackError,
    PlaybackState,
    AudioInfo,
)


class SoundDeviceEngine(IAudioEngine):
    """SoundDevice 音频引擎实现

    基于 sounddevice 库的音频播放实现。

    性能目标：
    - 启动延迟 < 50ms
    - 位置回调延迟 < 20ms
    - 位置回调频率 ~60fps
    """

    # 回调频率：60fps = 16.67ms
    CALLBACK_INTERVAL = 0.016  # 16ms

    def __init__(self):
        """初始化音频引擎"""
        # 音频数据
        self._data: Optional[np.ndarray] = None
        self._sample_rate: int = 44100
        self._channels: int = 2
        self._file_path: Optional[str] = None
        self._duration_ms: int = 0

        # 播放状态
        self._state = PlaybackState.STOPPED
        self._position_ms: int = 0
        self._speed: float = 1.0
        self._volume: float = 1.0

        # 线程控制
        self._playback_thread: Optional[Thread] = None
        self._stop_event = Event()
        self._pause_event = Event()
        self._position_lock = Lock()

        # 回调
        self._position_callback: Optional[Callable[[int], None]] = None
        self._callback_thread: Optional[Thread] = None

        # 当前播放流
        self._stream: Optional[sd.OutputStream] = None

    def load(self, file_path: str) -> None:
        """加载音频文件"""
        try:
            # 读取音频文件
            self._data, self._sample_rate = sf.read(file_path, dtype="float32")

            # 确保数据是二维数组（多声道）
            if self._data.ndim == 1:
                self._data = self._data.reshape(-1, 1)

            self._channels = self._data.shape[1]
            self._file_path = file_path

            # 计算时长（毫秒）
            duration_samples = len(self._data)
            self._duration_ms = int(duration_samples / self._sample_rate * 1000)

            # 重置状态
            self._position_ms = 0
            self._state = PlaybackState.STOPPED

        except FileNotFoundError:
            raise AudioLoadError(f"文件不存在: {file_path}")
        except Exception as e:
            raise AudioLoadError(f"加载音频失败: {e}")

    def play(self) -> None:
        """开始播放"""
        if self._data is None:
            raise AudioPlaybackError("没有加载音频文件")

        if self._state == PlaybackState.PLAYING:
            return  # 已经在播放

        if self._state == PlaybackState.PAUSED:
            # 从暂停恢复
            self._pause_event.set()
            self._state = PlaybackState.PLAYING
            return

        # 开始新播放
        self._stop_event.clear()
        self._pause_event.set()
        self._state = PlaybackState.PLAYING

        # 启动播放线程
        self._playback_thread = Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()

        # 启动回调线程
        if self._position_callback:
            self._callback_thread = Thread(target=self._callback_loop, daemon=True)
            self._callback_thread.start()

    def pause(self) -> None:
        """暂停播放"""
        if self._state == PlaybackState.PLAYING:
            self._pause_event.clear()
            self._state = PlaybackState.PAUSED

    def stop(self) -> None:
        """停止播放"""
        self._state = PlaybackState.STOPPED
        self._stop_event.set()
        self._pause_event.set()  # 确保线程能退出等待

        # 等待播放线程结束
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=0.5)

        # 关闭流
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # 重置位置
        with self._position_lock:
            self._position_ms = 0

    def get_position_ms(self) -> int:
        """获取当前播放位置（毫秒）"""
        with self._position_lock:
            return self._position_ms

    def set_position_ms(self, position_ms: int) -> None:
        """设置播放位置（毫秒）"""
        if self._data is None:
            return

        # 限制范围
        position_ms = max(0, min(position_ms, self._duration_ms))

        with self._position_lock:
            self._position_ms = position_ms

    def get_duration_ms(self) -> int:
        """获取音频总时长（毫秒）"""
        return self._duration_ms

    def get_playback_state(self) -> PlaybackState:
        """获取播放状态"""
        return self._state

    def is_playing(self) -> bool:
        """是否正在播放"""
        return self._state == PlaybackState.PLAYING

    def set_speed(self, speed: float) -> None:
        """设置播放速度"""
        if not 0.5 <= speed <= 2.0:
            raise ValueError(f"速度 {speed} 超出范围 [0.5, 2.0]")
        self._speed = speed

    def get_speed(self) -> float:
        """获取当前播放速度"""
        return self._speed

    def set_volume(self, volume: float) -> None:
        """设置音量"""
        self._volume = max(0.0, min(1.0, volume))

    def get_volume(self) -> float:
        """获取当前音量"""
        return self._volume

    def set_position_callback(self, callback: Callable[[int], None]) -> None:
        """设置位置变化回调"""
        self._position_callback = callback

        # 如果正在播放，启动回调线程
        if self._state == PlaybackState.PLAYING and not self._callback_thread:
            self._callback_thread = Thread(target=self._callback_loop, daemon=True)
            self._callback_thread.start()

    def clear_position_callback(self) -> None:
        """清除位置变化回调"""
        self._position_callback = None

    def get_audio_info(self) -> Optional[AudioInfo]:
        """获取音频文件信息"""
        if self._file_path is None:
            return None

        return AudioInfo(
            file_path=self._file_path,
            duration_ms=self._duration_ms,
            sample_rate=self._sample_rate,
            channels=self._channels,
        )

    def release(self) -> None:
        """释放资源"""
        self.stop()
        self._data = None
        self._file_path = None
        self._duration_ms = 0

    def _playback_loop(self) -> None:
        """播放循环线程"""
        try:
            # 计算缓冲区大小（约 100ms）
            block_size = int(self._sample_rate * 0.1)

            def callback(outdata, frames, time_info, status):
                """音频回调函数"""
                if status:
                    print(f"Audio callback status: {status}")

                # 检查暂停
                if not self._pause_event.is_set():
                    outdata.fill(0)
                    return

                # 检查停止
                if self._stop_event.is_set():
                    outdata.fill(0)
                    raise sd.CallbackStop

                # 获取当前位置
                with self._position_lock:
                    position_ms = self._position_ms

                # 计算采样位置
                position_samples = int(position_ms / 1000 * self._sample_rate)

                # 计算需要读取的采样数（考虑变速）
                samples_to_read = int(frames * self._speed)

                # 提取音频数据
                end_sample = min(position_samples + samples_to_read, len(self._data))
                data_chunk = self._data[position_samples:end_sample]

                # 应用音量
                data_chunk = data_chunk * self._volume

                # 填充输出缓冲区
                if len(data_chunk) < frames:
                    # 到达文件末尾
                    outdata[: len(data_chunk)] = data_chunk
                    outdata[len(data_chunk) :].fill(0)

                    # 播放结束
                    if end_sample >= len(self._data):
                        self._state = PlaybackState.STOPPED
                        self._stop_event.set()
                        raise sd.CallbackStop
                else:
                    outdata[:] = data_chunk[:frames]

                # 更新位置
                actual_samples_played = len(data_chunk) / self._speed
                with self._position_lock:
                    self._position_ms += int(
                        actual_samples_played / self._sample_rate * 1000
                    )

            # 创建输出流
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="float32",
                blocksize=block_size,
                callback=callback,
            )

            with self._stream:
                # 等待停止信号
                while not self._stop_event.is_set():
                    time.sleep(0.1)

        except Exception as e:
            print(f"Playback error: {e}")
            self._state = PlaybackState.STOPPED

    def _callback_loop(self) -> None:
        """位置回调线程（约 60fps）"""
        last_position = -1

        while not self._stop_event.is_set():
            if self._state == PlaybackState.PLAYING and self._position_callback:
                current_position = self.get_position_ms()

                # 只在位置变化时回调
                if current_position != last_position:
                    try:
                        self._position_callback(current_position)
                    except Exception as e:
                        print(f"Position callback error: {e}")

                    last_position = current_position

            # 16ms 间隔（约 60fps）
            time.sleep(self.CALLBACK_INTERVAL)
