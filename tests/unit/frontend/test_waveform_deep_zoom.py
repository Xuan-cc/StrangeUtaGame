"""深放大波形升级：回退选层 bug、直读路径、动态缩放上限、逐采样视图。

对应 SV 口径的三件事：
- 峰值层不够细时长音频深放大回退必须选**最细**可用层（旧 bug 选最粗）；
- 粒度深于最细缓存层时直接归约原始样本（SV getSummaries direct-read 分支）；
- 缩放上限按绝对采样深度动态放宽（长音频可放到 ~0.25 采样/像素），
  spp<1 时切换为过采样平滑曲线 + 采样点方块（SV PixelsPerFrame 区间）。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from strange_uta_game.backend.infrastructure.audio import spectrum
from strange_uta_game.frontend.editor.timing.timeline_widget import (
    TimelineWidget,
    WaveformDisplay,
)

SR = 44100


# ── DSP 助手（纯 numpy，无 Qt） ──────────────────────────────


class TestReducePeaksByEdges:
    def test_basic_segments(self):
        samples = np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        edges = np.array([1, 3, 3, 5])
        mins, maxs, rmss = spectrum.reduce_peaks_by_edges(samples, edges)
        # 像素 0：[1,3)={1,2}；像素 1：空段=0；像素 2：[3,5)={3,4}
        assert mins[0] == 1.0 and maxs[0] == 2.0
        assert mins[1] == 0.0 and maxs[1] == 0.0 and rmss[1] == 0.0
        assert mins[2] == 3.0 and maxs[2] == 4.0
        assert rmss[0] == pytest.approx(math.sqrt((1.0 + 4.0) / 2), rel=1e-5)
        assert rmss[2] == pytest.approx(math.sqrt((9.0 + 16.0) / 2), rel=1e-5)

    def test_out_of_range_edges_are_clipped(self):
        samples = np.ones(4, dtype=np.float32)
        edges = np.array([-10, 2, 100])  # clamp 到 [0,4]
        mins, maxs, rmss = spectrum.reduce_peaks_by_edges(samples, edges)
        # 像素 0：[0,2)；像素 1：[2,4)
        assert maxs[0] == 1.0 and maxs[1] == 1.0
        assert rmss[0] == pytest.approx(1.0, rel=1e-6)


class TestOversampleWindowedSinc:
    def test_integer_positions_reproduce_samples(self):
        rng = np.random.default_rng(42)
        samples = rng.uniform(-1, 1, 1000).astype(np.float32)
        out = spectrum.oversample_windowed_sinc(
            samples, np.arange(1000, dtype=np.float64)
        )
        np.testing.assert_allclose(out, samples, atol=2e-3)

    def test_smooth_sine_between_samples(self):
        # 低频正弦的半采样偏移处，插值应接近真值（带限信号 sinc 插值准重建）
        positions = np.arange(2000, dtype=np.float64) + 0.5
        true = np.sin(2 * np.pi * 0.01 * positions)
        samples = np.sin(2 * np.pi * 0.01 * np.arange(2001, dtype=np.float64)).astype(
            np.float32
        )
        out = spectrum.oversample_windowed_sinc(samples, positions)
        np.testing.assert_allclose(out, true, atol=0.01)

    def test_zero_outside_signal_no_normalization(self):
        samples = np.ones(100, dtype=np.float32)
        out = spectrum.oversample_windowed_sinc(samples, np.array([-50.0, 150.0]))
        # 界外按 0 参与求和且不归一化：边缘幅度自然衰减到 0
        assert abs(out[0]) < 1e-6 and abs(out[1]) < 1e-6


# ── 回退选层 bug（Phase 0） ──────────────────────────────


def test_fallback_returns_finest_level_not_coarsest(qapp):
    """全部缓存层都粗于目标时必须回退到**最细**层（旧 bug 选最粗层，
    长音频深放大整屏糊成实心砖块）。"""
    display = WaveformDisplay()
    rows = 4
    display._waveform_peak_levels[16] = (
        np.zeros(rows, dtype=np.float32),
        np.full(rows, 0.5, dtype=np.float32),
        np.full(rows, 0.1, dtype=np.float32),
    )
    display._waveform_peak_levels[64] = (
        np.zeros(1, dtype=np.float32),
        np.full(1, 0.9, dtype=np.float32),
        np.full(1, 0.3, dtype=np.float32),
    )

    bin_size, (mins, maxs, _rmss) = display._find_cached_fallback_level(4)

    assert bin_size == 16
    assert maxs[0] == 0.5  # 最细层的数据，而不是最粗层的 0.9


# ── 直读路径（Phase 1） ──────────────────────────────


def _make_display(duration_ms: int, samples: np.ndarray, zoom: float) -> WaveformDisplay:
    display = WaveformDisplay()
    display.set_duration(duration_ms)
    display.set_audio_data(samples, SR, 1)
    display.set_zoom(zoom)
    return display


def test_deep_zoom_direct_read_matches_reference(qapp):
    """目标粒度深于最细缓存层时直读原始样本，逐像素 min/max/RMS 与
    参考归约一致（不受 bin 量化涂抹）。"""
    n = SR  # 1s
    samples = np.linspace(-1, 1, n, dtype=np.float32)
    samples[12345] = 1.0  # 瞬态
    display = _make_display(1000, samples, zoom=1.0)
    display.resize(100, 120)
    # 模拟长音频的内存预算上限：只剩 4096/采样 的粗层可用
    rows = (n + 4095) // 4096
    display._waveform_peak_levels.clear()
    display._waveform_peak_levels[4096] = (
        np.zeros(rows, dtype=np.float32),
        np.full(rows, 0.5, dtype=np.float32),
        np.full(rows, 0.25, dtype=np.float32),
    )

    width = 100
    peaks = display._compute_waveform_peaks(width)

    assert peaks is not None and len(peaks) == width
    # 参考：像素 i 归约 [floor(左端时间→采样), floor(下一像素左端))——与
    # 实现同一条 ms→采样换算（验证的是 reduceat 归约管线而非浮点确定性）
    ms_per_pixel = 1000.0 / width
    edges = [
        int(np.floor((i * ms_per_pixel) / 1000.0 * SR)) for i in range(width + 1)
    ]
    edges[-1] = min(edges[-1], n)
    for i in range(width):
        seg = samples[edges[i] : edges[i + 1]]
        lo, hi, rms = peaks[i]
        assert lo == pytest.approx(float(seg.min()), abs=1e-6)
        assert hi == pytest.approx(float(seg.max()), abs=1e-6)
        assert rms == pytest.approx(
            float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))), rel=1e-4
        )
    # 瞬态保留：覆盖样本 12345 的像素必须看到 1.0
    transient_pixel = next(
        i for i in range(width) if edges[i] <= 12345 < edges[i + 1]
    )
    assert peaks[transient_pixel][1] == pytest.approx(1.0, abs=1e-6)


def test_long_audio_deep_zoom_without_levels_uses_direct_read(qapp):
    """长音频预热未完成（无任何缓存层）+ 深放大可见采样有界 → 直读而非
    占位。这正是旧版"回退最粗层/无层占位"最伤的场景。"""
    duration_ms = 60_000
    n = SR * 60
    samples = np.zeros(n, dtype=np.float32)
    transient_sample = 1_000_000  # ≈22675.7ms
    samples[transient_sample] = 1.0
    display = _make_display(duration_ms, samples, zoom=600.0)  # 可见 100ms
    display.resize(100, 120)
    display._waveform_peak_levels.clear()  # 模拟预热未完成

    # 滚到瞬态附近：可见窗 [22625.7, 22725.7)ms
    t_ms = transient_sample / SR * 1000.0
    display.set_scroll_position((t_ms - 50.0) / duration_ms)

    peaks = display._compute_waveform_peaks(100)

    assert peaks is not None
    assert max(hi for _, hi, _ in peaks) == pytest.approx(1.0, abs=1e-6)


# ── 动态缩放上限（Phase 2） ──────────────────────────────


def test_zoom_cap_absolute_depth(qapp):
    """上限随音频长度/屏宽放宽到 ~0.25 采样/像素；无音频时保持基准值。"""
    display = WaveformDisplay()
    assert display.zoom_cap() == WaveformDisplay._MAX_ZOOM

    display.resize(1200, 120)
    display.set_duration(180_000)  # 3min
    display.set_audio_data(np.zeros(SR * 180, dtype=np.float32), SR, 1)

    expected = SR * 180 / (1200 * WaveformDisplay._ZOOM_CAP_MIN_SPP)
    assert display.zoom_cap() == pytest.approx(expected, rel=1e-9)
    assert display.zoom_cap() > 20_000  # 远超旧 1000x 上限

    # set_zoom / 滚轮共用同一 clamp
    display.set_zoom(10**9)
    assert display._zoom_factor == pytest.approx(display.zoom_cap(), rel=1e-9)


def test_zoom_cap_shrinks_and_reclamps_on_shorter_audio(qapp):
    display = WaveformDisplay()
    display.resize(1200, 120)
    display.set_duration(180_000)
    display.set_audio_data(np.zeros(SR * 180, dtype=np.float32), SR, 1)
    display.set_zoom(20_000)  # 长音频下合法

    # 换短音频：上限收回，zoom 必须被压回范围内，否则可见时长为负
    display.set_duration(1000)
    display.set_audio_data(np.zeros(SR, dtype=np.float32), SR, 1)
    assert display._zoom_factor <= display.zoom_cap()
    assert display._visible_duration_ms() > 0


def test_slider_mapping_roundtrip_with_dynamic_cap(qapp):
    timeline = TimelineWidget()
    timeline.waveform_display.resize(640, 120)
    timeline.waveform_display.set_duration(180_000)
    timeline.waveform_display.set_audio_data(
        np.zeros(SR * 180, dtype=np.float32), SR, 1
    )
    cap = timeline.waveform_display.zoom_cap()

    # 滑杆两端 = 1x 与动态上限；任意值往返一致（整数滑杆量化容差 ~0.1%）
    assert timeline._slider_to_zoom(0) == pytest.approx(1.0)
    assert timeline._slider_to_zoom(10000) == pytest.approx(cap, rel=1e-9)
    for zoom in (1.0, 50.0, 1000.0, cap / 3):
        assert timeline._slider_to_zoom(timeline._zoom_to_slider(zoom)) == (
            pytest.approx(zoom, rel=5e-3)
        )


# ── 逐采样视图（Phase 3）+ 批量绘制（Phase 4） ────────────────


def test_sample_view_zone_values_and_anchors(qapp):
    """spp<1：每像素一个过采样值 + 可见采样的锚点方块数据。"""
    n = SR
    samples = np.zeros(n, dtype=np.float32)
    samples[20] = 1.0  # 单点瞬态：sinc 重建在锚点附近应显著抬起
    display = _make_display(1000, samples, zoom=1000.0)  # 可见 1ms
    display.resize(200, 120)
    assert display._samples_per_pixel() < 1.0

    view = display._compute_sample_view(200)

    values, anchor_x, anchor_v = view
    assert len(values) == 200
    # 瞬态原始值必须以锚点方块形式出现（描点画的是真实采样值）
    assert 1.0 in anchor_v
    assert anchor_x.min() >= 0 and anchor_x.max() < 200
    # 过采样曲线在瞬态附近抬起：采样 20 ≈ x=20/44.1ms/0.005ms ≈ 90.7px
    assert abs(values[90:92]).max() > 0.5
    # 音频范围外（本例可见窗=0..1ms，覆盖前 44 个采样，无界外像素）；
    # 曲线整体有界
    assert np.abs(values).max() <= 1.0 + 1e-3


def test_sample_view_cache_hit(qapp):
    n = SR
    samples = np.sin(np.arange(n, dtype=np.float32) * 0.05).astype(np.float32)
    display = _make_display(1000, samples, zoom=1000.0)
    display.resize(200, 120)

    first = display._compute_sample_view(200)
    second = display._compute_sample_view(200)

    assert first is second


def test_rasterized_bars_fill_physical_resolution_without_stripes(qapp):
    """HiDPI（Windows 125%/150%/200% 缩放）下必须按物理像素出图，且包络
    每个物理列都有峰值内容；不能每个逻辑列只画中间 1 个物理列，否则
    125%~200% 屏幕会出现用户截图中的规则蓝色“栅栏”。"""
    from PyQt6.QtGui import QImage, QPainter

    display = WaveformDisplay()
    display._waveform_rms_enabled = False  # 只测包络竖线
    peaks = [(-1.0, 1.0, 0.5)] * 100

    for dpr in (1.0, 1.5, 2.0):
        device = QImage(100, 60, QImage.Format.Format_ARGB32_Premultiplied)
        device.setDevicePixelRatio(dpr)
        painter = QPainter(device)
        image = display._rasterize_waveform_bars(painter, 100, 60, peaks)
        painter.end()

        assert image is not None
        assert image.devicePixelRatioF() == pytest.approx(dpr)
        # 物理尺寸 = 逻辑尺寸 × dpr（blit 时 1:1，无重采样）
        assert image.width() == math.ceil(100 * dpr)
        assert image.height() == math.ceil(60 * dpr)

        raw = np.frombuffer(
            image.constBits().asstring(image.sizeInBytes()), dtype=np.uint32
        )
        stride = image.bytesPerLine() // 4
        filled = (raw != 0).reshape(image.height(), stride)[
            :, : image.width()
        ]
        # 满幅波形应覆盖全部物理列，不随 DPR 产生空列。
        assert filled.mean() == pytest.approx(1.0, abs=0.01)
        if dpr == 2.0:
            row = filled[image.height() // 2]
            assert row.all()


def test_rasterized_bars_connect_trivial_ranges(qapp):
    """SV 情况 B：幅值不足 1px 高（trivialRange）的相邻列之间必须有
    中点连接线——否则低谷/细尾部是散点，"峰与峰之间全是间隙"。"""
    from PyQt6.QtGui import QImage, QPainter

    display = WaveformDisplay()
    display._waveform_rms_enabled = False
    # 全部列都是极小幅度（trivial）：SV 画一条穿过各列中点的连续细线
    peaks = [(-0.01, 0.01, 0.0)] * 100

    dpr = 2.0
    device = QImage(100, 60, QImage.Format.Format_ARGB32_Premultiplied)
    device.setDevicePixelRatio(dpr)
    painter = QPainter(device)
    image = display._rasterize_waveform_bars(painter, 100, 60, peaks)
    painter.end()

    raw = np.frombuffer(
        image.constBits().asstring(image.sizeInBytes()), dtype=np.uint32
    )
    stride = image.bytesPerLine() // 4
    filled = (raw != 0).reshape(image.height(), stride)[:, : image.width()]
    # 竖线列（奇）+ 连接线补的空隙列（偶）→ 中点附近存在整行连续的细线
    # （最左半列在第一条竖线之前，为空是正确的）
    assert filled[:, 1:].any(axis=0).all(), "首列之后每列都应有内容"
    assert filled.sum(axis=1).max() == image.width(), "中点行必须整行连续"


def test_render_static_layer_both_zones_smoke(qapp):
    """包络区间与逐采样区间的渲染都不报错（含亚毫秒网格标注）。"""
    n = SR
    samples = np.sin(np.arange(n, dtype=np.float32) * 0.02).astype(np.float32)
    display = _make_display(1000, samples, zoom=1000.0)
    display.resize(300, 120)

    # 逐采样区间（spp<1）：可见 1ms，网格细分到 0.1ms 档
    layer = display._render_static_layer(300, 120, 0.0, 1.0, 1.0)
    assert not layer.isNull()

    # 包络区间（spp≥1）：可见 200ms
    display.set_zoom(5.0)
    layer = display._render_static_layer(300, 120, 0.0, 200.0, 200.0)
    assert not layer.isNull()
