"""音频引擎测试。"""

import pytest
import numpy as np
import soundfile as sf
from pathlib import Path

from strange_uta_game.backend.infrastructure.audio import (
    SoundDeviceEngine,
    AudioLoadError,
    PlaybackState,
)


@pytest.fixture
def test_audio_file(tmp_path):
    """创建测试音频文件"""
    # 生成 1 秒的测试音频（44100 Hz，单声道）
    sample_rate = 44100
    duration = 1.0  # 1 秒
    t = np.linspace(0, duration, int(sample_rate * duration))
    # 生成 440Hz 正弦波
    data = np.sin(2 * np.pi * 440 * t) * 0.3

    file_path = tmp_path / "test_audio.wav"
    sf.write(file_path, data, sample_rate)

    return str(file_path)


class TestSoundDeviceEngine:
    """测试 SoundDevice 音频引擎"""

    def test_load_audio(self, test_audio_file):
        """测试加载音频文件"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        info = engine.get_audio_info()
        assert info is not None
        assert info.file_path == test_audio_file
        assert info.duration_ms == 1000  # 1 秒 = 1000ms
        assert info.sample_rate == 44100

    def test_load_nonexistent_file(self):
        """测试加载不存在的文件应该报错"""
        engine = SoundDeviceEngine()

        with pytest.raises(AudioLoadError) as exc_info:
            engine.load("/nonexistent/file.wav")

        # 错误消息应该包含有关加载失败的信息
        assert "加载音频失败" in str(exc_info.value) or "Error opening" in str(
            exc_info.value
        )

    def test_get_duration(self, test_audio_file):
        """测试获取音频时长"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        duration = engine.get_duration_ms()

        assert duration == 1000

    def test_set_and_get_position(self, test_audio_file):
        """测试设置和获取位置"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        # 设置位置
        engine.set_position_ms(500)

        # 获取位置
        position = engine.get_position_ms()

        assert position == 500

    def test_set_position_out_of_range(self, test_audio_file):
        """测试设置超出范围的位置"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        # 设置超出范围的位置应该被限制
        engine.set_position_ms(2000)  # 音频只有 1000ms

        position = engine.get_position_ms()
        assert position == 1000  # 被限制在最大值

        # 负数应该被限制为 0
        engine.set_position_ms(-100)
        position = engine.get_position_ms()
        assert position == 0

    def test_playback_state(self, test_audio_file):
        """测试播放状态"""
        engine = SoundDeviceEngine()

        # 初始状态
        assert engine.get_playback_state() == PlaybackState.STOPPED
        assert not engine.is_playing()

        engine.load(test_audio_file)

        # 加载后还是停止状态
        assert engine.get_playback_state() == PlaybackState.STOPPED

    def test_set_and_get_speed(self, test_audio_file):
        """测试设置和获取播放速度"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        # 默认速度为 1.0
        assert engine.get_speed() == 1.0

        # 设置速度
        engine.set_speed(1.5)
        assert engine.get_speed() == 1.5

        engine.set_speed(0.5)
        assert engine.get_speed() == 0.5

    def test_set_speed_out_of_range(self, test_audio_file):
        """测试设置超出范围的速度"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        with pytest.raises(ValueError):
            engine.set_speed(3.0)

        with pytest.raises(ValueError):
            engine.set_speed(0.1)

    def test_set_and_get_volume(self, test_audio_file):
        """测试设置和获取音量"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        # 默认音量为 1.0
        assert engine.get_volume() == 1.0

        # 设置音量
        engine.set_volume(0.5)
        assert engine.get_volume() == 0.5

        engine.set_volume(0.0)
        assert engine.get_volume() == 0.0

    def test_set_volume_out_of_range(self, test_audio_file):
        """测试设置超出范围的音量"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        # 超出范围应该被限制
        engine.set_volume(2.0)
        assert engine.get_volume() == 1.0

        engine.set_volume(-1.0)
        assert engine.get_volume() == 0.0

    def test_position_callback(self, test_audio_file):
        """测试位置回调"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        positions = []

        def callback(position):
            positions.append(position)

        engine.set_position_callback(callback)

        # 注意：由于需要实际播放音频才能触发回调，
        # 这里只测试回调函数的设置
        assert engine._position_callback is callback

    def test_release(self, test_audio_file):
        """测试释放资源"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        # 释放资源
        engine.release()

        # 释放后信息应该被清除
        assert engine.get_audio_info() is None

    def test_stop_resets_position(self, test_audio_file):
        """测试停止后位置重置"""
        engine = SoundDeviceEngine()
        engine.load(test_audio_file)

        # 设置位置
        engine.set_position_ms(500)

        # 停止
        engine.stop()

        # 位置应该重置为 0
        position = engine.get_position_ms()
        assert position == 0
