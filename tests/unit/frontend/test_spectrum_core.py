from __future__ import annotations

import math

import numpy as np
import pytest

from strange_uta_game.backend.infrastructure.audio import spectrum as sc

SR = 44100


def _sine(freq: float, seconds: float, amp: float = 0.8) -> np.ndarray:
    t = np.arange(int(SR * seconds), dtype=np.float64) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _click_track(bpm: float, seconds: float, seed: int = 7) -> np.ndarray:
    """每拍一个短促宽带打击（指数衰减噪声脉冲），模拟节拍轨。"""
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    x = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm * SR
    decay = np.exp(-np.arange(200) / 40.0).astype(np.float32)
    for p in np.arange(0, (n - 300) / period):
        i = int(round(p * period))
        x[i : i + 200] += decay * rng.standard_normal(200).astype(np.float32)
    return x


def _bandpass_track(bpm: float, seconds: float) -> np.ndarray:
    """低频鼓点（每拍）+ 高频击打（每半拍），更接近真实编配。"""
    n = int(SR * seconds)
    x = np.zeros(n, dtype=np.float32)
    t = np.arange(300) / SR
    kick = (np.sin(2 * np.pi * 60.0 * t) * np.exp(-t / 0.04)).astype(np.float32)
    hat = (
        np.random.default_rng(11).standard_normal(80) * np.exp(-np.arange(80) / 12.0)
    ).astype(np.float32)
    period = 60.0 / bpm * SR
    for p in np.arange(0, (n - 400) / period):
        i = int(round(p * period))
        x[i : i + 300] += 0.8 * kick
        j = i + int(period * 0.5)
        x[j : j + 80] += 0.2 * hat
    return x


class TestComputeSpectrogram:
    def test_peak_bin_matches_tone_frequency(self):
        result = sc.compute_spectrogram(_sine(440.0, 2.0), SR, 2048)
        assert result is not None
        matrix = result["matrix"]
        assert matrix.dtype == np.uint8
        assert result["hop"] == 2048 // 4
        peak_bin = int(np.argmax(matrix.max(axis=0)))
        assert peak_bin == pytest.approx(440.0 * 2048 / SR, abs=2)

    def test_silence_quantizes_to_floor_and_tone_to_high(self):
        silence = sc.compute_spectrogram(np.zeros(int(SR * 1.0), np.float32), SR, 1024)
        tone = sc.compute_spectrogram(_sine(1000.0, 1.0), SR, 1024)
        assert silence["matrix"].max() <= 3
        assert tone["matrix"].max() >= 200

    def test_short_audio_pads_to_single_frame(self):
        result = sc.compute_spectrogram(np.ones(100, np.float32), SR, 2048)
        assert result is not None
        assert result["matrix"].shape[0] == 1

    def test_tone_db_scale_matches_amplitude(self):
        """dB 标尺与信号幅度对齐：满幅正弦 ≈ 0dB，且无重复 +6dB 补偿。"""
        for amp in (0.8, 0.1):
            result = sc.compute_spectrogram(_sine(440.0, 2.0, amp=amp), SR, 2048)
            assert result is not None
            measured_db = int(result["matrix"].max()) * 128.0 / 255.0 - 128.0
            expected_db = 20.0 * math.log10(amp)
            assert measured_db == pytest.approx(expected_db, abs=1.5)

    def test_cancel_returns_none(self):
        x = _sine(440.0, 3.0)
        assert sc.compute_spectrogram(x, SR, 2048, cancel_check=lambda: True) is None

    def test_dc_and_nyquist_not_double_compensated(self):
        """单边谱二倍补偿只适用于内部 bin：DC/Nyquist 乘 2 会多算 6.02dB。

        0.5 幅度的 DC、Nyquist、内部 bin 正弦三种信号应读出同一 dB
        （≈ -6.02dB）。取整窗覆盖的中间帧（居中加窗，帧心=k·hop）。
        """
        fft, hop = 2048, 512
        k = 100  # 内部 bin（正弦频率取 bin 中心）
        expect = int(((20.0 * math.log10(0.5) + 128.0) * 255.0 / 128.0))

        def mid_val(matrix, bin_idx):
            return int(matrix[matrix.shape[0] // 2, bin_idx])

        dc = sc.compute_spectrogram(np.full(fft, 0.5, np.float32), SR, fft, hop)
        nyq = (0.5 * (-1.0) ** np.arange(fft)).astype(np.float32)
        ny = sc.compute_spectrogram(nyq, SR, fft, hop)
        t = np.arange(fft) / SR
        sine = (
            0.5 * np.sin(2 * np.pi * (k * SR / fft) * t)
        ).astype(np.float32)
        si = sc.compute_spectrogram(sine, SR, fft, hop)

        dc_v = mid_val(dc["matrix"], 0)
        ny_v = mid_val(ny["matrix"], -1)
        si_v = mid_val(si["matrix"], k)
        assert si_v == pytest.approx(expect, abs=2)  # 内部 bin 标尺不变
        assert dc_v == pytest.approx(si_v, abs=2)  # DC 不再多 6.02dB
        assert ny_v == pytest.approx(si_v, abs=2)  # Nyquist 不再多 6.02dB

    def test_odd_fft_has_no_nyquist_exception(self):
        """奇数 FFT 的最后一个 rfft bin 是内部 bin，保持二倍补偿。"""
        fft, hop = 1023, 256
        x = np.zeros(fft * 4, dtype=np.float32)
        res = sc.compute_spectrogram(x, SR, fft, hop)
        assert res["matrix"].shape[1] == fft // 2 + 1  # 不崩、形状正确

    def test_tail_pulse_enters_last_frames(self):
        """P2-3 复现用例：结尾不足一窗的脉冲不得从声谱中消失。

        len=2559、fft=2048、hop=512：旧 floor 口径只产出 1 帧、脉冲完全
        排除在窗外；居中加窗后脉冲落在末尾若干帧的高权区，清晰可见。
        """
        n, fft, hop = 2559, 2048, 512
        x = np.zeros(n, dtype=np.float32)
        x[2558] = 1.0
        res = sc.compute_spectrogram(x, SR, fft, hop)
        assert res["matrix"].shape[0] == sc.frame_count(n, fft, hop)
        assert res["matrix"].shape[0] > 1
        assert int(res["matrix"].max()) > 50  # 脉冲能量可见（旧=0）

    def test_frame_count_matches_matrix_and_estimate(self):
        """帧数公式三处一致：compute 实际矩阵 / frame_count / 内存预算估算。"""
        for length in (1, 100, 2047, 2048, 2559, 4096, SR * 3):
            for fft, hop in ((2048, 512), (1024, 256), (8192, 4096)):
                x = np.zeros(length, dtype=np.float32)
                res = sc.compute_spectrogram(x, SR, fft, hop)
                assert res["matrix"].shape[0] == sc.frame_count(length, fft, hop)
                assert sc.estimate_matrix_bytes(length, SR, fft, hop) == (
                    sc.frame_count(length, fft, hop) * (fft // 2 + 1)
                )


class TestFrameChunkMemory:
    """P2-1：居中加窗不得整曲复制；取消不得先复制再退出。

    np.pad 曾对整首 mono 复制（10 分钟 ≈106MB、1 小时 ≈635MB），且
    发生在任何取消检查之前——已取消任务也会先完成整次复制。
    """

    def test_chunks_match_padded_reference(self):
        """分块产出与「整曲 np.pad + 滑窗」参考逐元素一致（重构等价性）。"""
        rng = np.random.default_rng(7)
        samples = rng.standard_normal(3000).astype(np.float32)
        fft, hop = 1024, 256
        n_frames = sc.frame_count(len(samples), fft, hop)
        front = fft // 2
        needed = (n_frames - 1) * hop + fft
        padded = np.pad(samples, (front, needed - len(samples) - front))
        ref = np.lib.stride_tricks.sliding_window_view(padded, fft)[::hop]

        total = 0
        for i0, blk in sc._iter_frame_chunks(samples, fft, hop, 7):
            m = blk.shape[0]
            np.testing.assert_array_equal(blk, ref[i0 : i0 + m])
            total += m
        assert total == n_frames

    def test_first_chunk_allocates_fixed_small_buffer(self):
        import tracemalloc

        samples = np.zeros(SR * 60, dtype=np.float32)  # 60s ≈ 10.6MB
        it = sc._iter_frame_chunks(samples, 2048, 512, 64)
        tracemalloc.start()
        i0, blk = next(it)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert i0 == 0 and blk.shape == (64, 2048)
        # chunk 本体 0.5MB + 头部补零缓冲 ≈0.14MB：固定小额，不随输入
        # 长度增长（np.pad 旧实现实测峰值 = 输入等大 10.6MB）
        assert peak < 2 * 1024 * 1024

    def test_immediate_cancel_allocates_fixed_small_overhead(self):
        """任务启动前已取消：只有固定小额开销，不随输入长度增长。

        修复前 10 分钟音频实测：声谱先分配 53MB、BPM 先分配 27MB 的
        整段结果矩阵（mel_power/out）之后才退出。不能用
        ``peak < 输入大小`` 做标准——输入越长，允许的无效分配越大。
        """
        import tracemalloc

        samples = np.zeros(SR * 600, dtype=np.float32)  # 10 分钟
        tracemalloc.start()
        assert sc.compute_spectrogram(
            samples, SR, 2048, cancel_check=lambda: True
        ) is None
        _, peak_spec = tracemalloc.get_traced_memory()
        bpm = sc.detect_bpm(samples, SR, cancel_check=lambda: True)
        _, peak_total = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert bpm["bpm"] is None
        assert peak_spec < 1024 * 1024  # 修复前 ≈53MB
        assert peak_total - peak_spec < 1024 * 1024  # BPM 修复前 ≈27MB

    def test_midway_cancel_returns_none(self):
        """首块处理完后取消：生成器提前停止，后置守卫拒收半成品。"""
        state = {"cancel": False}

        def progress(_v):
            state["cancel"] = True

        x = _sine(440.0, 2.0)
        result = sc.compute_spectrogram(
            x, SR, 2048, progress_cb=progress,
            cancel_check=lambda: state["cancel"],
        )
        assert result is None

    def test_progress_is_reported(self):
        seen = []
        sc.compute_spectrogram(_sine(440.0, 5.0), SR, 1024, progress_cb=seen.append)
        assert seen and seen[-1] == 1.0


class TestPyramid:
    def test_level_alignment_two_frame_groups(self):
        # 金字塔只在行数 > 256 时向上建层，用足够长的矩阵验证。
        matrix = (
            np.random.default_rng(2).integers(0, 255, (600, 4)).astype(np.uint8)
        )
        levels = sc.build_pyramid(matrix)
        assert levels[0] is matrix
        np.testing.assert_array_equal(
            levels[1], np.maximum(matrix[0::2], matrix[1::2])
        )

    def test_odd_tail_keeps_max_information(self):
        matrix = np.zeros((301, 2), np.uint8)
        matrix[300, 0] = 200
        levels = sc.build_pyramid(matrix)
        assert levels[1].shape[0] == 151
        assert levels[1][150, 0] == 200

    def test_pick_level_prefers_coarsest_with_double_columns(self):
        levels = [np.zeros((4096 >> k, 5), np.uint8) for k in range(8)]
        # 窗口 1024 帧、128 列：层 2（窗口 256 组 ≥ 2×128）应为最粗可用层。
        assert sc.pick_level(levels, 0, 1024, 128) == 2
        # 极端放大：窗口内帧数不足以支撑任何粗层，退回第 0 层。
        assert sc.pick_level(levels, 0, 100, 128) == 0

    def test_build_level_matches_pyramid_layers(self):
        matrix = (
            np.random.default_rng(2).integers(0, 255, (600, 4)).astype(np.uint8)
        )
        levels = sc.build_pyramid(matrix)
        for k in range(len(levels)):
            np.testing.assert_array_equal(sc.build_level(matrix, k), levels[k])

    def test_pyramid_depth_matches_build_pyramid_count(self):
        for rows in (100, 256, 257, 600, 4096):
            matrix = np.zeros((rows, 2), np.uint8)
            assert sc.pyramid_depth(rows) == len(sc.build_pyramid(matrix)) - 1

    def test_coarse_pyramid_for_budget_shapes_and_budget(self):
        """超预算粗层金字塔：中间层受目标预算约束，向上逐级减半。"""
        rows, bins = 100_000, 1025  # ≈100MB uint8
        matrix = np.zeros((rows, bins), np.uint8)
        mid, levels = sc.coarse_pyramid_for_budget(matrix, 256 * 1024 * 1024)
        assert mid >= 1
        assert levels[0].shape[0] == (rows + (1 << mid) - 1) >> mid
        assert levels[0].nbytes <= max(16 * 1024 * 1024, 256 * 1024 * 1024 // 8)
        for i in range(1, len(levels)):
            assert levels[i].shape[0] == (levels[i - 1].shape[0] + 1) // 2
        # 第 mid 层与逐层构建一致
        np.testing.assert_array_equal(levels[0], sc.build_level(matrix, mid))

    def test_reduce_columns_and_rows_shapes(self):
        sub = np.random.default_rng(1).integers(0, 255, (500, 513), dtype=np.uint8)
        cols = sc.reduce_columns(sub, 160)
        assert cols.shape == (160, 513)
        edges = sc.frequency_bin_edges(513, SR, 2048, 300, "log")
        view = sc.reduce_rows(cols, edges)
        assert view.shape == (160, 300)
        assert view.dtype == np.uint8


class TestFrequencyEdges:
    def test_log_edges_monotonic_and_clamped(self):
        edges = sc.frequency_bin_edges(513, SR, 2048, 128, "log")
        assert edges.shape == (129,)
        assert np.all(np.diff(edges) >= 0)
        assert edges[0] >= 0.0 and edges[-1] <= 512.0

    def test_linear_edges_span_full_nyquist(self):
        edges = sc.frequency_bin_edges(513, SR, 2048, 64, "linear")
        assert edges[0] == 0.0
        assert edges[-1] == pytest.approx(512.0, abs=1e-6)

    def test_default_zero_clamp_identical_to_no_clamp(self):
        """钳制参数默认 0/0 与无钳制逐位一致（纯渲染期参数，向后兼容）。"""
        for scale in ("log", "linear"):
            base = sc.frequency_bin_edges(513, SR, 2048, 64, scale)
            auto = sc.frequency_bin_edges(513, SR, 2048, 64, scale, 0.0, 0.0)
            assert np.array_equal(base, auto)

    def test_linear_clamp_span(self):
        # 44100Hz 采样 → Nyquist 22050Hz；钳到 [300, 4000]
        edges = sc.frequency_bin_edges(513, SR, 2048, 32, "linear", 300.0, 4000.0)
        bins_per_hz = 2048 / SR
        assert edges[0] == pytest.approx(300 * bins_per_hz, abs=1e-6)
        assert edges[-1] == pytest.approx(4000 * bins_per_hz, abs=1e-6)
        assert np.all(np.diff(edges) > 0)

    def test_log_clamp_intersects_30hz_floor(self):
        # log 下限 30Hz：f_min=100 生效；f_max 与 Nyquist 取小
        edges = sc.frequency_bin_edges(513, SR, 2048, 32, "log", 100.0, 8000.0)
        bins_per_hz = 2048 / SR
        assert edges[0] == pytest.approx(100 * bins_per_hz, rel=1e-6)
        assert edges[-1] == pytest.approx(8000 * bins_per_hz, rel=1e-6)
        # 对数均分：中点应在几何均值附近
        mid_hz = 100 * (8000 / 100) ** 0.5
        mid_pos = np.interp(16, np.arange(33), edges)
        assert mid_pos == pytest.approx(mid_hz * bins_per_hz, rel=1e-3)

    def test_invalid_clamp_falls_back_to_full_range(self):
        """f_min ≥ f_max（钳后为空）回退全范围，不产生空区间。"""
        for scale in ("log", "linear"):
            full = sc.frequency_bin_edges(513, SR, 2048, 32, scale)
            bad = sc.frequency_bin_edges(513, SR, 2048, 32, scale, 5000.0, 2000.0)
            assert np.array_equal(full, bad)

    def test_resolve_freq_range_matches_edges(self):
        """resolve_freq_range 与 frequency_bin_edges 的区间一致（轴刻度共用）。"""
        for scale in ("log", "linear"):
            f_lo, f_hi = sc.resolve_freq_range(SR / 2.0, scale, 200.0, 6000.0)
            edges = sc.frequency_bin_edges(513, SR, 2048, 16, scale, 200.0, 6000.0)
            bins_per_hz = 2048 / SR
            assert edges[0] == pytest.approx(f_lo * bins_per_hz, rel=1e-6)
            assert edges[-1] == pytest.approx(f_hi * bins_per_hz, rel=1e-6)


class TestColormapLut:
    def test_floor_region_is_band_bottom_solid(self):
        """低于地板填色带底部色（实底），不露背景；可见段拉伸不变。"""
        lut = sc.build_colormap_lut(100)
        assert lut.shape == (256, 4)
        # u < floor → 色带底色（= 地板处颜色），整段同色实底
        assert tuple(lut[50][:3]) == (0, 0, 4)
        assert tuple(lut[99][:3]) == tuple(lut[100][:3])
        # 可见段 [floor, 255] 拉伸到完整渐变：地板处渐变底部、顶端浅黄
        assert tuple(lut[100][:3]) == (0, 0, 4)
        assert tuple(lut[255][:3]) == (252, 255, 164)
        assert lut[:, 3].min() == 255

    def test_full_range_floor_zero_keeps_entire_ramp(self):
        lut = sc.build_colormap_lut(0)
        assert tuple(lut[0][:3]) == (0, 0, 4)
        assert tuple(lut[255][:3]) == (252, 255, 164)


class TestDetectBpm:
    def test_click_track_128_bpm(self):
        result = sc.detect_bpm(_click_track(128.0, 30.0), SR)
        assert result["bpm"] is not None
        assert result["bpm"] == pytest.approx(128.0, abs=2.0)
        assert result["confidence"] > 0.1

    def test_click_track_90_bpm(self):
        result = sc.detect_bpm(_click_track(90.0, 30.0), SR)
        assert result["bpm"] is not None
        assert result["bpm"] == pytest.approx(90.0, abs=2.0)

    def test_noisy_click_track_still_detects(self):
        rng = np.random.default_rng(3)
        x = _click_track(150.0, 30.0) + 0.05 * rng.standard_normal(
            int(SR * 30.0)
        ).astype(np.float32)
        result = sc.detect_bpm(x.astype(np.float32), SR)
        assert result["bpm"] is not None
        assert result["bpm"] == pytest.approx(150.0, abs=2.5)

    def test_kick_and_hat_pattern_detects_fast_tempo(self):
        """低频鼓每拍 + 高频击打每半拍（半拍不误导成双倍速）。"""
        result = sc.detect_bpm(_bandpass_track(175.0, 25.0), SR)
        assert result["bpm"] is not None
        assert result["bpm"] == pytest.approx(175.0, abs=2.5)

    def test_too_short_returns_none(self):
        assert sc.detect_bpm(np.zeros(1000, np.float32), SR)["bpm"] is None

    def test_cancelled_returns_none(self):
        assert sc.detect_bpm(_click_track(120.0, 30.0), SR,
                             cancel_check=lambda: True)["bpm"] is None
