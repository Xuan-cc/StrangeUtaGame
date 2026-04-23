"""离线预渲染 TSM 缓存。

设计：
- 切换播放速度（≠ 1.0x）时，后台 worker 用 audiotsm WSOLA 把整段原始 PCM
  渲染成该速度下的 PCM，作为一块连续的 ``np.ndarray`` 缓存在内存里。
- 回调线程播放时只需从对应缓存里按 sample 偏移拷贝到 ring buffer，
  **完全不在实时路径上跑 Python WSOLA**。
- 同一个 ``(audio_path, speed)`` 渲染完即长驻；最多保留 LRU 3 份。
- 1.0x 特殊路径：直接返回原始 PCM 引用，零渲染开销。

线程模型：
- 任意线程（通常 UI 线程）调 :meth:`ensure`：若目标速度的 PCM 已就绪，同步返回；
  否则安排后台渲染，立即返回 ``None``，完成后通过 ``progress_cb`` / ``done_cb`` 通知。
- 任意时刻只有一个渲染 worker 在跑；新的请求会取消前一个。
- :meth:`get` 非阻塞，查不到返回 ``None``。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Optional, Tuple

import numpy as np
import audiotsm
from audiotsm.io.array import ArrayReader, ArrayWriter


ProgressCallback = Callable[[float, float], None]  # (speed, 0.0~1.0)
DoneCallback = Callable[[float], None]              # (speed,)

_SPEED_QUANT = 2  # round(speed, 2)，0.01 精度
_LRU_MAX = 3


def _quantize(speed: float) -> float:
    return round(float(speed), _SPEED_QUANT)


class TSMRenderCache:
    """(audio_path, speed) -> rendered PCM (n_samples, channels) float32。"""

    def __init__(self) -> None:
        self._original: Optional[np.ndarray] = None  # (n, channels)
        self._sample_rate: int = 0
        self._channels: int = 0
        self._path: Optional[str] = None

        # key: (path, quantized_speed) -> ndarray (n, channels)
        self._cache: OrderedDict[Tuple[str, float], np.ndarray] = OrderedDict()

        self._worker: Optional[threading.Thread] = None
        self._worker_cancel = threading.Event()
        self._worker_speed: Optional[float] = None
        self._lock = threading.Lock()

    # ---------- 加载 ----------

    def set_source(
        self,
        path: str,
        original_pcm: np.ndarray,
        sample_rate: int,
    ) -> None:
        """切换原始音频。清空缓存。``original_pcm`` 形状 ``(n, channels)``。"""
        self._cancel_worker_and_wait()
        with self._lock:
            self._path = path
            self._original = original_pcm
            self._sample_rate = int(sample_rate)
            self._channels = int(original_pcm.shape[1])
            self._cache.clear()

    def clear(self) -> None:
        self._cancel_worker_and_wait()
        with self._lock:
            self._cache.clear()

    # ---------- 查询 ----------

    def get(self, speed: float) -> Optional[np.ndarray]:
        """非阻塞查询。命中则触碰 LRU；未命中返回 None。"""
        if self._original is None:
            return None
        q = _quantize(speed)
        if abs(q - 1.0) < 1e-9:
            return self._original  # 1.0x 直通
        key = (self._path or "", q)
        with self._lock:
            pcm = self._cache.get(key)
            if pcm is not None:
                self._cache.move_to_end(key)
            return pcm

    def has(self, speed: float) -> bool:
        return self.get(speed) is not None

    # ---------- 渲染 ----------

    def ensure(
        self,
        speed: float,
        progress_cb: Optional[ProgressCallback] = None,
        done_cb: Optional[DoneCallback] = None,
    ) -> Optional[np.ndarray]:
        """确保 ``speed`` 对应的 PCM 就绪。

        - 若已缓存：立即返回 ndarray。
        - 否则：后台开始渲染，返回 ``None``；完成时调 ``done_cb(speed)``。

        新的 ensure 调用会取消正在进行的旧渲染（如果不同 speed）。
        """
        if self._original is None:
            return None
        q = _quantize(speed)
        if abs(q - 1.0) < 1e-9:
            return self._original
        cached = self.get(q)
        if cached is not None:
            return cached

        # 需要渲染
        with self._lock:
            if self._worker is not None and self._worker_speed == q and self._worker.is_alive():
                # 已经在渲同一速度，合并
                return None

        self._cancel_worker_and_wait()

        self._worker_cancel.clear()
        self._worker_speed = q

        def _target() -> None:
            try:
                rendered = self._render_full(q, progress_cb)
                if rendered is None:
                    return  # 被取消
                with self._lock:
                    self._cache[(self._path or "", q)] = rendered
                    self._cache.move_to_end((self._path or "", q))
                    while len(self._cache) > _LRU_MAX:
                        self._cache.popitem(last=False)
                if done_cb is not None:
                    try:
                        done_cb(q)
                    except Exception as e:
                        print(f"[TSMRenderCache] done_cb error: {e}")
            except Exception as e:
                print(f"[TSMRenderCache] render error: {e}")

        t = threading.Thread(target=_target, daemon=True, name=f"TSMRender-{q}")
        self._worker = t
        t.start()
        return None

    # ---------- 内部 ----------

    def _cancel_worker_and_wait(self) -> None:
        self._worker_cancel.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        self._worker = None
        self._worker_speed = None

    def _render_full(
        self,
        speed: float,
        progress_cb: Optional[ProgressCallback],
    ) -> Optional[np.ndarray]:
        """整文件 WSOLA 渲染；返回 ``(n_samples, channels)`` float32。

        用**一个连续 ArrayReader** 保证 WSOLA 窗口相位不断裂；取消检查
        放在 ``write_to`` 的循环里（足够细粒度），进度按 reader 剩余量估算。
        """
        assert self._original is not None
        n_in = self._original.shape[0]
        if n_in == 0:
            return np.zeros((0, self._channels), dtype=np.float32)

        # audiotsm 约定输入 (channels, n)
        data_ch_first = np.ascontiguousarray(self._original.T, dtype=np.float32)
        reader = ArrayReader(data_ch_first)
        writer = ArrayWriter(self._channels)
        tsm = audiotsm.wsola(self._channels, speed=speed)

        # 主循环：参考 audiotsm.base.tsm.TSM.run —— 必须循环到
        # ``finished and reader.empty`` 同时成立，否则 in_buffer 里残留的
        # 样本不会被处理（这是早期版本截短输出的根因）。
        finished = False
        last_reported = -1.0
        while not (finished and reader.empty):
            if self._worker_cancel.is_set():
                return None
            tsm.read_from(reader)
            _, finished = tsm.write_to(writer)
            # 进度估算：reader._data 是 audiotsm 内部属性，未暴露公共接口；
            # 这里用它估算剩余输入比例，仅用于 UI 反馈，不影响正确性。
            remaining = reader._data.shape[1] if hasattr(reader, "_data") else 0
            progress = 1.0 - (remaining / n_in) if n_in else 1.0
            if progress_cb is not None and progress - last_reported > 0.02:
                try:
                    progress_cb(speed, min(progress * 0.98, 0.98))
                except Exception:
                    pass
                last_reported = progress

        # flush 尾巴（处理 in_buffer 残留 + 输出 out_buffer 残留）
        flushed = False
        while not flushed:
            if self._worker_cancel.is_set():
                return None
            _, flushed = tsm.flush_to(writer)

        # writer.data: (channels, n_out) -> (n_out, channels)
        out = np.ascontiguousarray(writer.data.T, dtype=np.float32)
        if progress_cb is not None:
            try:
                progress_cb(speed, 1.0)
            except Exception:
                pass
        return out
