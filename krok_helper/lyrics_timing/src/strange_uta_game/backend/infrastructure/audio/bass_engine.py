"""BASS audio engine.

The public timing position intentionally stays free of UI-only smoothing.  The
editor can ask for a monotonic display position separately, while timing keys
always read the raw audio-clock position.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from .base import (
    AudioInfo,
    AudioLoadError,
    AudioPlaybackError,
    IAudioEngine,
    PlaybackState,
)

# ═══════════════════════════════════════════════════════════════════
# Load BASS DLLs
# ═══════════════════════════════════════════════════════════════════

_BASS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "bass"
_IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8
_BASS_DIR = _BASS_ROOT / "x64" if _IS_64BIT and (_BASS_ROOT / "x64").exists() else _BASS_ROOT

if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(str(_BASS_DIR))

_bass = ctypes.CDLL(str(_BASS_DIR / "bass.dll"))
_bass_fx = ctypes.CDLL(str(_BASS_DIR / "bass_fx.dll"))

# ═══════════════════════════════════════════════════════════════════
# BASS constants (from bass.h / bass_fx.h)
# ═══════════════════════════════════════════════════════════════════

BASS_POS_BYTE = 0
BASS_ACTIVE_STOPPED = 0
BASS_ACTIVE_PLAYING = 1
BASS_ACTIVE_STALLED = 2
BASS_ACTIVE_PAUSED = 3
BASS_ACTIVE_PAUSED_DEVICE = 4
BASS_ATTRIB_VOL = 2
BASS_ATTRIB_TEMPO = 0x10000
# BASS_FX tempo (SoundTouch) tuning options
BASS_ATTRIB_TEMPO_OPTION_USE_AA_FILTER = 0x10010
BASS_ATTRIB_TEMPO_OPTION_SEQUENCE_MS = 0x10013
BASS_ATTRIB_TEMPO_OPTION_SEEKWINDOW_MS = 0x10014
BASS_ATTRIB_TEMPO_OPTION_OVERLAP_MS = 0x10015
BASS_ATTRIB_TEMPO_OPTION_PREVENT_CLICK = 0x10016
BASS_SAMPLE_FLOAT = 256
BASS_DATA_FLOAT = 0x40000000
BASS_STREAM_DECODE = 0x200000
BASS_STREAM_PRESCAN = 0x20000
BASS_FX_FREESOURCE = 0x10000
BASS_UNICODE = 0x80000000
BASS_DEVICE_LATENCY = 0x100
BASS_ERROR_ALREADY = 14

# ═══════════════════════════════════════════════════════════════════
# ctypes signatures — BASS core
# ═══════════════════════════════════════════════════════════════════

_bass.BASS_Init.restype = ctypes.c_int
_bass.BASS_Init.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]

_bass.BASS_Free.restype = ctypes.c_int
_bass.BASS_Free.argtypes = []

_bass.BASS_Start.restype = ctypes.c_int
_bass.BASS_Start.argtypes = []

_bass.BASS_StreamCreateFile.restype = ctypes.c_uint
_bass.BASS_StreamCreateFile.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint]

_bass.BASS_ChannelPlay.restype = ctypes.c_int
_bass.BASS_ChannelPlay.argtypes = [ctypes.c_uint, ctypes.c_int]

_bass.BASS_ChannelPause.restype = ctypes.c_int
_bass.BASS_ChannelPause.argtypes = [ctypes.c_uint]

_bass.BASS_ChannelStop.restype = ctypes.c_int
_bass.BASS_ChannelStop.argtypes = [ctypes.c_uint]

_bass.BASS_ChannelGetPosition.restype = ctypes.c_uint64
_bass.BASS_ChannelGetPosition.argtypes = [ctypes.c_uint, ctypes.c_uint]

_bass.BASS_ChannelSetPosition.restype = ctypes.c_int
_bass.BASS_ChannelSetPosition.argtypes = [ctypes.c_uint, ctypes.c_uint64, ctypes.c_uint]

_bass.BASS_ChannelGetLength.restype = ctypes.c_uint64
_bass.BASS_ChannelGetLength.argtypes = [ctypes.c_uint, ctypes.c_uint]

_bass.BASS_ChannelIsActive.restype = ctypes.c_uint
_bass.BASS_ChannelIsActive.argtypes = [ctypes.c_uint]

_bass.BASS_ChannelSetAttribute.restype = ctypes.c_int
_bass.BASS_ChannelSetAttribute.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_float]

_bass.BASS_ChannelBytes2Seconds.restype = ctypes.c_double
_bass.BASS_ChannelBytes2Seconds.argtypes = [ctypes.c_uint, ctypes.c_uint64]

_bass.BASS_ChannelSeconds2Bytes.restype = ctypes.c_uint64
_bass.BASS_ChannelSeconds2Bytes.argtypes = [ctypes.c_uint, ctypes.c_double]

_bass.BASS_ChannelGetData.restype = ctypes.c_int
_bass.BASS_ChannelGetData.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]

_bass.BASS_StreamFree.restype = ctypes.c_int
_bass.BASS_StreamFree.argtypes = [ctypes.c_uint]

_bass.BASS_ErrorGetCode.restype = ctypes.c_int
_bass.BASS_ErrorGetCode.argtypes = []

_bass.BASS_PluginLoad.restype = ctypes.c_uint
_bass.BASS_PluginLoad.argtypes = [ctypes.c_void_p, ctypes.c_uint]

# BASS Sample API（用于低延迟按键音）
_bass.BASS_SampleLoad.restype = ctypes.c_uint
_bass.BASS_SampleLoad.argtypes = [
    ctypes.c_int,       # mem
    ctypes.c_void_p,    # file (wchar_t* 时需搭配 BASS_UNICODE flag)
    ctypes.c_uint64,    # offset
    ctypes.c_uint,      # length
    ctypes.c_uint,      # max concurrent channels
    ctypes.c_uint,      # flags
]

_bass.BASS_SampleGetChannel.restype = ctypes.c_uint
_bass.BASS_SampleGetChannel.argtypes = [ctypes.c_uint, ctypes.c_int]

_bass.BASS_SampleFree.restype = ctypes.c_int
_bass.BASS_SampleFree.argtypes = [ctypes.c_uint]

# BASS_INFO struct — for reading output buffer latency
class BASS_INFO(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint),
        ("hwsize", ctypes.c_uint),
        ("hwfree", ctypes.c_uint),
        ("freesam", ctypes.c_uint),
        ("free3d", ctypes.c_uint),
        ("minrate", ctypes.c_uint),
        ("maxrate", ctypes.c_uint),
        ("eax", ctypes.c_int),
        ("minbuf", ctypes.c_uint),      # minimum buffer length (ms)
        ("dsver", ctypes.c_uint),
        ("latency", ctypes.c_uint),     # average output delay (bytes)
        ("initflags", ctypes.c_uint),
        ("speakers", ctypes.c_uint),
        ("freq", ctypes.c_uint),
    ]

_bass.BASS_GetInfo.restype = ctypes.c_int
_bass.BASS_GetInfo.argtypes = [ctypes.POINTER(BASS_INFO)]


class BASS_CHANNELINFO(ctypes.Structure):
    _fields_ = [
        ("freq", ctypes.c_uint),
        ("chans", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("ctype", ctypes.c_uint),
        ("origres", ctypes.c_uint),
        ("plugin", ctypes.c_uint),
        ("sample", ctypes.c_uint),
        ("filename", ctypes.c_char_p),
    ]


_bass.BASS_ChannelGetInfo.restype = ctypes.c_int
_bass.BASS_ChannelGetInfo.argtypes = [ctypes.c_uint, ctypes.POINTER(BASS_CHANNELINFO)]

_BASS_PLUGIN_HANDLES: dict[str, int] = {}


def _load_bass_plugin(name: str) -> int:
    """Load an optional BASS add-on from the active DLL dir or bundled root."""
    if name in _BASS_PLUGIN_HANDLES:
        return _BASS_PLUGIN_HANDLES[name]

    candidates = [_BASS_DIR / name]
    if _BASS_ROOT != _BASS_DIR:
        candidates.append(_BASS_ROOT / name)

    for path in candidates:
        if not path.exists():
            continue
        handle = _bass.BASS_PluginLoad(ctypes.c_wchar_p(str(path)), BASS_UNICODE)
        if handle:
            _BASS_PLUGIN_HANDLES[name] = handle
            # print(f"[BassEngine] BASS plugin loaded: {path}")
            return handle
        print(f"[BassEngine] BASS plugin load failed: {path} (error {_bass.BASS_ErrorGetCode()})")

    _BASS_PLUGIN_HANDLES[name] = 0
    return 0


def _load_all_bass_plugins() -> None:
    """加载 _BASS_DIR 下所有 BASS 解码插件，扩展可直接播放的音频格式。

    覆盖 bass_aac/bassalac/bassape/bass_ac3/bassdsd/bassflac/bassopus/
    basswma/basswv 等 → 支持 AAC/M4A/ALAC/APE/AC3/DSD/FLAC/Opus/WMA/WavPack。
    插件全局注册，对所有流（含 BASS_FX tempo 流）生效；缺失/失败的静默跳过，
    不影响核心 mp3/wav/ogg。
    """
    # 仅枚举当前架构目录（x64）；root 下多为 32 位 DLL，在 64 位进程加载会失败。
    # 跳过核心库与非解码 addon（basswasapi 是输出插件，不能当解码插件加载）。
    skip = {"bass.dll", "bass_fx.dll", "basswasapi.dll"}
    try:
        names = sorted(p.name for p in _BASS_DIR.glob("bass*.dll"))
    except Exception:
        names = []
    for name in names:
        if name.lower() in skip:
            continue
        _load_bass_plugin(name)


_load_all_bass_plugins()

# ═══════════════════════════════════════════════════════════════════
# ctypes signatures — BASS_FX
# ═══════════════════════════════════════════════════════════════════

_bass_fx.BASS_FX_TempoCreate.restype = ctypes.c_uint
_bass_fx.BASS_FX_TempoCreate.argtypes = [ctypes.c_uint, ctypes.c_uint]


class BassEngine(IAudioEngine):
    """BASS 音频引擎。"""

    def __init__(self) -> None:
        self._state = PlaybackState.STOPPED
        self._file_path: Optional[str] = None
        self._playback_path: Optional[str] = None
        # Path of a cache file we generated (video extraction / soundfile
        # fallback). Tracked so the next load()/release() can delete it — the
        # old TSM engine used to sweep .cache on every load; BASS doesn't, so
        # we clean up our own products to avoid unbounded growth.
        self._generated_playback_path: Optional[str] = None
        self._duration_ms: int = 0
        self._speed: float = 1.0
        self._volume: float = 1.0
        self._tempo_stream: int = 0
        self._decode_stream: int = 0
        self._position_callback: Optional[Callable[[int], None]] = None
        self._render_progress_cb: Optional[Callable[[float, float], None]] = None

        # waveform data (read via soundfile, for get_original_samples)
        self._original_data: Optional[np.ndarray] = None
        self._original_sample_rate: int = 44100
        self._channels: int = 2

        # Cached BASS output latency (ms).
        self._output_latency_ms: int = 0
        self._initialized = False
        self._recovering = False
        self._last_recovery_attempt = 0.0

        # Serializes stream lifecycle (load/release/stop/play/seek/recovery) so
        # the background recovery thread never races the UI thread. Re-entrant
        # because recovery helpers call into one another.
        self._stream_lock = threading.RLock()
        self._recovery_thread: Optional[threading.Thread] = None

        # UI-only monotonic guard, never used by timing keys.
        self._last_reported_ms: int = 0

        self._ensure_initialized()

    def _cache_output_latency(self) -> None:
        """Query BASS_INFO and cache output latency in milliseconds."""
        info = BASS_INFO()
        if _bass.BASS_GetInfo(ctypes.byref(info)):
            # BASS_INFO.latency is already expressed in milliseconds when
            # BASS_DEVICE_LATENCY was used at init time.
            self._output_latency_ms = max(0, int(info.latency))

    def _ensure_initialized(self) -> bool:
        """Initialize BASS, retrying on load/recovery if construction failed."""
        if self._initialized:
            info = BASS_INFO()
            if _bass.BASS_GetInfo(ctypes.byref(info)):
                self._output_latency_ms = max(0, int(info.latency))
                return True
            # BASS is process-global. Another engine instance may have called
            # BASS_Free(), so our per-instance flag can become stale.
            self._initialized = False

        if _bass.BASS_Init(-1, 44100, BASS_DEVICE_LATENCY, None, None):
            self._initialized = True
            self._cache_output_latency()
            return True

        err = _bass.BASS_ErrorGetCode()
        if err == BASS_ERROR_ALREADY:
            self._initialized = True
            self._cache_output_latency()
            return True

        print(f"[BassEngine] BASS_Init failed (error {err}), will retry later")
        self._initialized = False
        return False

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — load / release
    # ═══════════════════════════════════════════════════════════════

    def load(self, file_path: str, progress_cb=None) -> None:
        with self._stream_lock:
            self.stop()
            self._free_streams()
            # Sweep the previous run's generated cache file before loading a new
            # one (streams are now freed, so it is safe to delete).
            self._cleanup_generated_playback()

            if not Path(file_path).is_file():
                raise AudioLoadError(f"加载音频失败: 文件不存在: {file_path}")

            if not self._ensure_initialized():
                err = _bass.BASS_ErrorGetCode()
                raise AudioLoadError(f"BASS 初始化失败 (error {err})")

            if progress_cb:
                progress_cb("读取音频...", 0.0)
            if progress_cb:
                progress_cb("创建 BASS 流...", 0.3)

            # Common path: open the file directly (single PRESCAN). Only fall
            # back to conversion when BASS itself cannot decode the container.
            try:
                self._create_streams(file_path)
                playback_path = file_path
            except AudioLoadError:
                # _convert_for_bass records its soundfile-fallback wav in
                # _generated_playback_path for later sweeping. Video extractions
                # are NOT tracked here — they become the project's persistent
                # audio (file_loader stores that path), so deleting them would
                # break saved video projects.
                playback_path = self._convert_for_bass(file_path, progress_cb)
                self._create_streams(playback_path)

            byte_len = _bass.BASS_ChannelGetLength(self._tempo_stream, BASS_POS_BYTE)
            self._duration_ms = int(
                _bass.BASS_ChannelBytes2Seconds(self._tempo_stream, byte_len) * 1000
            )

            self._load_waveform_data(file_path, playback_path)

            self._file_path = file_path
            self._playback_path = playback_path
            self._state = PlaybackState.STOPPED
            self._last_reported_ms = 0
            self._speed = 1.0
            self._apply_speed()
            self._apply_volume()

            # Re-cache latency with actual file params
            self._cache_output_latency()

            if progress_cb:
                progress_cb("就绪", 1.0)

    def _convert_for_bass(self, file_path: str, progress_cb=None) -> str:
        """Convert a file BASS cannot open into a path it can.

        Only reached when ``_create_streams(file_path)`` failed, so the common
        case never pays for this. Handles video containers (ffmpeg) and formats
        soundfile can decode but BASS cannot (e.g. FLAC without bassflac.dll).
        """
        try:
            from .video_converter import VIDEO_EXTENSIONS, extract_audio

            if Path(file_path).suffix.lower() in VIDEO_EXTENSIONS:
                return extract_audio(file_path, progress_cb=progress_cb)
        except Exception as exc:
            raise AudioLoadError(str(exc)) from exc

        try:
            from .video_converter import clear_extracted_cache

            data, sr = sf.read(str(file_path), dtype="float32")
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            cache_dir = self._fallback_cache_dir()
            clear_extracted_cache()
            wav_path = cache_dir / f"{Path(file_path).stem}_bass_fallback.wav"
            sf.write(str(wav_path), data, sr)
            # Track only our own fallback product for later cleanup.
            self._generated_playback_path = str(wav_path)
            return str(wav_path)
        except Exception as exc:
            raise AudioLoadError(f"BASS 无法打开文件，且转换失败: {exc}") from exc

    @staticmethod
    def _fallback_cache_dir() -> Path:
        """Cache dir for converted audio.

        Reuses video_converter's cache location so there is a single place to
        change it; falls back to the OS temp dir when that path is not writable
        (e.g. installed under Program Files).
        """
        try:
            from .video_converter import _get_cache_dir

            return _get_cache_dir()
        except Exception:
            import tempfile

            cache_dir = Path(tempfile.gettempdir()) / "strange_uta_game_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir

    def _rebuild_streams(self) -> bool:
        """Synchronously (re)create streams from ``_playback_path``.

        Used by play() self-heal when streams were torn down by a failed
        recovery. Returns True on success. Caller must hold ``_stream_lock``.
        """
        if not self._playback_path:
            return False
        if not self._ensure_initialized():
            return False
        try:
            self._free_streams()
            self._create_streams(self._playback_path)
        except AudioLoadError:
            return False
        self._apply_speed()
        self._apply_volume()
        return True

    def _create_streams(self, playback_path: str) -> None:
        # BASS_SAMPLE_FLOAT keeps the whole decode→tempo→output chain in 32-bit
        # float. Without it the chain runs in 16-bit and SoundTouch's overlap-add
        # can overshoot full scale, wrapping/clipping into audible crackle ("爆音"),
        # especially at speeds != 1.0. Float gives SoundTouch headroom.
        self._decode_stream = _bass.BASS_StreamCreateFile(
            0,
            ctypes.c_wchar_p(playback_path),
            0,
            0,
            BASS_STREAM_DECODE | BASS_STREAM_PRESCAN | BASS_SAMPLE_FLOAT | BASS_UNICODE,
        )
        if not self._decode_stream:
            err = _bass.BASS_ErrorGetCode()
            raise AudioLoadError(f"BASS 无法打开文件 (error {err}): {playback_path}")

        self._tempo_stream = _bass_fx.BASS_FX_TempoCreate(
            self._decode_stream, BASS_FX_FREESOURCE
        )
        if not self._tempo_stream:
            err = _bass.BASS_ErrorGetCode()
            _bass.BASS_StreamFree(self._decode_stream)
            self._decode_stream = 0
            raise AudioLoadError(f"BASS_FX_TempoCreate 失败 (error {err})")
        self._decode_stream = 0
        self._apply_tempo_options()

    def _apply_tempo_options(self) -> None:
        """Configure SoundTouch to minimise tempo artifacts.

        PREVENT_CLICK removes the click when speed crosses 1.0; the AA filter
        plus slightly larger sequence/overlap windows smooth output at the
        extreme 0.5x/2.0x ends. Applied once per stream creation.
        """
        if not self._tempo_stream:
            return
        for attrib, value in (
            (BASS_ATTRIB_TEMPO_OPTION_PREVENT_CLICK, 1.0),
            (BASS_ATTRIB_TEMPO_OPTION_USE_AA_FILTER, 1.0),
        ):
            _bass.BASS_ChannelSetAttribute(
                self._tempo_stream, attrib, ctypes.c_float(value)
            )

    def _load_waveform_data(self, file_path: str, playback_path: str) -> None:
        """Best-effort waveform decode; playback must not depend on it."""
        if self._load_waveform_data_from_bass(playback_path):
            return

        for candidate in (file_path, playback_path):
            try:
                data, sr = sf.read(str(candidate), dtype="float32")
                if data.ndim == 1:
                    data = data.reshape(-1, 1)
                self._original_data = np.ascontiguousarray(data, dtype=np.float32)
                self._original_sample_rate = int(sr)
                self._channels = self._original_data.shape[1]
                return
            except Exception:
                continue

        self._original_data = None
        self._original_sample_rate = 44100
        self._channels = 2

    def _load_waveform_data_from_bass(self, playback_path: str) -> bool:
        decode_stream = _bass.BASS_StreamCreateFile(
            0,
            ctypes.c_wchar_p(playback_path),
            0,
            0,
            BASS_STREAM_DECODE
            | BASS_STREAM_PRESCAN
            | BASS_SAMPLE_FLOAT
            | BASS_UNICODE,
        )
        if not decode_stream:
            return False

        try:
            info = BASS_CHANNELINFO()
            if not _bass.BASS_ChannelGetInfo(decode_stream, ctypes.byref(info)):
                return False
            byte_len = _bass.BASS_ChannelGetLength(decode_stream, BASS_POS_BYTE)
            if byte_len == 0 or byte_len > (1 << 62):
                return False

            total_floats = int(byte_len // 4)
            if total_floats <= 0:
                return False

            raw = np.empty(total_floats, dtype=np.float32)
            offset = 0
            chunk_floats = 262144
            while offset < total_floats:
                take = min(chunk_floats, total_floats - offset)
                ptr = ctypes.c_void_p(raw.ctypes.data + offset * 4)
                got = _bass.BASS_ChannelGetData(
                    decode_stream, ptr, (take * 4) | BASS_DATA_FLOAT
                )
                if got <= 0:
                    break
                offset += got // 4

            if offset <= 0:
                return False
            raw = raw[:offset]
            channels = max(1, int(info.chans))
            frame_count = len(raw) // channels
            if frame_count <= 0:
                return False
            self._original_data = np.ascontiguousarray(
                raw[: frame_count * channels].reshape(frame_count, channels),
                dtype=np.float32,
            )
            self._original_sample_rate = int(info.freq) or 44100
            self._channels = channels
            return True
        finally:
            _bass.BASS_StreamFree(decode_stream)

    def _free_streams(self) -> None:
        if self._tempo_stream:
            _bass.BASS_StreamFree(self._tempo_stream)
            self._tempo_stream = 0
        if self._decode_stream:
            _bass.BASS_StreamFree(self._decode_stream)
            self._decode_stream = 0

    def _cleanup_generated_playback(self) -> None:
        """Delete the cache file we generated for the previous load, if any.

        Only touches files we created under the cache dir; never deletes the
        user's original media. Streams must already be freed.
        """
        path = self._generated_playback_path
        self._generated_playback_path = None
        if not path:
            return
        try:
            p = Path(path)
            # Defensive: only remove our own fallback wavs inside the cache dir.
            if (
                p.is_file()
                and p.name.endswith("_bass_fallback.wav")
                and p.parent == self._fallback_cache_dir()
            ):
                p.unlink()
        except Exception:
            pass

    def release(self) -> None:
        with self._stream_lock:
            self.stop()
            self._free_streams()
            self._cleanup_generated_playback()
            if self._initialized:
                _bass.BASS_Free()
                self._initialized = False
            self._original_data = None
            self._file_path = None
            self._playback_path = None
            self._duration_ms = 0
            self._position_callback = None

    def play(self) -> None:
        with self._stream_lock:
            # Self-heal: a previously failed recovery may have torn the streams
            # down (_tempo_stream == 0) while _playback_path is still valid.
            # Rebuild from it instead of forcing the user to reload the file.
            if self._tempo_stream == 0:
                if not self._rebuild_streams():
                    raise AudioPlaybackError("没有加载音频文件")
            if self._state == PlaybackState.PLAYING:
                return
            if not _bass.BASS_ChannelPlay(self._tempo_stream, 0):
                # Play failed — attempt a synchronous full device recovery once.
                if not self._recover_device_sync("play failed"):
                    err = _bass.BASS_ErrorGetCode()
                    raise AudioPlaybackError(f"BASS 播放失败 (error {err})")
                if not _bass.BASS_ChannelPlay(self._tempo_stream, 0):
                    err = _bass.BASS_ErrorGetCode()
                    raise AudioPlaybackError(f"BASS 播放失败 (error {err})")
            self._state = PlaybackState.PLAYING

    def pause(self) -> None:
        with self._stream_lock:
            if self._state == PlaybackState.PLAYING:
                _bass.BASS_ChannelPause(self._tempo_stream)
                self._state = PlaybackState.PAUSED

    def stop(self) -> None:
        with self._stream_lock:
            if self._tempo_stream:
                _bass.BASS_ChannelStop(self._tempo_stream)
                _bass.BASS_ChannelSetPosition(self._tempo_stream, 0, BASS_POS_BYTE)
            self._state = PlaybackState.STOPPED
            self._last_reported_ms = 0

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — position
    # ═══════════════════════════════════════════════════════════════

    def get_position_ms(self) -> int:
        """Return the raw timing position in ms.

        No monotonic clamp is applied here; timing keys use this method.
        """
        if self._recovering:
            # Streams may be torn down mid-recovery; hold last known position.
            return self._last_reported_ms
        self._sync_state_from_bass()
        return self._read_position_ms(apply_latency=self._state == PlaybackState.PLAYING)

    def get_display_position_ms(self) -> int:
        """Return a UI-friendly monotonic position for progress displays."""
        if self._recovering:
            return self._last_reported_ms
        self._sync_state_from_bass()
        latency_adjusted_ms = self._read_position_ms(
            apply_latency=self._state == PlaybackState.PLAYING
        )
        reported_ms = latency_adjusted_ms
        if (
            self._state == PlaybackState.PLAYING
            and reported_ms < self._last_reported_ms
        ):
            reported_ms = self._last_reported_ms
        self._last_reported_ms = reported_ms

        return reported_ms

    def _read_position_ms(self, apply_latency: bool) -> int:
        if self._tempo_stream == 0:
            return 0
        pos = _bass.BASS_ChannelGetPosition(self._tempo_stream, BASS_POS_BYTE)
        ms = int(_bass.BASS_ChannelBytes2Seconds(self._tempo_stream, pos) * 1000)
        if (
            self._state != PlaybackState.PLAYING
            and self._duration_ms > 0
            and self._last_reported_ms >= self._duration_ms
            and ms == 0
        ):
            ms = self._duration_ms
        if apply_latency:
            ms = max(0, ms - self._output_latency_ms)
        return min(max(ms, 0), self._duration_ms)

    def set_position_ms(self, position_ms: int) -> None:
        with self._stream_lock:
            if self._tempo_stream == 0:
                return
            secs = max(0, min(position_ms, self._duration_ms)) / 1000.0
            # Seek on tempo stream — BASS_FX propagates to decode stream automatically.
            byte_pos = _bass.BASS_ChannelSeconds2Bytes(self._tempo_stream, ctypes.c_double(secs))
            _bass.BASS_ChannelSetPosition(self._tempo_stream, byte_pos, BASS_POS_BYTE)
            self._last_reported_ms = position_ms

    def get_duration_ms(self) -> int:
        return self._duration_ms

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — state
    # ═══════════════════════════════════════════════════════════════

    def get_playback_state(self) -> PlaybackState:
        self._sync_state_from_bass()
        return self._state

    def is_playing(self) -> bool:
        self._sync_state_from_bass()
        return self._state == PlaybackState.PLAYING

    def _sync_state_from_bass(self) -> None:
        if self._tempo_stream == 0 or self._recovering:
            return
        if self._state != PlaybackState.PLAYING:
            return

        active = _bass.BASS_ChannelIsActive(self._tempo_stream)
        if active == BASS_ACTIVE_PLAYING or active == BASS_ACTIVE_STALLED:
            return
        if active == BASS_ACTIVE_PAUSED_DEVICE:
            self._recover_device("device paused")
            return
        if active == BASS_ACTIVE_STOPPED:
            pos = self._read_position_ms(apply_latency=False)
            # Generous tolerance so VBR/MP3 length under-estimates don't look
            # like an "early" stop. Scales with output latency.
            tol = max(200, self._output_latency_ms * 2)
            if self._duration_ms > 0 and pos >= self._duration_ms - tol:
                # Normal end-of-track.
                self._state = PlaybackState.PAUSED
                self._last_reported_ms = self._duration_ms
            elif self._device_is_lost():
                # Genuine device loss (unplug / sample-rate change) — recover.
                self._recover_device("device lost")
            else:
                # Stopped early but the device is alive (benign decoder stop).
                # Treat as end-of-track; never loop on recovery here.
                self._state = PlaybackState.PAUSED
                self._last_reported_ms = max(self._last_reported_ms, pos)

    def _device_is_lost(self) -> bool:
        """True when the output device is no longer usable.

        BASS_GetInfo fails once the device has been lost (unplug, exclusive
        grab, sample-rate change), which distinguishes a real device fault from
        an ordinary end-of-stream stop.
        """
        info = BASS_INFO()
        return not _bass.BASS_GetInfo(ctypes.byref(info))

    def _recover_device(self, reason: str) -> None:
        """Trigger device recovery off the UI thread.

        Called from the position-poll path; must never block. Spawns a daemon
        thread (deduped via ``_recovering``) and returns immediately. Getters
        return the last known position while ``_recovering`` is set.
        """
        if self._recovering or not self._playback_path:
            return
        now = time.monotonic()
        if now - self._last_recovery_attempt < 1.0:
            return
        self._last_recovery_attempt = now
        self._recovering = True
        t = threading.Thread(
            target=self._run_recovery, args=(reason,), daemon=True, name="BassRecovery"
        )
        self._recovery_thread = t
        t.start()

    def _run_recovery(self, reason: str) -> None:
        try:
            print(f"[BassEngine] device recovery ({reason})...")
            self._do_recover()
        finally:
            self._recovering = False

    def _recover_device_sync(self, reason: str) -> bool:
        """Synchronous recovery for user-initiated play(). Returns success.

        If a background recovery is already running, wait briefly for it.
        Caller holds ``_stream_lock``.
        """
        if not self._playback_path:
            return False
        if self._recovering:
            if self._recovery_thread is not None:
                self._recovery_thread.join(timeout=3.0)
            return self._tempo_stream != 0
        self._recovering = True
        self._last_recovery_attempt = time.monotonic()
        try:
            print(f"[BassEngine] device recovery ({reason})...")
            return self._do_recover()
        finally:
            self._recovering = False

    def _do_recover(self) -> bool:
        """Rebuild the device + streams, preserving position/speed/volume.

        Returns True on success. Serialized via ``_stream_lock``.
        """
        with self._stream_lock:
            if not self._playback_path:
                return False
            position_ms = self._read_position_ms(apply_latency=False)
            should_resume = self._state == PlaybackState.PLAYING
            speed = self._speed
            volume = self._volume
            try:
                self._free_streams()
                _bass.BASS_Free()
                self._initialized = False
                if not self._ensure_initialized():
                    self._state = PlaybackState.PAUSED
                    return False
                self._create_streams(self._playback_path)
                self._speed = speed
                self._volume = volume
                self._apply_speed()
                self._apply_volume()
                self.set_position_ms(position_ms)
                if should_resume:
                    _bass.BASS_Start()
                    if _bass.BASS_ChannelPlay(self._tempo_stream, 0):
                        self._state = PlaybackState.PLAYING
                    else:
                        self._state = PlaybackState.PAUSED
                self._cache_output_latency()
                return True
            except Exception as exc:
                print(f"[BassEngine] device recovery failed: {exc}")
                self._state = PlaybackState.PAUSED
                return False

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — speed (real-time via BASS_FX)
    # ═══════════════════════════════════════════════════════════════

    def set_speed(self, speed: float) -> None:
        if not 0.2 <= speed <= 2.0:
            raise ValueError(f"速度 {speed} 超出范围 [0.2, 2.0]")
        self._speed = float(speed)
        self._apply_speed()
        if self._render_progress_cb is not None:
            try:
                self._render_progress_cb(self._speed, 1.0)
            except Exception:
                pass

    def _apply_speed(self) -> None:
        if self._tempo_stream:
            tempo_pct = (self._speed - 1.0) * 100.0
            _bass.BASS_ChannelSetAttribute(
                self._tempo_stream, BASS_ATTRIB_TEMPO, ctypes.c_float(tempo_pct)
            )

    def get_speed(self) -> float:
        return self._speed

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — volume
    # ═══════════════════════════════════════════════════════════════

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        self._apply_volume()

    def _apply_volume(self) -> None:
        if self._tempo_stream:
            _bass.BASS_ChannelSetAttribute(
                self._tempo_stream, BASS_ATTRIB_VOL, ctypes.c_float(self._volume)
            )

    def get_volume(self) -> float:
        return self._volume

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — callbacks
    # ═══════════════════════════════════════════════════════════════

    def set_position_callback(self, callback: Callable[[int], None]) -> None:
        self._position_callback = callback

    def clear_position_callback(self) -> None:
        self._position_callback = None

    def set_render_progress_callback(
        self, callback: Optional[Callable[[float, float], None]] = None
    ) -> None:
        self._render_progress_cb = callback

    # ═══════════════════════════════════════════════════════════════
    # IAudioEngine — info / waveform data
    # ═══════════════════════════════════════════════════════════════

    def get_audio_info(self) -> Optional[AudioInfo]:
        if self._file_path is None:
            return None
        return AudioInfo(
            file_path=self._file_path,
            duration_ms=self._duration_ms,
            sample_rate=self._original_sample_rate,
            channels=(
                self._original_data.shape[1]
                if self._original_data is not None
                else 2
            ),
        )

    def get_original_samples(self) -> Optional[np.ndarray]:
        """Return raw PCM (float32) for waveform display."""
        return self._original_data
