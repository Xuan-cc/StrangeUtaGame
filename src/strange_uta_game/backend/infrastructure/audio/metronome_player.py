"""节拍器音效播放器 — 基于 BASS Sample API（独立实现，与 KeySoundPlayer 同构）。

与按键音播放器刻意不做继承/复用：节拍器与键音各自演化（声道数上限、
音量语义、静音策略等）互不牵连。实现沿用同一套已验证的模式：

- ``BASS_SampleLoad`` 把 WAV 加载为"样本"，最多可同时持有 _MAX_CONCURRENT
  个播放通道，超出时自动复用最旧的通道（BASS_SAMPLE_OVER_POS）。
- 每次播放仅 ``BASS_SampleGetChannel`` + ``BASS_ChannelPlay``，无文件 IO、
  无内存分配，延迟极低——满足节拍器对触发时刻精度的要求。
- BASS 是进程全局单例，``BASS_Free``（切歌/换引擎）后 handle 全部失效，
  需经 ``invalidate()`` 归零、随后重新 ``load()``。
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from . import bass_available

try:
    from .bass_engine import (
        BASS_ATTRIB_VOL,
        BASS_DEVICE_LATENCY,
        BASS_ERROR_ALREADY,
        BASS_UNICODE,
        _bass,
    )
except (ImportError, OSError, AttributeError):
    # mac 等：bass_engine 不可导入（_DummyCDLL 抛 AttributeError）。MetronomePlayer
    # 仅在 bass_available 为 True 时由工厂实例化，占位常量不会被实际使用。
    _bass = None  # type: ignore[assignment]
    BASS_ATTRIB_VOL = 0
    BASS_DEVICE_LATENCY = 0
    BASS_ERROR_ALREADY = -1
    BASS_UNICODE = 0

BASS_SAMPLE_OVER_POS: int = 0x400000  # 超出 max 时复用最旧（按播放位置）
_MAX_CONCURRENT: int = 8              # 每个音效最多同时播放数


def metronome_sound_paths() -> tuple:
    """节拍音资源路径 (普通拍, 重音)：resource/sounds/metronome_*.wav。"""
    # metronome_player.py → audio → infrastructure → backend → strange_uta_game
    sounds_dir = (
        Path(__file__).resolve().parent.parent.parent.parent / "resource" / "sounds"
    )
    return sounds_dir / "metronome_beat.wav", sounds_dir / "metronome_accent.wav"


class MetronomePlayer:
    """低延迟节拍音播放器（普通拍 / 每 4 拍重音），支持重叠播放。

    线程安全：BASS 内部线程安全，此类不加额外锁（调度线程直接调用）。
    """

    def __init__(self) -> None:
        self._beat_sample: int = 0
        self._accent_sample: int = 0
        self._volume: float = 1.0  # 0.0 ~ 2.0（对应 0 ~ 200%）

    def load(self, beat_path: Path, accent_path: Path) -> None:
        """加载普通拍和重音样本。已有样本先释放。"""
        self.free()
        # BASS 是进程全局单例；若已由 BassEngine 初始化则 BASS_ERROR_ALREADY 正常
        if not _bass.BASS_Init(-1, 44100, BASS_DEVICE_LATENCY, None, None):
            if _bass.BASS_ErrorGetCode() != BASS_ERROR_ALREADY:
                return  # 初始化失败，静默跳过
        self._beat_sample = self._load_sample(beat_path)
        self._accent_sample = self._load_sample(accent_path)

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

    def _play_sample(self, sample: int) -> None:
        if not sample:
            return
        try:
            chan = _bass.BASS_SampleGetChannel(sample, False)
            if chan:
                _bass.BASS_ChannelSetAttribute(
                    chan, BASS_ATTRIB_VOL, ctypes.c_float(self._volume)
                )
                _bass.BASS_ChannelPlay(chan, False)
        except Exception:
            pass

    def play_beat(self) -> None:
        """播放普通拍（立即返回，不阻塞）。"""
        self._play_sample(self._beat_sample)

    def play_accent(self) -> None:
        """播放重音（每 4 拍的小节首拍）。"""
        self._play_sample(self._accent_sample)

    def set_volume(self, volume_pct: int) -> None:
        """设置音量，单位为百分比（0~200）。"""
        self._volume = max(0.0, min(2.0, volume_pct / 100.0))

    def get_volume(self) -> int:
        return int(round(self._volume * 100))

    def invalidate(self) -> None:
        """BASS_Free 后调用：将 handle 归零，不再尝试 BASS_SampleFree。

        BASS_Free 已经回收了所有资源；若之后再用旧 handle 调用 BASS_SampleFree，
        可能误释放新 BASS 会话中复用了同一 handle 值的合法资源。
        """
        self._beat_sample = 0
        self._accent_sample = 0

    def is_loaded(self) -> bool:
        return bool(self._beat_sample and self._accent_sample)

    def free(self) -> None:
        """释放样本资源（BASS_Free 之后调用亦安全）。"""
        try:
            if self._beat_sample:
                _bass.BASS_SampleFree(self._beat_sample)
        except Exception:
            pass
        finally:
            self._beat_sample = 0
        try:
            if self._accent_sample:
                _bass.BASS_SampleFree(self._accent_sample)
        except Exception:
            pass
        finally:
            self._accent_sample = 0


# ── mac（BASS 不可用）回退实现 ──────────────────────────────────────────────

import sounddevice as _sd
import soundfile as _sf
import numpy as _np


class SoundDeviceMetronomePlayer:
    """基于 sounddevice 的节拍音播放器（mac 等无 BASS 平台使用）。

    与 :class:`MetronomePlayer` 同接口：``load`` 预读 WAV 为 numpy 数组，
    ``play_*`` 调 ``sounddevice.play``。节拍音对延迟容忍度高，per-call
    播放足够，不复刻主引擎的 ring buffer。
    """

    def __init__(self) -> None:
        self._beat: tuple[_np.ndarray, int] | None = None  # (data, sample_rate)
        self._accent: tuple[_np.ndarray, int] | None = None
        self._volume: float = 1.0  # 0.0 ~ 2.0

    def load(self, beat_path: Path, accent_path: Path) -> None:
        """加载普通拍和重音；失败静默跳过。"""
        self._beat = self._read(beat_path)
        self._accent = self._read(accent_path)

    @staticmethod
    def _read(path: Path) -> tuple[_np.ndarray, int] | None:
        if not path.is_file():
            return None
        try:
            data, sr = _sf.read(str(path), dtype="float32")
            return data, sr
        except Exception:
            return None

    def _play(self, sample: tuple[_np.ndarray, int] | None) -> None:
        if sample is None:
            return
        data, sr = sample
        try:
            _sd.play(data * self._volume, sr)
        except Exception:
            pass  # 设备忙/不可用时不打断主流程

    def play_beat(self) -> None:
        self._play(self._beat)

    def play_accent(self) -> None:
        self._play(self._accent)

    def set_volume(self, volume_pct: int) -> None:
        self._volume = max(0.0, min(2.0, volume_pct / 100.0))

    def get_volume(self) -> int:
        return int(round(self._volume * 100))

    def invalidate(self) -> None:
        """对齐 MetronomePlayer 接口；sounddevice 无外部 handle 需失效。"""
        pass

    def is_loaded(self) -> bool:
        return self._beat is not None and self._accent is not None

    def free(self) -> None:
        self._beat = None
        self._accent = None


def create_metronome_player():
    """按 BASS 可用性选择节拍音实现。"""
    if bass_available:
        return MetronomePlayer()
    return SoundDeviceMetronomePlayer()
