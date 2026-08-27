"""SoundDevice 引擎 mono 与显示 PCM 同采样空间测试（P1-1 回归）。

背景：缓存 PCM 可能被重采样（22050→32000）且含 MP3 编解码延迟/补齐
（~26ms）。mono 若从原始解码 PCM 预混，会被 AudioInfo.sample_rate 错误
解释——波形/声谱/BPM 与时间轴整体错位，重采样场景下后段约 31% 的
时间轴没有分析数据。修复后 mono 从缓存 PCM（get_original_samples 的
同一份数组）预混。
"""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

try:
    from strange_uta_game.backend.infrastructure.audio.sounddevice_engine import (
        SoundDeviceEngine,
    )

    _ENGINE_IMPORTABLE = True
except Exception:  # PortAudio/sounddevice 在个别环境不可加载
    _ENGINE_IMPORTABLE = False

pytestmark = pytest.mark.skipif(
    not _ENGINE_IMPORTABLE, reason="sounddevice/PortAudio 不可用"
)


def _make_engine() -> SoundDeviceEngine:
    engine = SoundDeviceEngine()
    # 测试不碰音频设备：load() 尾部会建立常驻输出流
    engine._start_streaming = lambda: None
    return engine


def _pulse_stereo(sr: int, seconds: float = 2.0, pulse_at: float = 1.0):
    """左声道在 pulse_at 秒处放一个短脉冲，右声道静音。"""
    n = int(sr * seconds)
    x = np.zeros(n, dtype=np.float32)
    i = int(sr * pulse_at)
    x[i : i + 64] = 0.8
    return np.stack([x, np.zeros_like(x)], axis=1)


class TestMonoSampleSpace:
    def test_resampled_input_mono_matches_display_pcm(self, tmp_path):
        """22050Hz 输入被缓存重采样到 MP3 档位：mono 与显示 PCM 同长同采样率。"""
        p = tmp_path / "resampled.wav"
        sf.write(str(p), _pulse_stereo(22050), 22050, subtype="FLOAT")
        engine = _make_engine()
        engine.load(str(p))
        try:
            display = engine.get_original_samples()
            mono = engine.get_mono_samples()
            info = engine.get_audio_info()
            assert display is not None and mono is not None
            assert info.sample_rate >= 32000  # 22050 被升采样到 MP3 档位
            assert len(mono) == display.shape[0]  # 同一 PCM 时间空间
            assert mono.ndim == 1 and mono.dtype == np.float32
        finally:
            engine.release()

    def test_native_rate_mono_matches_display_pcm(self, tmp_path):
        """44100Hz 原生档经 MP3 编解码延迟/补齐：长度有变化但两者必须一致。"""
        p = tmp_path / "native.wav"
        sf.write(str(p), _pulse_stereo(44100), 44100, subtype="FLOAT")
        engine = _make_engine()
        engine.load(str(p))
        try:
            display = engine.get_original_samples()
            mono = engine.get_mono_samples()
            info = engine.get_audio_info()
            assert display.shape[0] != int(44100 * 2)  # MP3 补齐改变长度
            assert len(mono) == display.shape[0]
            assert info.sample_rate == 44100
        finally:
            engine.release()

    def test_pulse_position_preserved(self, tmp_path):
        """1.0s 脉冲在 mono 中的峰值毫秒位置不漂移（容差覆盖 MP3 ~26ms 延迟）。"""
        p = tmp_path / "pulse.wav"
        sf.write(str(p), _pulse_stereo(44100), 44100, subtype="FLOAT")
        engine = _make_engine()
        engine.load(str(p))
        try:
            mono = engine.get_mono_samples()
            info = engine.get_audio_info()
            peak_ms = int(np.argmax(np.abs(mono))) / info.sample_rate * 1000
            assert abs(peak_ms - 1000.0) < 60
        finally:
            engine.release()
