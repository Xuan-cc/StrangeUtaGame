"""BASS engine with offline TSM time-stretch (variable speed, constant pitch).

Why this exists
---------------
``BassEngine`` changes speed with BASS_FX (SoundTouch) in real time. That is
convenient but, depending on the material, can crackle/pop ("爆音"). This engine
instead reuses the project's proven offline time-stretcher (:class:`TSMRenderCache`
— Pedalboard ``time_stretch``, 30s chunks with ~3s render overlap, hard-cut
merge) to pre-render the whole track at the requested speed, then plays that
rendered file with a **plain BASS file stream**. BASS handles play/pause/seek/
position natively; speed change = swap the underlying source file + scale the
reported position.

Timeline mapping
----------------
A track rendered at ``speed=S`` has duration ``original/S`` (time-stretch is
time-based, sample-rate independent). So:

    original_ms = stream_ms * S          (position read-out)
    stream_ms   = original_ms / S        (seek)

``get_duration_ms`` always returns the *original* duration, and timing positions
are always on the original timeline, exactly like the old SoundDevice engine.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

import ctypes

import numpy as np
import soundfile as sf

from .base import (
    AudioInfo,
    AudioLoadError,
    AudioPlaybackError,
    IAudioEngine,
    PlaybackState,
)

# Reuse the ctypes bindings + constants already configured in bass_engine so we
# don't redefine signatures (and so both engines share one loaded DLL).
from .bass_engine import (
    _bass,
    _bass_fx,
    BASS_ACTIVE_PAUSED_DEVICE,
    BASS_ACTIVE_PLAYING,
    BASS_ACTIVE_STALLED,
    BASS_ACTIVE_STOPPED,
    BASS_ATTRIB_TEMPO,
    BASS_ATTRIB_TEMPO_OPTION_PREVENT_CLICK,
    BASS_ATTRIB_TEMPO_OPTION_USE_AA_FILTER,
    BASS_ATTRIB_VOL,
    BASS_CHANNELINFO,
    BASS_DATA_FLOAT,
    BASS_DEVICE_LATENCY,
    BASS_ERROR_ALREADY,
    BASS_FX_FREESOURCE,
    BASS_INFO,
    BASS_POS_BYTE,
    BASS_SAMPLE_FLOAT,
    BASS_STREAM_DECODE,
    BASS_STREAM_PRESCAN,
    BASS_UNICODE,
)
from .tsm_cache import (
    TSMRenderCache,
    _get_cache_dir,
    _get_cache_path,
    _get_source_mp3_path,
    _quantize,
)

# SoundTouch anti-alias filter length (taps). Longer = less aliasing/metallic
# artifact at the cost of a little CPU. Not exported by bass_engine.
BASS_ATTRIB_TEMPO_OPTION_AA_FILTER_LENGTH = 0x10011


class BassTsmEngine(IAudioEngine):
    """Pitch-preserving variable-speed playback via offline TSM + BASS."""

    def __init__(self) -> None:
        self._state = PlaybackState.STOPPED
        self._file_path: Optional[str] = None          # user's original file
        self._source_1x_path: Optional[str] = None      # BASS-openable 1.0x source
        self._current_source_path: Optional[str] = None  # file currently feeding stream
        self._duration_ms: int = 0                       # ORIGINAL timeline duration
        self._speed: float = 1.0                         # requested speed
        self._speed_scale: float = 1.0                   # render scale of a FILE-mode stream
        self._volume: float = 1.0
        self._stream: int = 0                            # active BASS stream
        # When True the active stream is a real-time BASS_FX tempo stream
        # (interim, may crackle) instead of a clean pre-rendered file stream.
        # A tempo stream reports position on the ORIGINAL timeline directly,
        # so its effective position scale is 1.0.
        self._is_tempo: bool = False
        self._tempo_speed: float = 1.0                   # speed of the tempo stream

        self._position_callback: Optional[Callable[[int], None]] = None
        self._render_progress_cb: Optional[Callable[[float, float], None]] = None

        # waveform data for visualisation (source PCM)
        self._original_data: Optional[np.ndarray] = None
        self._original_sample_rate: int = 44100
        self._channels: int = 2

        # offline time-stretch cache (the user's 30s/3s algorithm)
        self._cache = TSMRenderCache()
        # speed whose render we are waiting for; applied once ready
        self._pending_speed: Optional[float] = None
        self._ready_speed: Optional[float] = None  # set by render done_cb thread

        self._output_latency_ms: int = 0
        self._initialized = False
        self._recovering = False
        self._last_recovery_attempt = 0.0
        self._last_reported_ms: int = 0

        self._stream_lock = threading.RLock()

        # QW 防抖：用户连续按 QW 调速时，实时引擎立即响应，但 TSM 渲染任务
        # 派发延迟 500ms——只有 500ms 内没有再次调速，才实际提交渲染任务。
        # 这样可以避免每按一次 QW 都派发一个抢占任务，产生大量无效渲染。
        _DEBOUNCE_DELAY_S = 0.5
        self._tsm_debounce_delay: float = _DEBOUNCE_DELAY_S
        self._tsm_debounce_timer: Optional[threading.Timer] = None
        self._tsm_debounce_lock = threading.Lock()

        self._ensure_initialized()

    # ════════════════════════════════════ init / latency

    def _cache_output_latency(self) -> None:
        info = BASS_INFO()
        if _bass.BASS_GetInfo(ctypes.byref(info)):
            self._output_latency_ms = max(0, int(info.latency))

    def _ensure_initialized(self) -> bool:
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
        print(f"[TSM引擎] BASS 初始化失败（错误码 {err}），稍后重试")
        self._initialized = False
        return False

    # ════════════════════════════════════ load / release

    def load(self, file_path: str, progress_cb=None) -> None:
        with self._stream_lock:
            self.stop()
            self._free_stream()
            # 立即清空源路径，防止 UI 线程 poll（_sync_state_from_bass）在
            # clear_cache() 删文件后仍用旧路径尝试打开流（BASS error 8）。
            self._source_1x_path = None
            self._current_source_path = None
            self._pending_speed = None
            self._ready_speed = None

            if not Path(file_path).is_file():
                raise AudioLoadError(f"加载音频失败: 文件不存在: {file_path}")
            if not self._ensure_initialized():
                err = _bass.BASS_ErrorGetCode()
                raise AudioLoadError(f"BASS 初始化失败 (error {err})")

            if progress_cb:
                progress_cb("读取音频...", 0.0)

            # Resolve a path BASS can open (convert unsupported containers).
            playback_path = self._resolve_source_path(file_path, progress_cb)

            # Decode full source PCM (float32, (n, ch)) for both the waveform
            # display and the TSM renderer.
            pcm, sr, ch = self._decode_full_pcm(playback_path)
            self._original_data = pcm
            self._original_sample_rate = sr
            self._channels = ch

            # Original-timeline duration from the source PCM.
            self._duration_ms = int(len(pcm) / sr * 1000) if sr else 0

            # Feed the offline stretcher. NOTE: set_source() calls clear_cache()
            # which deletes ALL .cache/*.mp3 — including a video-extracted or
            # soundfile-fallback source that lives in .cache. So if our playback
            # source is inside the cache dir, it is now gone; fall back to the
            # canonical re-encoded source mp3 that set_source just wrote (it
            # survives because clear_cache runs before it is written). PCM was
            # already decoded above, so the deletion does not affect waveform.
            if progress_cb:
                progress_cb("准备变速缓存...", 0.6)
            song_name = Path(file_path).stem
            self._cache.set_source(song_name, pcm, sr)

            if self._path_in_cache_dir(playback_path):
                src_mp3 = _get_source_mp3_path(song_name)
                if src_mp3.exists():
                    # Drop a now-redundant soundfile-fallback wav (survives the
                    # mp3-only clear_cache) before repointing to the source mp3.
                    if playback_path.endswith("_bass_src.wav"):
                        try:
                            Path(playback_path).unlink()
                        except Exception:
                            pass
                    playback_path = str(src_mp3)

            # Active stream = 1.0x source (clean file mode).
            self._source_1x_path = playback_path
            self._current_source_path = playback_path
            self._speed = 1.0
            self._speed_scale = 1.0
            self._is_tempo = False
            self._tempo_speed = 1.0
            self._pending_speed = None
            self._ready_speed = None
            if not self._build_stream_locked(target_original_ms=0, resume=False):
                raise AudioLoadError(f"BASS 无法打开播放流: {playback_path}")

            self._file_path = file_path
            self._state = PlaybackState.STOPPED
            self._last_reported_ms = 0
            self._cache_output_latency()

            if progress_cb:
                progress_cb("就绪", 1.0)

    def _resolve_source_path(self, file_path: str, progress_cb=None) -> str:
        """Open directly if BASS can; else convert (video extract / wav)."""
        test = _bass.BASS_StreamCreateFile(
            0, ctypes.c_wchar_p(file_path), 0, 0,
            BASS_STREAM_DECODE | BASS_UNICODE,
        )
        if test:
            _bass.BASS_StreamFree(test)
            return file_path

        try:
            from .video_converter import VIDEO_EXTENSIONS, extract_audio

            if Path(file_path).suffix.lower() in VIDEO_EXTENSIONS:
                return extract_audio(file_path, progress_cb=progress_cb)
        except Exception as exc:
            raise AudioLoadError(str(exc)) from exc

        # soundfile fallback → wav in cache dir
        try:
            from .tsm_cache import _get_cache_dir

            data, sr = sf.read(str(file_path), dtype="float32")
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            wav_path = _get_cache_dir() / f"{Path(file_path).stem}_bass_src.wav"
            sf.write(str(wav_path), data, sr)
            return str(wav_path)
        except Exception as exc:
            raise AudioLoadError(f"BASS 无法打开文件，且转换失败: {exc}") from exc

    @staticmethod
    def _decode_full_pcm(path: str) -> tuple[np.ndarray, int, int]:
        """Decode the whole file to float32 (n, ch) via BASS."""
        ds = _bass.BASS_StreamCreateFile(
            0, ctypes.c_wchar_p(path), 0, 0,
            BASS_STREAM_DECODE | BASS_STREAM_PRESCAN | BASS_SAMPLE_FLOAT | BASS_UNICODE,
        )
        if not ds:
            # last-ditch soundfile
            data, sr = sf.read(str(path), dtype="float32")
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            return np.ascontiguousarray(data, np.float32), int(sr), int(data.shape[1])
        try:
            info = BASS_CHANNELINFO()
            _bass.BASS_ChannelGetInfo(ds, ctypes.byref(info))
            byte_len = _bass.BASS_ChannelGetLength(ds, BASS_POS_BYTE)
            total = int(byte_len // 4)
            raw = np.empty(max(total, 0), dtype=np.float32)
            off = 0
            chunk = 262144
            while off < total:
                take = min(chunk, total - off)
                ptr = ctypes.c_void_p(raw.ctypes.data + off * 4)
                got = _bass.BASS_ChannelGetData(ds, ptr, (take * 4) | BASS_DATA_FLOAT)
                if got <= 0:
                    break
                off += got // 4
            raw = raw[:off]
            ch = max(1, int(info.chans))
            n = len(raw) // ch
            pcm = np.ascontiguousarray(raw[: n * ch].reshape(n, ch), np.float32)
            return pcm, int(info.freq) or 44100, ch
        finally:
            _bass.BASS_StreamFree(ds)

    @staticmethod
    def _path_in_cache_dir(path: str) -> bool:
        """True if ``path`` lives in the TSM .cache dir (and thus may be wiped
        by clear_cache during set_source)."""
        try:
            return Path(path).resolve().parent == _get_cache_dir().resolve()
        except Exception:
            return False

    def _prewarm_common_speeds(
        self,
        speed_min: float = 0.2,
        speed_max: float = 2.0,
    ) -> None:
        """后台预渲染常用速度档。

        仅渲染 [speed_min, speed_max] 范围内的速度，与 UI 滑块上下限保持一致，
        避免派发用户永远不会用到的无效渲染任务。

        预热任务不传 done_cb，因为它们在后台静默完成，不需要触发换源逻辑。
        换源仅由用户主动调用 set_speed() 后的 ensure(preempt=True) 触发。
        """
        # Users almost always slow down for timing (0.9 → 0.2), so render the
        # high-to-low slow-down speeds first, in descending order of likelihood.
        # A couple of speed-ups trail at the end.
        order = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 1.25, 1.5]
        prio = 0
        for speed in order:
            if speed_min - 1e-9 <= speed <= speed_max + 1e-9:
                # 不传 done_cb：预热任务完成后不触发 _on_render_ready，
                # 实际速度只由用户指令（set_speed）管理。
                self._cache.ensure(speed, priority=prio)
                prio += 1

    def _free_stream(self) -> None:
        if self._stream:
            _bass.BASS_StreamFree(self._stream)
            self._stream = 0

    def release(self) -> None:
        # 取消防抖 timer，防止 engine 释放后回调仍然触发
        with self._tsm_debounce_lock:
            if self._tsm_debounce_timer is not None:
                self._tsm_debounce_timer.cancel()
                self._tsm_debounce_timer = None
        with self._stream_lock:
            self.stop()
            self._free_stream()
            try:
                self._cache.clear()
            except Exception:
                pass
            self._cleanup_src_wav()
            if self._initialized:
                _bass.BASS_Free()
                self._initialized = False
            self._original_data = None
            self._file_path = None
            self._source_1x_path = None
            self._current_source_path = None
            self._duration_ms = 0
            self._position_callback = None

    def _cleanup_src_wav(self) -> None:
        p = self._source_1x_path
        if p and p.endswith("_bass_src.wav"):
            try:
                Path(p).unlink()
            except Exception:
                pass

    # ════════════════════════════════════ source switching (= speed)

    def _rendered_path(self, q: float) -> Optional[str]:
        """Path of the rendered file for quantised speed q, if on disk."""
        if abs(q - 1.0) < 1e-9:
            return self._source_1x_path
        path = _get_cache_path(self._cache._song_name, q)
        return str(path) if path.exists() else None

    def _effective_scale(self) -> float:
        """Position scale of the active stream.

        File-mode rendered stream: original_ms = stream_ms * render_scale.
        Tempo-mode stream: BASS_FX reports SOURCE position already → scale 1.0.
        """
        return 1.0 if self._is_tempo else self._speed_scale

    def _build_stream_locked(self, target_original_ms: int, resume: bool) -> bool:
        """(Re)create the active stream for the current mode. Caller holds lock.

        Mode is decided by ``_is_tempo``:
          - tempo: real-time BASS_FX on the 1x source at ``_tempo_speed``;
          - file:  plain playable stream of ``_current_source_path``.
        """
        if not self._ensure_initialized():
            return False

        if self._is_tempo:
            new_stream = self._create_tempo_stream(self._source_1x_path, self._tempo_speed)
        else:
            if not self._current_source_path:
                print("[TSM引擎] 流打开失败：源文件路径为空（加载中或未加载）")
                return False
            new_stream = _bass.BASS_StreamCreateFile(
                0, ctypes.c_wchar_p(self._current_source_path), 0, 0,
                BASS_SAMPLE_FLOAT | BASS_STREAM_PRESCAN | BASS_UNICODE,
            )
        if not new_stream:
            err = _bass.BASS_ErrorGetCode()
            print(f"[TSM引擎] 流打开失败（错误码 {err}，模式={'实时' if self._is_tempo else '文件'}）")
            return False

        scale = self._effective_scale()
        stream_ms = max(0, target_original_ms) / max(scale, 1e-6)
        byte_pos = _bass.BASS_ChannelSeconds2Bytes(
            new_stream, ctypes.c_double(stream_ms / 1000.0)
        )
        _bass.BASS_ChannelSetPosition(new_stream, byte_pos, BASS_POS_BYTE)
        _bass.BASS_ChannelSetAttribute(
            new_stream, BASS_ATTRIB_VOL, ctypes.c_float(self._volume)
        )

        if resume and not _bass.BASS_ChannelPlay(new_stream, 0):
            err = _bass.BASS_ErrorGetCode()
            _bass.BASS_StreamFree(new_stream)
            print(
                f"[TSM引擎] 新流播放失败（错误码 {err}，模式={'实时' if self._is_tempo else '文件'}）"
            )
            return False

        old_stream = self._stream
        self._stream = new_stream
        if old_stream:
            _bass.BASS_StreamFree(old_stream)
        return True

    def _create_tempo_stream(self, source_path: Optional[str], speed: float) -> int:
        """Build a real-time BASS_FX tempo stream on the 1x source."""
        if not source_path:
            return 0
        decode = _bass.BASS_StreamCreateFile(
            0, ctypes.c_wchar_p(source_path), 0, 0,
            BASS_STREAM_DECODE | BASS_STREAM_PRESCAN | BASS_SAMPLE_FLOAT | BASS_UNICODE,
        )
        if not decode:
            return 0
        st = _bass_fx.BASS_FX_TempoCreate(decode, BASS_FX_FREESOURCE)
        if not st:
            _bass.BASS_StreamFree(decode)
            return 0
        _bass.BASS_ChannelSetAttribute(
            st, BASS_ATTRIB_TEMPO, ctypes.c_float((speed - 1.0) * 100.0)
        )
        # Reduce the metallic/click artifacts of the interim real-time stretch.
        for attrib, value in (
            (BASS_ATTRIB_TEMPO_OPTION_PREVENT_CLICK, 1.0),
            (BASS_ATTRIB_TEMPO_OPTION_USE_AA_FILTER, 1.0),
            (BASS_ATTRIB_TEMPO_OPTION_AA_FILTER_LENGTH, 64.0),
        ):
            _bass.BASS_ChannelSetAttribute(st, attrib, ctypes.c_float(value))
        return st

    def _seek_stream(self, original_ms: int) -> None:
        if self._stream == 0:
            return
        stream_ms = max(0, original_ms) / max(self._effective_scale(), 1e-6)
        secs = stream_ms / 1000.0
        byte_pos = _bass.BASS_ChannelSeconds2Bytes(self._stream, ctypes.c_double(secs))
        _bass.BASS_ChannelSetPosition(self._stream, byte_pos, BASS_POS_BYTE)

    def set_speed(self, speed: float) -> None:
        """设置播放速度。

        实际速度（_speed）只由此方法管理，不受后台渲染完成/启动渲染的影响。
        _notify_render 仅在有后台渲染任务进行中时上报进度，不在此处调用，
        避免干扰 UI 层对渲染状态的判断。
        """
        if not 0.2 <= speed <= 2.0:
            raise ValueError(f"速度 {speed} 超出范围 [0.2, 2.0]")
        with self._stream_lock:
            self._speed = float(speed)
            q = _quantize(self._speed)

            # Already playing the clean rendered file for this speed?
            # 直接返回，不发 _notify_render：没有后台渲染任务在跑，无需上报进度。
            if not self._is_tempo and abs(q - self._speed_scale) < 1e-9:
                self._pending_speed = None
                return

            ready = self._rendered_path(q)
            if ready is not None:
                # Clean offline render available → play it immediately.
                # 直接切换文件流，不发 _notify_render：渲染早已完成，无需进度上报。
                self._switch_to_file(q, ready)
                self._pending_speed = None
                return

            # Not rendered yet → play the requested speed RIGHT NOW via real-time
            # BASS_FX tempo (may crackle), and kick off the offline render. When
            # it finishes we swap to the clean file (see _maybe_apply_ready_speed).
            if self._is_tempo:
                self._retune_tempo(self._speed)
            else:
                self._switch_to_tempo(self._speed)
            self._pending_speed = q
            # 防抖派发 TSM 渲染任务：实时引擎已立即响应（retune/switch_to_tempo），
            # 但抢占渲染任务需要等 500ms 内没有再次调速才提交，避免 QW 连续调速
            # 时产生大量无效抢占任务。
            self._schedule_tsm_render(q)

    def _schedule_tsm_render(self, q: float) -> None:
        """防抖调度 TSM 渲染任务。

        取消上一个未触发的 timer，重新计时 500ms。到期后若目标速度未变，
        才实际提交 ensure(preempt=True) 渲染任务。
        """
        with self._tsm_debounce_lock:
            if self._tsm_debounce_timer is not None:
                self._tsm_debounce_timer.cancel()
                self._tsm_debounce_timer = None
                print(f"[TSM防抖] 重置防抖计时器，新目标速度 {q}x")
            else:
                print(f"[TSM防抖] 启动防抖计时器，目标速度 {q}x，等待 {int(self._tsm_debounce_delay * 1000)}ms")
            t = threading.Timer(
                self._tsm_debounce_delay,
                self._fire_tsm_render,
                args=(q,),
            )
            t.daemon = True
            t.name = "TSMDebounce"
            self._tsm_debounce_timer = t
            t.start()

    def _fire_tsm_render(self, q: float) -> None:
        """防抖到期后实际提交渲染任务（在 TSMDebounce 线程执行）。

        若用户在 500ms 内再次调速，_pending_speed 已更新，此处通过比较确认
        目标速度未变后才派发任务，避免为过时速度浪费渲染资源。
        """
        with self._tsm_debounce_lock:
            self._tsm_debounce_timer = None
        # 确认目标速度未发生变化
        with self._stream_lock:
            if self._pending_speed is None or abs(self._pending_speed - q) > 1e-9:
                print(
                    f"[TSM防抖] 速度 {q}x 防抖到期，但目标速度已变更为"
                    f" {self._pending_speed}x，放弃派发"
                )
                return
            print(f"[TSM防抖] 速度 {q}x 防抖到期，派发渲染任务（抢占模式）")
            # UI 主动申请：最高优先级 + 抢占当前预热渲染，尽快产出干净音频。
            # progress_cb 负责上报渲染进度给 UI，done_cb 负责触发换源。
            self._cache.ensure(
                q,
                priority=-1,
                progress_cb=self._render_progress_cb,
                done_cb=self._on_render_ready,
                preempt=True,
            )

    def _switch_to_file(self, q: float, path: str) -> None:
        cur_ms = self._read_position_ms(apply_latency=False)
        resume = self._state == PlaybackState.PLAYING
        self._is_tempo = False
        self._speed_scale = q
        self._current_source_path = path
        self._build_stream_locked(target_original_ms=cur_ms, resume=resume)
        kind = "原始 1.0x" if abs(q - 1.0) < 1e-9 else "预渲染（无损）"
        print(f"[TSM引擎] 切换音源 → {kind}，速度 {q:.2f}x，文件: {Path(path).name}")

    def _switch_to_tempo(self, speed: float) -> None:
        cur_ms = self._read_position_ms(apply_latency=False)
        resume = self._state == PlaybackState.PLAYING
        self._is_tempo = True
        self._tempo_speed = speed
        self._current_source_path = self._source_1x_path
        self._build_stream_locked(target_original_ms=cur_ms, resume=resume)
        print(f"[TSM引擎] 切换音源 → 实时变速（临时，可能爆音），速度 {speed:.2f}x，后台渲染中...")

    def _retune_tempo(self, speed: float) -> None:
        """Change an existing real-time BASS_FX tempo stream in place."""
        self._tempo_speed = speed
        if self._stream:
            _bass.BASS_ChannelSetAttribute(
                self._stream, BASS_ATTRIB_TEMPO, ctypes.c_float((speed - 1.0) * 100.0)
            )

    def get_speed_mode(self) -> str:
        """当前变速音源类型，便于上层/调试判断是否已用上无爆音音频。

        - "original": 1.0x 原始音频
        - "rendered": 已切到离线预渲染文件（无损/无爆音）
        - "realtime": 实时 BASS_FX 临时变速（渲染未就绪，可能爆音）
        """
        if self._is_tempo:
            return "realtime"
        if abs(self._speed_scale - 1.0) < 1e-9:
            return "original"
        return "rendered"

    def prewarm_speeds(
        self,
        speed_min: float = 0.2,
        speed_max: float = 2.0,
    ) -> None:
        """以指定速度范围触发后台预渲染（公共接口，供 UI 层调用）。

        已渲染或已入队的速度会被幂等跳过。UI 层在 _on_audio_loaded 时
        以实际滑块上下限调用此方法，确保只派发用户可能用到的速度任务。
        """
        with self._stream_lock:
            self._prewarm_common_speeds(speed_min=speed_min, speed_max=speed_max)

    def _on_render_ready(self, speed: float) -> None:
        """Render done_cb — runs on the TSM finalizer thread. Just flag it;
        the actual stream swap happens on the UI thread during the next poll."""
        self._ready_speed = _quantize(speed)

    def _maybe_apply_ready_speed(self) -> None:
        """Apply a finished background render if it matches the desired speed."""
        ready = self._ready_speed
        if ready is None:
            return
        self._ready_speed = None
        if self._pending_speed is None or abs(ready - self._pending_speed) > 1e-9:
            print(
                f"[TSM引擎] 速度 {ready}x 渲染完成，但目标速度已变更为"
                f" {self._pending_speed}x，丢弃过时渲染，不换源"
            )
            return
        path = self._rendered_path(ready)
        if path is None:
            return
        # Swap the interim real-time tempo stream for the clean rendered file.
        print(f"[TSM引擎] 速度 {ready}x 渲染完成，从实时变速切换为预渲染无损音源")
        self._switch_to_file(ready, path)
        self._pending_speed = None
        self._notify_render(ready, 1.0)

    def _notify_render(self, speed: float, progress: float) -> None:
        if self._render_progress_cb is not None:
            try:
                self._render_progress_cb(speed, progress)
            except Exception:
                pass

    def get_speed(self) -> float:
        return self._speed

    # ════════════════════════════════════ transport

    def play(self) -> None:
        with self._stream_lock:
            if self._stream == 0:
                if not self._rebuild_stream():
                    raise AudioPlaybackError("没有加载音频文件")
            if self._state == PlaybackState.PLAYING:
                return
            if not _bass.BASS_ChannelPlay(self._stream, 0):
                if not self._recover_device("play failed"):
                    err = _bass.BASS_ErrorGetCode()
                    raise AudioPlaybackError(f"BASS 播放失败 (error {err})")
                _bass.BASS_ChannelPlay(self._stream, 0)
            self._state = PlaybackState.PLAYING

    def pause(self) -> None:
        with self._stream_lock:
            if self._state == PlaybackState.PLAYING:
                _bass.BASS_ChannelPause(self._stream)
                self._state = PlaybackState.PAUSED

    def stop(self) -> None:
        with self._stream_lock:
            if self._stream:
                _bass.BASS_ChannelStop(self._stream)
                _bass.BASS_ChannelSetPosition(self._stream, 0, BASS_POS_BYTE)
            self._state = PlaybackState.STOPPED
            self._last_reported_ms = 0

    def _rebuild_stream(self) -> bool:
        if not (self._current_source_path or self._source_1x_path):
            return False
        if not self._ensure_initialized():
            return False
        return self._build_stream_locked(
            target_original_ms=self._last_reported_ms, resume=False
        )

    # ════════════════════════════════════ position

    def get_position_ms(self) -> int:
        if self._recovering:
            return self._last_reported_ms
        self._sync_state_from_bass()
        return self._read_position_ms(apply_latency=self._state == PlaybackState.PLAYING)

    def get_display_position_ms(self) -> int:
        if self._recovering:
            return self._last_reported_ms
        self._sync_state_from_bass()
        ms = self._read_position_ms(apply_latency=self._state == PlaybackState.PLAYING)
        if self._state == PlaybackState.PLAYING and ms < self._last_reported_ms:
            ms = self._last_reported_ms
        self._last_reported_ms = ms
        return ms

    def _read_position_ms(self, apply_latency: bool) -> int:
        if self._stream == 0:
            return 0
        pos = _bass.BASS_ChannelGetPosition(self._stream, BASS_POS_BYTE)
        stream_ms = _bass.BASS_ChannelBytes2Seconds(self._stream, pos) * 1000
        if apply_latency:
            stream_ms = max(0.0, stream_ms - self._output_latency_ms)
        # Map stream timeline → original timeline (tempo stream already reports
        # original time → scale 1.0; rendered file → multiply by render scale).
        original_ms = int(round(stream_ms * self._effective_scale()))
        if (
            self._state != PlaybackState.PLAYING
            and self._duration_ms > 0
            and self._last_reported_ms >= self._duration_ms
            and pos == 0
        ):
            original_ms = self._duration_ms
        return min(max(original_ms, 0), self._duration_ms)

    def set_position_ms(self, position_ms: int) -> None:
        with self._stream_lock:
            if self._stream == 0:
                return
            target = max(0, min(position_ms, self._duration_ms))
            self._seek_stream(target)
            self._last_reported_ms = target

    def get_duration_ms(self) -> int:
        return self._duration_ms

    # ════════════════════════════════════ state / recovery

    def get_playback_state(self) -> PlaybackState:
        self._sync_state_from_bass()
        return self._state

    def is_playing(self) -> bool:
        self._sync_state_from_bass()
        return self._state == PlaybackState.PLAYING

    def _sync_state_from_bass(self) -> None:
        if self._stream == 0 or self._recovering:
            return
        # Opportunistically apply any finished background render.
        self._maybe_apply_ready_speed()
        if self._state != PlaybackState.PLAYING:
            return
        active = _bass.BASS_ChannelIsActive(self._stream)
        if active in (BASS_ACTIVE_PLAYING, BASS_ACTIVE_STALLED):
            return
        if active == BASS_ACTIVE_PAUSED_DEVICE:
            self._recover_device("device paused")
            return
        if active == BASS_ACTIVE_STOPPED:
            pos = self._read_position_ms(apply_latency=False)
            tol = max(200, self._output_latency_ms * 2)
            if self._duration_ms > 0 and pos >= self._duration_ms - tol:
                self._state = PlaybackState.PAUSED
                self._last_reported_ms = self._duration_ms
            elif self._device_is_lost():
                self._recover_device("device lost")
            else:
                self._state = PlaybackState.PAUSED
                self._last_reported_ms = max(self._last_reported_ms, pos)

    def _device_is_lost(self) -> bool:
        info = BASS_INFO()
        return not _bass.BASS_GetInfo(ctypes.byref(info))

    def _recover_device(self, reason: str) -> bool:
        """Synchronous, throttled device recovery. Returns success."""
        if self._recovering:
            return self._stream != 0
        now = time.monotonic()
        if now - self._last_recovery_attempt < 1.0:
            return False
        self._last_recovery_attempt = now
        self._recovering = True
        pos = self._read_position_ms(apply_latency=False)
        resume = self._state == PlaybackState.PLAYING
        has_source = bool(self._current_source_path or self._source_1x_path)
        try:
            print(f"[TSM引擎] 设备恢复中（原因: {reason}）...")
            self._free_stream()
            _bass.BASS_Free()
            self._initialized = False
            if not self._ensure_initialized() or not has_source:
                self._state = PlaybackState.PAUSED
                return False
            ok = self._build_stream_locked(pos, resume)
            self._state = PlaybackState.PLAYING if (ok and resume) else PlaybackState.PAUSED
            self._cache_output_latency()
            return ok
        except Exception as exc:
            print(f"[TSM引擎] 设备恢复失败: {exc}")
            self._state = PlaybackState.PAUSED
            return False
        finally:
            self._recovering = False

    # ════════════════════════════════════ volume / callbacks / info

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        self._apply_volume()

    def _apply_volume(self) -> None:
        if self._stream:
            _bass.BASS_ChannelSetAttribute(
                self._stream, BASS_ATTRIB_VOL, ctypes.c_float(self._volume)
            )

    def get_volume(self) -> float:
        return self._volume

    def set_position_callback(self, callback: Callable[[int], None]) -> None:
        self._position_callback = callback

    def clear_position_callback(self) -> None:
        self._position_callback = None

    def set_render_progress_callback(
        self, callback: Optional[Callable[[float, float], None]] = None
    ) -> None:
        self._render_progress_cb = callback

    def get_audio_info(self) -> Optional[AudioInfo]:
        if self._file_path is None:
            return None
        return AudioInfo(
            file_path=self._file_path,
            duration_ms=self._duration_ms,
            sample_rate=self._original_sample_rate,
            channels=self._channels,
        )

    def get_original_samples(self) -> Optional[np.ndarray]:
        return self._original_data
