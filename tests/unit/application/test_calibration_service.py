"""CalibrationService 单元测试。

覆盖：
- compute_tap_offset_ms：perfect tap / early / late / pre-start / invalid interval
- filtered_average_offset_ms：空 / 小样本 / IQR 过滤 / 10% 对称 trim
"""

from __future__ import annotations

import math

import pytest

from strange_uta_game.backend.application import (
    compute_tap_offset_ms,
    filtered_average_offset_ms,
)


class TestComputeTapOffsetMs:
    def test_perfect_tap_returns_zero(self):
        # tap 正好落在第 3 拍
        start = 100.0
        interval = 0.5
        tap = start + 3 * interval
        assert compute_tap_offset_ms(tap, start, interval) == pytest.approx(0.0)

    def test_early_tap_positive(self):
        # tap 比第 2 拍早 20ms → 正值
        start = 10.0
        interval = 0.5
        tap = start + 2 * interval - 0.02
        offset = compute_tap_offset_ms(tap, start, interval)
        assert offset == pytest.approx(20.0, abs=1e-6)

    def test_late_tap_negative(self):
        # tap 比第 4 拍晚 15ms → 负值
        start = 0.0
        interval = 0.25
        tap = start + 4 * interval + 0.015
        offset = compute_tap_offset_ms(tap, start, interval)
        assert offset == pytest.approx(-15.0, abs=1e-6)

    def test_tap_before_start_returns_none(self):
        assert compute_tap_offset_ms(5.0, 10.0, 0.5) is None

    def test_zero_or_negative_interval_returns_none(self):
        assert compute_tap_offset_ms(1.0, 0.0, 0.0) is None
        assert compute_tap_offset_ms(1.0, 0.0, -0.1) is None


class TestFilteredAverageOffsetMs:
    def test_empty_returns_none(self):
        assert filtered_average_offset_ms([]) is None

    def test_small_sample_plain_mean(self):
        # < 4 个样本跳过 IQR；此时 trim_count = len // 10 == 0，不 trim
        offsets = [10.0, 20.0, 30.0]
        avg = filtered_average_offset_ms(offsets)
        assert avg == pytest.approx(20.0)

    def test_iqr_filters_extreme_outlier(self):
        # 主体在 ~10 附近，1000 是极端异常值，IQR 应过滤掉
        offsets = [8.0, 9.0, 10.0, 11.0, 12.0, 1000.0]
        avg = filtered_average_offset_ms(offsets)
        # 无 1000 的样本均值 = 10
        assert avg == pytest.approx(10.0)
        # 若未过滤 1000，均值会被拉到 ~175
        assert avg is not None and avg < 20.0

    def test_symmetric_trim_when_10_percent_nonzero(self):
        # 12 个样本 → trim_count = 12 // 10 = 1，两端各去 1
        offsets = [
            -100.0,
            0.0, 0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0, 0.0,
            100.0,
        ]
        avg = filtered_average_offset_ms(offsets)
        # IQR 不会过滤这些（Q1=0, Q3=0, IQR=0, 上下界均为 0 → 过滤掉 ±100 后回退原集）
        # 然后 10% trim 去掉 -100 和 100，剩余 10 个 0，均值 = 0
        assert avg == pytest.approx(0.0)

    def test_unsorted_input_handled(self):
        offsets = [30.0, 10.0, 20.0]
        avg = filtered_average_offset_ms(offsets)
        assert avg == pytest.approx(20.0)

    def test_all_same_values(self):
        offsets = [5.0] * 10
        avg = filtered_average_offset_ms(offsets)
        assert avg == pytest.approx(5.0)
