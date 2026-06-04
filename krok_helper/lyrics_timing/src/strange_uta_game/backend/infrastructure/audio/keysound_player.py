"""低延迟按键音播放器 — 基于 BASS Sample API。

BASS_SampleLoad 把 WAV 加载为"样本"，最多可同时持有 _MAX_CONCURRENT 个播放通道，
超出时自动复用最旧的通道（BASS_SAMPLE_OVER_POS）。每次 play_* 调用仅需
BASS_SampleGetChannel + BASS_ChannelPlay，无文件 IO，无内存分配，延迟极低。
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from .bass_engine import (
    BASS_ATTRIB_VOL,
    BASS_DEVICE_LATENCY,
    BASS_ERROR_ALREADY,
    BASS_UNICODE,
    _bass,
)

BASS_SAMPLE_OVER_POS: int = 0x400000  # 超出 max 时复用最旧（按播放位置）
_MAX_CONCURRENT: int = 8              # 每个音效最多同时播放数


class KeySoundPlayer:
    """低延迟按键音播放器，支持重叠播放，不互相打断。

    线程安全：BASS 内部线程安全，此类不加额外锁。
    """

    def __init__(self) -> None:
        self._press_sample: int = 0
        self._release_sample: int = 0
        self._enabled: bool = True
        self._volume: float = 1.0  # 0.0 ~ 2.0（对应 0 ~ 200%）

    def load(self, press_path: Path, release_path: Path) -> None:
        """加载按下音和抬起音。已有样本先释放。"""
        self.free()
        # BASS 是进程全局单例；若已由 BassEngine 初始化则 BASS_ERROR_ALREADY 正常
        if not _bass.BASS_Init(-1, 44100, BASS_DEVICE_LATENCY, None, None):
            if _bass.BASS_ErrorGetCode() != BASS_ERROR_ALREADY:
                return  # 初始化失败，静默跳过
        self._press_sample = self._load_sample(press_path)
        self._release_sample = self._load_sample(release_path)

    def _load_sample(self, path: Path) -> int:
        if not path.is_file():
            return 0
        return int(
            _bass.BASS_SampleLoad(
                False,
                ctypes.c_wchar_p(str(path)),
                0,
                0,
                _MAX_CONCURRENT,
                BASS_SAMPLE_OVER_POS | BASS_UNICODE,
            )
        )

    def play_press(self) -> None:
        """播放按下音（立即返回，不阻塞）。"""
        if not self._enabled or not self._press_sample:
            return
        try:
            chan = _bass.BASS_SampleGetChannel(self._press_sample, False)
            if chan:
                _bass.BASS_ChannelSetAttribute(chan, BASS_ATTRIB_VOL, ctypes.c_float(self._volume))
                _bass.BASS_ChannelPlay(chan, False)
        except Exception:
            pass

    def play_release(self) -> None:
        """播放抬起音（立即返回，不阻塞）。"""
        if not self._enabled or not self._release_sample:
            return
        try:
            chan = _bass.BASS_SampleGetChannel(self._release_sample, False)
            if chan:
                _bass.BASS_ChannelSetAttribute(chan, BASS_ATTRIB_VOL, ctypes.c_float(self._volume))
                _bass.BASS_ChannelPlay(chan, False)
        except Exception:
            pass

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def set_volume(self, volume_pct: int) -> None:
        """设置音量，单位为百分比（0~200）。"""
        self._volume = max(0.0, min(2.0, volume_pct / 100.0))

    def invalidate(self) -> None:
        """BASS_Free 后调用：将 handle 归零，不再尝试 BASS_SampleFree。

        BASS_Free 已经回收了所有资源；若之后再用旧 handle 调用 BASS_SampleFree，
        可能误释放新 BASS 会话中复用了同一 handle 值的合法资源。
        """
        self._press_sample = 0
        self._release_sample = 0

    def is_loaded(self) -> bool:
        return bool(self._press_sample and self._release_sample)

    def free(self) -> None:
        """释放样本资源（BASS_Free 之后调用亦安全）。"""
        try:
            if self._press_sample:
                _bass.BASS_SampleFree(self._press_sample)
        except Exception:
            pass
        finally:
            self._press_sample = 0
        try:
            if self._release_sample:
                _bass.BASS_SampleFree(self._release_sample)
        except Exception:
            pass
        finally:
            self._release_sample = 0
