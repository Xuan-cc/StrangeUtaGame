"""RingBuffer 单元测试。"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from strange_uta_game.backend.infrastructure.audio.ring_buffer import RingBuffer


def _pcm(n: int, channels: int, start: float = 0.0) -> np.ndarray:
    return (np.arange(n * channels, dtype=np.float32) + start).reshape(n, channels)


class TestRingBufferBasic:
    def test_reject_zero_capacity(self):
        with pytest.raises(ValueError):
            RingBuffer(0, 2)

    def test_reject_zero_channels(self):
        with pytest.raises(ValueError):
            RingBuffer(10, 0)

    def test_empty_state(self):
        rb = RingBuffer(10, 2)
        assert rb.available_read() == 0
        assert rb.available_write() == 10

    def test_write_then_read_roundtrip(self):
        rb = RingBuffer(16, 2)
        data = _pcm(8, 2)
        assert rb.write_from(data) == 8
        assert rb.available_read() == 8

        out = np.zeros((8, 2), dtype=np.float32)
        assert rb.read_into(out) == 8
        np.testing.assert_array_equal(out, data)
        assert rb.available_read() == 0

    def test_wrap_around(self):
        rb = RingBuffer(8, 1)
        # 写 6, 读 6, 此时 read=write=6
        rb.write_from(_pcm(6, 1))
        rb.read_into(np.zeros((6, 1), dtype=np.float32))
        # 再写 5 会跨越边界
        data = _pcm(5, 1, start=100.0)
        assert rb.write_from(data) == 5
        out = np.zeros((5, 1), dtype=np.float32)
        assert rb.read_into(out) == 5
        np.testing.assert_array_equal(out, data)

    def test_overflow_returns_partial(self):
        rb = RingBuffer(4, 1)
        assert rb.write_from(_pcm(10, 1)) == 4  # 只写得下 4
        assert rb.available_write() == 0

    def test_underflow_returns_partial(self):
        rb = RingBuffer(4, 1)
        rb.write_from(_pcm(2, 1))
        out = np.zeros((5, 1), dtype=np.float32)
        assert rb.read_into(out) == 2  # 只读到 2

    def test_reset_clears(self):
        rb = RingBuffer(4, 1)
        rb.write_from(_pcm(3, 1))
        rb.reset()
        assert rb.available_read() == 0
        assert rb.available_write() == 4

    def test_shape_validation(self):
        rb = RingBuffer(4, 2)
        with pytest.raises(ValueError):
            rb.write_from(np.zeros((3, 1), dtype=np.float32))
        with pytest.raises(ValueError):
            rb.read_into(np.zeros((3, 1), dtype=np.float32))

    def test_write_zero_length_noop(self):
        rb = RingBuffer(4, 2)
        assert rb.write_from(np.zeros((0, 2), dtype=np.float32)) == 0

    def test_read_zero_length_noop(self):
        rb = RingBuffer(4, 2)
        rb.write_from(_pcm(2, 2))
        assert rb.read_into(np.zeros((0, 2), dtype=np.float32)) == 0
        assert rb.available_read() == 2


class TestRingBufferConcurrent:
    """单生产者 / 单消费者下的线程安全基本属性：
    - 总写入帧数 == 总读取帧数 (最终)
    - 读到的数据按写入顺序拼接后逐字节一致
    """

    def test_spsc_monotonic_sequence(self):
        rb = RingBuffer(1024, 1)
        n_total = 100_000

        produced_done = threading.Event()

        def producer():
            i = 0
            while i < n_total:
                chunk = min(300, n_total - i)
                block = np.arange(i, i + chunk, dtype=np.float32).reshape(chunk, 1)
                while True:
                    written = rb.write_from(block)
                    if written == chunk:
                        break
                    # 写不下，等一下消费
                    if written > 0:
                        block = block[written:]
                        chunk -= written
                    # 无阻塞：让出时间片
                i += int(block.shape[0]) if written == 0 else 0
                # 上面的循环已经把整个 block 写完才跳出
                i = min(n_total, i + (0 if written == 0 else 0))
                # 简化：直接按 n_total 推进（上面的内层循环保证整 block 写入）
            produced_done.set()

        # 用更简单稳健的生产者循环替换上面的复杂版本
        def producer_simple():
            i = 0
            rng = np.arange(n_total, dtype=np.float32).reshape(-1, 1)
            while i < n_total:
                chunk = min(300, n_total - i)
                block = rng[i : i + chunk]
                offset = 0
                while offset < chunk:
                    w = rb.write_from(block[offset:])
                    offset += w
                i += chunk
            produced_done.set()

        collected = np.zeros((n_total, 1), dtype=np.float32)
        read_pos = 0

        def consumer():
            nonlocal read_pos
            tmp = np.zeros((512, 1), dtype=np.float32)
            while read_pos < n_total:
                r = rb.read_into(tmp)
                if r > 0:
                    collected[read_pos : read_pos + r] = tmp[:r]
                    read_pos += r

        t_p = threading.Thread(target=producer_simple)
        t_c = threading.Thread(target=consumer)
        t_p.start()
        t_c.start()
        t_p.join(timeout=10)
        t_c.join(timeout=10)

        assert read_pos == n_total
        expected = np.arange(n_total, dtype=np.float32).reshape(-1, 1)
        np.testing.assert_array_equal(collected, expected)
