"""SoundDevice 音频引擎实现（audiotsm WSOLA 流式变速版）。

基于 sounddevice + soundfile 播放，变速/变调由 audiotsm.wsola 负责。
设计目标：
- 预加载音频到内存 (Numpy array)
- 在音频回调内实时向 WSOLA 索取变速后的 samples
- 用户拖动倍速滑块时只修改 ``self._tsm.set_speed(...)``，相位、缓冲区
  由 audiotsm 内部维护，拼接处无断裂、无爆音
- 用户拖动进度条时重建 ArrayReader 并 ``tsm.clear()``，避免跨位置
  残留相位污染

关键特性：
- 支持任意 0.5~2.0 之间的浮点倍速
- 立体声原生支持（WSOLA 通过一致性块位移，声道间相位差天然保留）
- 变速不变调（WSOLA = Waveform Similarity Overlap-Add）
- 单线程播放（回调内同步拉取，无后台预处理线程，无淡入拼接）
"""

import numpy as np
import sounddevice as sd
import soundfile as sf
import audiotsm
from audiotsm.io.array import ArrayReader, ArrayWriter
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
    """SoundDevice 音频引擎实现（audiotsm WSOLA）。

    架构：
    - ``self._original_data``: 预加载的原始 PCM (float32, shape=(n_samples, channels))
    - ``self._tsm``: audiotsm.wsola 状态保持对象（整个生命周期复用）
    - ``self._reader``: ArrayReader 包装原始数据的切片 (channels, frames) 视图
    - ``self._reader_pos_samples``: 虚拟播放头（以原始采样为基准），权威位置

    回调流程：
    1. 从 WSOLA 请求 ``frames`` 个输出样本
    2. 当 WSOLA 缺原料时反复 ``read_from(self._reader)`` 喂入
    3. 若到达末尾调用 ``flush_to`` 获取尾部样本
    4. 将 (channels, frames) 转回 (frames, channels) 送声卡

    位置推进：
    - 每次回调更新 ``position_ms = reader_pos_samples / sample_rate * 1000``
    - WSOLA 吞掉多少原始采样，位置前进多少（而不是按输出帧数推进）
    """

    # 位置回调频率：60fps = ~16ms
    CALLBACK_INTERVAL = 0.016

    def __init__(self):
        # 音频数据
        self._original_data: Optional[np.ndarray] = None  # (n_samples, channels)
        self._sample_rate: int = 44100
        self._channels: int = 2
        self._file_path: Optional[str] = None
        self._duration_ms: int = 0

        # 播放状态
        self._state = PlaybackState.STOPPED
        self._position_ms: int = 0
        self._speed: float = 1.0
        self._volume: float = 1.0

        # audiotsm 流式变速对象（状态保持）
        self._tsm: Optional[audiotsm.base.tsm.TSM] = None
        self._reader: Optional[ArrayReader] = None
        self._reader_pos_samples: int = 0  # 以原始采样为基准的虚拟播放头

        # 线程控制
        self._playback_thread: Optional[Thread] = None
        self._stop_event = Event()
        self._pause_event = Event()
        self._position_lock = Lock()
        self._seek_lock = Lock()  # 保护 TSM/Reader 的重建

        # 回调
        self._position_callback: Optional[Callable[[int], None]] = None
        self._callback_thread: Optional[Thread] = None

        # 当前播放流
        self._stream: Optional[sd.OutputStream] = None

    # ==================== 加载 / 资源 ====================

    def load(self, file_path: str) -> None:
        """加载音频文件并初始化 WSOLA 状态机。"""
        try:
            data, self._sample_rate = sf.read(file_path, dtype="float32")

            if data.ndim == 1:
                data = data.reshape(-1, 1)

            self._channels = data.shape[1]
            self._file_path = file_path
            self._original_data = data

            duration_samples = len(data)
            self._duration_ms = int(duration_samples / self._sample_rate * 1000)

            self._position_ms = 0
            self._reader_pos_samples = 0
            self._state = PlaybackState.STOPPED

            # 初始化 WSOLA（按当前设置的 speed，通常为 1.0）
            self._rebuild_tsm_and_reader(self._reader_pos_samples)

        except FileNotFoundError:
            raise AudioLoadError(f"文件不存在: {file_path}")
        except Exception as e:
            raise AudioLoadError(f"加载音频失败: {e}")

    def release(self) -> None:
        """释放资源。"""
        self.stop()
        self._original_data = None
        self._tsm = None
        self._reader = None
        self._file_path = None
        self._duration_ms = 0
        self._reader_pos_samples = 0

    # ==================== 播放控制 ====================

    def play(self) -> None:
        if self._original_data is None:
            raise AudioPlaybackError("没有加载音频文件")

        if self._state == PlaybackState.PLAYING:
            return

        if self._state == PlaybackState.PAUSED:
            self._pause_event.set()
            self._state = PlaybackState.PLAYING
            return

        self._stop_event.clear()
        self._pause_event.set()
        self._state = PlaybackState.PLAYING

        self._playback_thread = Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()

        if self._position_callback:
            self._callback_thread = Thread(target=self._callback_loop, daemon=True)
            self._callback_thread.start()

    def pause(self) -> None:
        if self._state == PlaybackState.PLAYING:
            self._pause_event.clear()
            self._state = PlaybackState.PAUSED

    def stop(self) -> None:
        self._state = PlaybackState.STOPPED
        self._stop_event.set()
        self._pause_event.set()

        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=0.5)

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        with self._position_lock:
            self._position_ms = 0
        with self._seek_lock:
            self._reader_pos_samples = 0
            if self._original_data is not None:
                self._rebuild_tsm_and_reader(0)

    # ==================== 位置 ====================

    def get_position_ms(self) -> int:
        with self._position_lock:
            return self._position_ms

    def set_position_ms(self, position_ms: int) -> None:
        """拖动进度条：重建 reader 并 clear WSOLA 缓冲。"""
        if self._original_data is None:
            return

        position_ms = max(0, min(position_ms, self._duration_ms))
        target_sample = int(position_ms / 1000 * self._sample_rate)

        with self._position_lock:
            self._position_ms = position_ms
        with self._seek_lock:
            self._reader_pos_samples = target_sample
            self._rebuild_tsm_and_reader(target_sample)

    def get_duration_ms(self) -> int:
        return self._duration_ms

    # ==================== 状态查询 ====================

    def get_playback_state(self) -> PlaybackState:
        return self._state

    def is_playing(self) -> bool:
        return self._state == PlaybackState.PLAYING

    # ==================== 速度 ====================

    def set_speed(self, speed: float) -> None:
        """实时调整倍速（WSOLA 状态保持，无断裂无爆音）。"""
        if not 0.5 <= speed <= 2.0:
            raise ValueError(f"速度 {speed} 超出范围 [0.5, 2.0]")
        self._speed = speed
        if self._tsm is not None:
            # audiotsm.wsola 支持运行时修改 speed，内部相位/重叠缓冲区自动平滑
            self._tsm.set_speed(speed)

    def get_speed(self) -> float:
        return self._speed

    # ==================== 音量 ====================

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))

    def get_volume(self) -> float:
        return self._volume

    # ==================== 回调注册 ====================

    def set_position_callback(self, callback: Callable[[int], None]) -> None:
        self._position_callback = callback
        if self._state == PlaybackState.PLAYING and not self._callback_thread:
            self._callback_thread = Thread(target=self._callback_loop, daemon=True)
            self._callback_thread.start()

    def clear_position_callback(self) -> None:
        self._position_callback = None

    def get_audio_info(self) -> Optional[AudioInfo]:
        if self._file_path is None:
            return None
        return AudioInfo(
            file_path=self._file_path,
            duration_ms=self._duration_ms,
            sample_rate=self._sample_rate,
            channels=self._channels,
        )

    # ==================== WSOLA 状态管理 ====================

    def _rebuild_tsm_and_reader(self, start_sample: int) -> None:
        """重建 WSOLA 对象与 ArrayReader。

        在以下场景调用：
        - 加载音频（首次）
        - 拖动进度条（seek）
        - 停止（回到开头）

        切换倍速时**不**重建，直接调 ``tsm.set_speed`` 让内部平滑衔接。
        """
        assert self._original_data is not None
        start_sample = max(0, min(start_sample, len(self._original_data)))

        # audiotsm 约定输入是 (channels, frames)
        data_ch_first = self._original_data[start_sample:].T.astype(np.float32)
        # ArrayReader 会持有对数组的引用，不会拷贝
        self._reader = ArrayReader(data_ch_first)

        self._tsm = audiotsm.wsola(self._channels, speed=self._speed)

    # ==================== 播放循环 ====================

    def _playback_loop(self) -> None:
        """启动 sounddevice OutputStream，由 PortAudio 驱动回调。"""
        try:
            # 约 100ms 缓冲以对抗系统抖动（audiotsm 内部还有自己的块大小）
            block_size = int(self._sample_rate * 0.1)

            def callback(outdata, frames, time_info, status):
                if status:
                    # Underflow 等只打印不中断
                    print(f"Audio callback status: {status}")

                # 暂停：输出静音但保留流
                if not self._pause_event.is_set():
                    outdata.fill(0)
                    return

                # 停止：中断回调
                if self._stop_event.is_set():
                    outdata.fill(0)
                    raise sd.CallbackStop

                self._render_frames(outdata, frames)

            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="float32",
                blocksize=block_size,
                callback=callback,
            )

            with self._stream:
                while not self._stop_event.is_set():
                    time.sleep(0.1)

        except Exception as e:
            print(f"Playback error: {e}")
            self._state = PlaybackState.STOPPED

    def _render_frames(self, outdata: np.ndarray, frames: int) -> None:
        """向 WSOLA 索取 ``frames`` 个输出样本填充 outdata。

        outdata shape: (frames, channels) —— sounddevice 约定
        WSOLA I/O shape: (channels, n) —— audiotsm 约定

        audiotsm 协议（经实测确认）：
        - ``read_from(reader)`` 返回实际消耗的输入采样数（可能 < reader 剩余）
        - ``write_to(writer)`` 返回 ``(n, finished)``；``finished=True`` 表示
          TSM 暂时没有足够原料生成下一帧，需要再 ``read_from``
        - ``flush_to(writer)`` 在 reader 耗尽后获取尾部样本
        """
        with self._seek_lock:
            tsm = self._tsm
            reader = self._reader
            if tsm is None or reader is None:
                outdata.fill(0)
                return

            # Writer 在每次回调中新建 —— 它只累积本次回调需要的样本
            writer = ArrayWriter(self._channels)

            reader_exhausted = reader.empty
            flushed = False
            stuck_counter = 0  # 防死循环：连续无进展则退出

            # 循环 read_from + write_to，直到产出 ≥ frames 或数据耗尽
            while True:
                produced = writer.data.shape[1] if writer.data is not None else 0
                if produced >= frames:
                    break

                # 先尝试吐
                n_written, synth_finished = tsm.write_to(writer)

                if n_written > 0:
                    stuck_counter = 0

                if not synth_finished:
                    # 还能继续吐，回到循环头
                    continue

                # synth_finished=True：TSM 需要更多输入
                if reader_exhausted:
                    if not flushed:
                        # EOF：flush 尾部
                        _, flush_done = tsm.flush_to(writer)
                        if flush_done:
                            flushed = True
                            break
                        stuck_counter += 1
                        if stuck_counter > 3:
                            break
                    else:
                        break
                else:
                    consumed = tsm.read_from(reader)
                    if consumed > 0:
                        self._reader_pos_samples += consumed
                        new_pos_ms = int(
                            self._reader_pos_samples / self._sample_rate * 1000
                        )
                        with self._position_lock:
                            self._position_ms = min(new_pos_ms, self._duration_ms)
                        stuck_counter = 0
                    else:
                        stuck_counter += 1

                    if reader.empty:
                        reader_exhausted = True

                    if stuck_counter > 3:
                        break

        # ── 填充 outdata ──
        produced = writer.data.shape[1] if writer.data is not None else 0
        n_copy = min(produced, frames)

        if n_copy > 0:
            # writer.data: (channels, frames) → outdata: (frames, channels)
            outdata[:n_copy] = writer.data[:, :n_copy].T
        if n_copy < frames:
            outdata[n_copy:].fill(0)

        # 音量
        if self._volume != 1.0:
            outdata *= self._volume

        # 到达末尾 → 停止
        assert self._reader is not None
        if n_copy == 0 and self._reader.empty:
            self._state = PlaybackState.STOPPED
            self._stop_event.set()
            raise sd.CallbackStop

    # ==================== 位置回调线程 ====================

    def _callback_loop(self) -> None:
        last_position = -1
        while not self._stop_event.is_set():
            if self._state == PlaybackState.PLAYING and self._position_callback:
                current_position = self.get_position_ms()
                if current_position != last_position:
                    try:
                        self._position_callback(current_position)
                    except Exception as e:
                        print(f"Position callback error: {e}")
                    last_position = current_position
            time.sleep(self.CALLBACK_INTERVAL)
