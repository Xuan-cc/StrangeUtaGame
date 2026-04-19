"""SoundDevice 音频引擎实现。

基于 sounddevice + soundfile 库实现音频播放。
特点：
- 基于 PortAudio，跨平台
- 低延迟音频回调
- 支持 NumPy 数组操作
- Phase Vocoder 变速不变调
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
        self._original_data: Optional[np.ndarray] = None
        self._sample_rate: int = 44100
        self._channels: int = 2
        self._file_path: Optional[str] = None
        self._duration_ms: int = 0

        # 播放状态
        self._state = PlaybackState.STOPPED
        self._position_ms: int = 0  # 始终为原始音频时间
        self._speed: float = 1.0
        self._volume: float = 1.0

        # Phase Vocoder 状态
        self._stretched_speed: float = 1.0  # self._data 对应的变速倍率
        self._stretch_version: int = 0  # 防止过时的后台线程覆盖新数据

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
            data, self._sample_rate = sf.read(file_path, dtype="float32")

            # 确保数据是二维数组（多声道）
            if data.ndim == 1:
                data = data.reshape(-1, 1)

            self._channels = data.shape[1]
            self._file_path = file_path
            self._original_data = data
            self._data = data.copy()
            self._stretched_speed = 1.0
            self._stretch_version = 0

            # 计算时长（毫秒）
            duration_samples = len(data)
            self._duration_ms = int(duration_samples / self._sample_rate * 1000)

            # 重置状态
            self._position_ms = 0
            self._state = PlaybackState.STOPPED

            # 若当前速度不是 1.0，需要立即预处理
            if abs(self._speed - 1.0) > 0.001:
                self._stretch_version += 1
                version = self._stretch_version
                Thread(
                    target=self._apply_speed_stretch,
                    args=(self._speed, version),
                    daemon=True,
                ).start()

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
        """获取当前播放位置（毫秒，原始音频时间）"""
        with self._position_lock:
            return self._position_ms

    def set_position_ms(self, position_ms: int) -> None:
        """设置播放位置（毫秒，原始音频时间）"""
        if self._data is None:
            return

        # 限制范围
        position_ms = max(0, min(position_ms, self._duration_ms))

        with self._position_lock:
            self._position_ms = position_ms

    def get_duration_ms(self) -> int:
        """获取音频总时长（毫秒，始终返回原始时长）"""
        return self._duration_ms

    def get_playback_state(self) -> PlaybackState:
        """获取播放状态"""
        return self._state

    def is_playing(self) -> bool:
        """是否正在播放"""
        return self._state == PlaybackState.PLAYING

    def set_speed(self, speed: float) -> None:
        """设置播放速度（Phase Vocoder 变速不变调）

        调用后立即在后台线程开始处理变速音频。处理完成前使用
        线性插值回退模式（音调会变化），处理完成后无缝切换到
        变速不变调模式。
        """
        if not 0.5 <= speed <= 2.0:
            raise ValueError(f"速度 {speed} 超出范围 [0.5, 2.0]")
        self._speed = speed

        if self._original_data is None:
            return

        self._stretch_version += 1
        version = self._stretch_version
        Thread(
            target=self._apply_speed_stretch,
            args=(speed, version),
            daemon=True,
        ).start()

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
        self._original_data = None
        self._file_path = None
        self._duration_ms = 0
        self._stretched_speed = 1.0
        self._stretch_version = 0

    # ==================== Phase Vocoder ====================

    def _apply_speed_stretch(self, speed: float, version: int) -> None:
        """后台线程：用 Phase Vocoder 计算变速音频并替换播放数据。"""
        if self._original_data is None:
            return

        if abs(speed - 1.0) < 0.001:
            new_data = self._original_data.copy()
        else:
            new_data = self._time_stretch(self._original_data, speed)

        # 仅当此版本仍为最新时才应用结果（防止快速连按时旧结果覆盖新结果）
        if self._stretch_version == version:
            self._data = new_data
            self._stretched_speed = speed

    @staticmethod
    def _time_stretch(data: np.ndarray, speed: float) -> np.ndarray:
        """Phase Vocoder 时间拉伸：变速不变调。

        Args:
            data: 音频数据，shape (n_samples, n_channels), float32
            speed: 速度倍率（>1 = 更快/更短，<1 = 更慢/更长）

        Returns:
            变速后的音频数据，shape (new_n_samples, n_channels)
        """
        n_fft = 2048
        hop_a = n_fft // 4  # 512 —— 分析跳步
        hop_s = max(1, round(hop_a / speed))  # 合成跳步
        window = np.hanning(n_fft).astype(np.float32)

        n_channels = data.shape[1]
        n_samples = data.shape[0]
        n_frames = max(0, (n_samples - n_fft) // hop_a + 1)

        if n_frames < 2:
            return data.copy()

        out_len = (n_frames - 1) * hop_s + n_fft
        n_bins = n_fft // 2 + 1
        expected = (2.0 * np.pi * np.arange(n_bins) * hop_a / n_fft).astype(np.float64)
        factor = hop_s / hop_a
        win_sq = window**2

        result = np.zeros((out_len, n_channels), dtype=np.float32)

        for ch in range(n_channels):
            x = data[:, ch].astype(np.float64)
            output = np.zeros(out_len, dtype=np.float64)
            win_sq_sum = np.zeros(out_len, dtype=np.float64)

            prev_phase = np.zeros(n_bins, dtype=np.float64)
            phase_acc = np.zeros(n_bins, dtype=np.float64)

            for i in range(n_frames):
                start = i * hop_a
                frame = x[start : start + n_fft] * window

                spectrum = np.fft.rfft(frame)
                magnitude = np.abs(spectrum)
                phase = np.angle(spectrum)

                if i == 0:
                    phase_acc[:] = phase
                else:
                    dp = phase - prev_phase - expected
                    dp -= 2.0 * np.pi * np.round(dp / (2.0 * np.pi))
                    phase_acc += (expected + dp) * factor

                prev_phase[:] = phase

                synth_spectrum = magnitude * np.exp(1j * phase_acc)
                synth_frame = np.fft.irfft(synth_spectrum, n=n_fft).astype(np.float64)
                synth_frame *= window

                out_start = i * hop_s
                out_end = out_start + n_fft
                if out_end > out_len:
                    break
                output[out_start:out_end] += synth_frame
                win_sq_sum[out_start:out_end] += win_sq

            mask = win_sq_sum > 1e-8
            output[mask] /= win_sq_sum[mask]
            result[:, ch] = output.astype(np.float32)

        # 截取到目标长度
        target_len = max(1, round(n_samples / speed))
        if result.shape[0] > target_len:
            result = result[:target_len]
        elif result.shape[0] < target_len:
            pad = np.zeros((target_len - result.shape[0], n_channels), dtype=np.float32)
            result = np.vstack([result, pad])

        return result

    # ==================== 播放 ====================

    def _playback_loop(self) -> None:
        """播放循环线程"""
        try:
            # 计算缓冲区大小（约 100ms）
            block_size = int(self._sample_rate * 0.1)

            def callback(outdata, frames, time_info, status):
                """音频回调函数

                当 Phase Vocoder 已处理完成（stretched_speed == speed）时，
                直接从预处理数据中顺序读取（变速不变调）。
                否则回退到 np.interp 线性插值（变速变调，作为过渡）。
                """
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

                # 获取当前位置（原始音频时间）
                with self._position_lock:
                    position_ms = self._position_ms

                current_speed = self._speed
                stretched_speed = self._stretched_speed
                current_data = self._data  # 捕获引用（GIL 保证原子性）

                if current_data is None:
                    outdata.fill(0)
                    raise sd.CallbackStop

                if abs(current_speed - stretched_speed) < 0.001:
                    # ── Phase Vocoder 模式：直接读取预处理数据 ──
                    self._callback_stretched(
                        outdata, frames, position_ms, stretched_speed, current_data
                    )
                else:
                    # ── 回退模式：np.interp 线性插值（过渡期） ──
                    self._callback_interp(outdata, frames, position_ms, current_speed)

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

    def _callback_stretched(
        self,
        outdata: np.ndarray,
        frames: int,
        position_ms: int,
        stretched_speed: float,
        current_data: np.ndarray,
    ) -> None:
        """Phase Vocoder 模式回调：从预处理数据中顺序读取。"""
        # 原始时间 → 拉伸后采样位置
        stretched_sample = int(position_ms / 1000 * self._sample_rate / stretched_speed)

        end_sample = min(stretched_sample + frames, len(current_data))
        data_chunk = current_data[stretched_sample:end_sample]
        actual_read = len(data_chunk)

        if actual_read < 1:
            outdata.fill(0)
            self._state = PlaybackState.STOPPED
            self._stop_event.set()
            raise sd.CallbackStop

        outdata[:actual_read] = data_chunk
        if actual_read < frames:
            outdata[actual_read:].fill(0)

        # 应用音量
        outdata *= self._volume

        # 推进原始时间
        advanced_ms = int(actual_read * stretched_speed / self._sample_rate * 1000)
        with self._position_lock:
            self._position_ms += advanced_ms

        # 检查是否到达末尾
        if end_sample >= len(current_data):
            self._state = PlaybackState.STOPPED
            self._stop_event.set()
            raise sd.CallbackStop

    def _callback_interp(
        self,
        outdata: np.ndarray,
        frames: int,
        position_ms: int,
        speed: float,
    ) -> None:
        """回退模式回调：np.interp 线性插值变速（变调，仅过渡期使用）。"""
        src = self._original_data if self._original_data is not None else self._data
        if src is None:
            outdata.fill(0)
            raise sd.CallbackStop

        position_samples = int(position_ms / 1000 * self._sample_rate)
        source_frames = max(2, int(frames * speed))

        end_sample = min(position_samples + source_frames, len(src))
        data_chunk = src[position_samples:end_sample]
        actual_read = len(data_chunk)

        if actual_read < 2:
            outdata.fill(0)
            self._state = PlaybackState.STOPPED
            self._stop_event.set()
            raise sd.CallbackStop

        # 计算实际可输出的帧数
        if actual_read < source_frames:
            out_frames = max(1, int(actual_read / speed))
            out_frames = min(out_frames, frames)
        else:
            out_frames = frames

        # np.interp 线性插值重采样
        x_source = np.arange(actual_read)
        x_target = np.linspace(0, actual_read - 1, out_frames)
        for ch in range(self._channels):
            outdata[:out_frames, ch] = np.interp(x_target, x_source, data_chunk[:, ch])

        # 零填充剩余输出
        if out_frames < frames:
            outdata[out_frames:].fill(0)

        # 应用音量
        outdata *= self._volume

        # 更新位置（基于实际消耗的源帧数）
        with self._position_lock:
            self._position_ms += int(actual_read / self._sample_rate * 1000)

        # 检查是否到达文件末尾
        if end_sample >= len(src):
            self._state = PlaybackState.STOPPED
            self._stop_event.set()
            raise sd.CallbackStop

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
