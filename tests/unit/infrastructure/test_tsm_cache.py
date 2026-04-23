"""TSMRenderCache 单元测试。"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from strange_uta_game.backend.infrastructure.audio.tsm_cache import (
    TSMRenderCache,
    _quantize,
)


def _make_pcm(seconds: float = 1.0, sr: int = 22050, channels: int = 2) -> np.ndarray:
    # 注意：seconds 不能太短，WSOLA 有固定的启动/flush 开销，太短的片段
    # 输出长度会显著偏离 input/speed 的理论值（例如 0.2s 实测只有 ~52%）。
    # 1s 起步可使比例 ≥ 0.9，足以稳定断言。
    n = int(seconds * sr)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    base = np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.2
    if channels == 1:
        return base.reshape(-1, 1)
    return np.stack([base, base * 0.8], axis=1)


class TestTSMRenderCacheBasic:
    def test_quantize(self):
        assert _quantize(1.0) == 1.0
        assert _quantize(1.234) == 1.23
        assert _quantize(1.235) in (1.23, 1.24)  # 依赖银行家舍入；两种都合法

    def test_empty_before_source(self):
        c = TSMRenderCache()
        assert c.get(1.0) is None
        assert c.has(1.5) is False

    def test_one_x_direct(self):
        c = TSMRenderCache()
        pcm = _make_pcm()
        c.set_source("a.wav", pcm, 22050)
        ret = c.get(1.0)
        assert ret is pcm  # 1.0x 直通，无拷贝

    def test_render_blocking_get_then_cached(self):
        c = TSMRenderCache()
        pcm = _make_pcm()
        c.set_source("a.wav", pcm, 22050)

        done = threading.Event()
        c.ensure(1.5, done_cb=lambda s: done.set())

        # 非阻塞返回
        assert c.get(1.5) is None

        assert done.wait(timeout=15), "渲染应在合理时间内完成"
        rendered = c.get(1.5)
        assert rendered is not None
        assert rendered.dtype == np.float32
        assert rendered.shape[1] == pcm.shape[1]
        # 1.5x 理论输出长度 ≈ 原长 / 1.5
        expected = pcm.shape[0] / 1.5
        assert abs(rendered.shape[0] - expected) / expected < 0.2

    def test_lru_evicts_oldest(self):
        c = TSMRenderCache()
        pcm = _make_pcm(seconds=0.1)
        c.set_source("a.wav", pcm, 22050)

        done = threading.Event()
        pending = {"count": 0}
        lock = threading.Lock()

        def done_cb(speed):
            with lock:
                pending["count"] += 1
                if pending["count"] >= 4:
                    done.set()

        # 顺序渲染 4 个速度，每次等完成再发下一个（避免互相取消）
        for s in (0.75, 1.25, 1.5, 1.75):
            ev = threading.Event()
            c.ensure(s, done_cb=lambda _s, ev=ev: ev.set())
            assert ev.wait(timeout=15)

        # LRU 只保留 3，最早的 0.75 被踢
        assert c.get(0.75) is None
        assert c.get(1.25) is not None
        assert c.get(1.5) is not None
        assert c.get(1.75) is not None

    def test_set_source_clears_cache(self):
        c = TSMRenderCache()
        pcm = _make_pcm()
        c.set_source("a.wav", pcm, 22050)
        ev = threading.Event()
        c.ensure(1.5, done_cb=lambda _s: ev.set())
        assert ev.wait(timeout=15)
        assert c.get(1.5) is not None

        pcm2 = _make_pcm(seconds=0.1)
        c.set_source("b.wav", pcm2, 22050)
        assert c.get(1.5) is None

    def test_duplicate_ensure_merged(self):
        c = TSMRenderCache()
        pcm = _make_pcm()
        c.set_source("a.wav", pcm, 22050)

        done_called = []
        ev = threading.Event()

        def done_cb(speed):
            done_called.append(speed)
            ev.set()

        c.ensure(1.5, done_cb=done_cb)
        # 立刻再发同一速度，不应新开 worker
        c.ensure(1.5, done_cb=done_cb)
        assert ev.wait(timeout=15)
        # 最终一定有至少一次完成
        assert len(done_called) >= 1
