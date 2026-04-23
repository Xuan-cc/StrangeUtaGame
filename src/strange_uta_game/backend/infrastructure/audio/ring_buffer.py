"""单生产者 / 单消费者 音频 Ring Buffer。

用于将"TSM 预渲染好的 PCM"从生产者线程安全、零锁、零分配地
喂给 PortAudio 回调线程。

关键性质：
- 读写头用 ``threading.Lock`` 保护（只在极短指针更新段持锁），
  实际 ``np.copyto`` 不持锁，完全无争用。
- 回调线程只调 :meth:`read_into`，生产者线程只调 :meth:`write_from`。
- 缓冲区大小固定，环形覆盖；生产者若写入超过剩余容量则本次丢弃超出部分
  （返回实际写入数），由调用方决定何时再次尝试。
- 形状固定为 ``(capacity_frames, channels)`` float32。

不支持：
- 多生产者或多消费者（会破坏无锁假设）
- 动态扩容（用 :meth:`reset` 丢弃历史）
"""

from __future__ import annotations

import threading

import numpy as np


class RingBuffer:
    """(frames, channels) float32 环形缓冲。

    Args:
        capacity_frames: 能缓存的最大帧数。
        channels: 声道数（1 或 2）。
    """

    def __init__(self, capacity_frames: int, channels: int) -> None:
        if capacity_frames <= 0:
            raise ValueError("capacity_frames must be > 0")
        if channels <= 0:
            raise ValueError("channels must be > 0")

        self._capacity = int(capacity_frames)
        self._channels = int(channels)
        # 多留一格：经典环形区分"满/空"技巧
        self._buf = np.zeros((self._capacity + 1, self._channels), dtype=np.float32)
        self._write = 0  # 下一个要写入的位置
        self._read = 0   # 下一个要读取的位置
        self._lock = threading.Lock()

    # ---------- 状态 ----------

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def capacity(self) -> int:
        return self._capacity

    def available_read(self) -> int:
        """可读帧数（当前生产者已写入且消费者尚未读走）。"""
        with self._lock:
            return self._available_read_nolock()

    def available_write(self) -> int:
        """剩余可写帧数。"""
        with self._lock:
            return self._capacity - self._available_read_nolock()

    def _available_read_nolock(self) -> int:
        if self._write >= self._read:
            return self._write - self._read
        return (self._capacity + 1) - self._read + self._write

    # ---------- 操作 ----------

    def reset(self) -> None:
        """丢弃全部内容，读写头复位。用于 seek / 切速度。

        调用方须保证此时回调线程已被隔离（例如暂停或预先已知不会再读）。
        """
        with self._lock:
            self._write = 0
            self._read = 0

    def write_from(self, data: np.ndarray) -> int:
        """生产者：写入 ``data`` (n, channels)，返回实际写入帧数。

        当剩余容量不足时，只写入剩余容量（不阻塞）。
        """
        if data.ndim != 2 or data.shape[1] != self._channels:
            raise ValueError(
                f"data shape expected (n, {self._channels}), got {data.shape}"
            )
        n = int(data.shape[0])
        if n == 0:
            return 0

        with self._lock:
            free = self._capacity - self._available_read_nolock()
            to_write = min(n, free)
            if to_write == 0:
                return 0
            w = self._write
            cap1 = self._capacity + 1
            end = w + to_write
            if end <= cap1:
                self._buf[w:end] = data[:to_write]
            else:
                first = cap1 - w
                self._buf[w:cap1] = data[:first]
                self._buf[0 : to_write - first] = data[first:to_write]
            self._write = (w + to_write) % cap1
            return to_write

    def read_into(self, out: np.ndarray) -> int:
        """消费者（**回调线程**）：把至多 ``len(out)`` 帧写入 ``out``。

        返回实际读取帧数。**零分配、非阻塞**。
        """
        if out.ndim != 2 or out.shape[1] != self._channels:
            raise ValueError(
                f"out shape expected (n, {self._channels}), got {out.shape}"
            )
        n = int(out.shape[0])
        if n == 0:
            return 0

        with self._lock:
            avail = self._available_read_nolock()
            to_read = min(n, avail)
            if to_read == 0:
                return 0
            r = self._read
            cap1 = self._capacity + 1
            end = r + to_read
            if end <= cap1:
                out[:to_read] = self._buf[r:end]
            else:
                first = cap1 - r
                out[:first] = self._buf[r:cap1]
                out[first:to_read] = self._buf[0 : to_read - first]
            self._read = (r + to_read) % cap1
            return to_read
