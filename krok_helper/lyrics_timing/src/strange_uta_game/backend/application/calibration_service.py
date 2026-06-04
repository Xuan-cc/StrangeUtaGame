"""节拍器 Offset 校准 — 纯数学服务。

从前端 ``calibration_dialog`` 抽出，仅包含与 UI / 音频 I/O 无关的计算，
便于单元测试和跨前端复用。

Public API
----------
- :func:`compute_tap_offset_ms` — 根据 tap 时间与节拍参数计算单次偏移（毫秒）
- :func:`filtered_average_offset_ms` — IQR + trim 平均，得到稳定的整体偏移估计
"""

from __future__ import annotations

from typing import Iterable, List, Optional


def compute_tap_offset_ms(
    tap_time: float, start_time: float, beat_interval: float
) -> Optional[float]:
    """计算单次 tap 相对最近一个节拍的偏移（毫秒）。

    正值 = 按早了（tap 在完美时间之前），负值 = 按晚了。

    Args:
        tap_time: 用户按键的绝对时间（秒，通常来自 ``time.perf_counter()``）。
        start_time: 节拍器启动的绝对时间（秒）。
        beat_interval: 每拍时长（秒），必须 > 0。

    Returns:
        偏移毫秒；若 ``tap_time < start_time`` 或 ``beat_interval <= 0`` 返回 ``None``。
    """
    if beat_interval <= 0:
        return None
    elapsed = tap_time - start_time
    if elapsed < 0:
        return None
    n = round(elapsed / beat_interval)
    perfect_time = start_time + n * beat_interval
    return (perfect_time - tap_time) * 1000.0


def filtered_average_offset_ms(offsets: Iterable[float]) -> Optional[float]:
    """对一组 tap 偏移做 IQR 去极端 + 10% 对称 trim 后取平均。

    算法（与原 ``_filtered_average_offset_ms`` 等价）：
    1. 样本少于 4 条时跳过 IQR；
    2. 否则保留 ``[Q1 - 1.5 IQR, Q3 + 1.5 IQR]`` 范围内的值（空则回退原集）；
    3. 再掐掉两端各 ``len // 10`` 个极端值（需保证剩余数量 > 0）；
    4. 返回算术平均。

    Args:
        offsets: 偏移毫秒的序列（不要求已排序）。

    Returns:
        平均偏移（毫秒），若输入为空返回 ``None``。
    """
    values: List[float] = sorted(offsets)
    if not values:
        return None

    filtered = values
    if len(values) >= 4:
        q1 = values[len(values) // 4]
        q3 = values[len(values) * 3 // 4]
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        filtered = [v for v in values if lower <= v <= upper]
        if not filtered:
            filtered = values

    trim_count = len(filtered) // 10
    trimmed = filtered
    if trim_count > 0 and len(filtered) - trim_count * 2 > 0:
        trimmed = filtered[trim_count : len(filtered) - trim_count]

    return sum(trimmed) / len(trimmed)
