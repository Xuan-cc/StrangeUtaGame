"""SugConcatWorker - 在后台线程中执行多个SUG的拼接。

使用标准的 QObject + moveToThread 模式，
通过信号将进度和结果传回主线程。
"""

from __future__ import annotations

import json
from pathlib import Path

import soundfile as sf  # noqa: F401  用于 sndfile probe
from PyQt6.QtCore import QObject, pyqtSignal

from strange_uta_game.backend.infrastructure.persistence.sug_io import (
    SugMigrator,
    SugProjectParser,
)
from strange_uta_game.frontend.editor.timing.sug_concat_dialog import SugEntry


def _probe_audio_duration_multi(media_path: str) -> int:
    """多渠道尝试读取音频/视频时长（毫秒），失败返回 0。

    按顺序尝试：soundfile → MP3 帧头 → BASS。
    """
    if not media_path or not Path(media_path).exists():
        return 0

    # 1. soundfile（支持 WAV / FLAC / OGG）
    try:
        import soundfile as _sf
        info = _sf.info(media_path)
        if info.duration > 0:
            return int(info.duration * 1000)
    except Exception:
        pass

    # 2. MP3 帧头
    ext = Path(media_path).suffix.lower()
    if ext == ".mp3":
        try:
            return _read_mp3_duration(media_path)
        except Exception:
            pass

    # 3. BASS（支持 MP3/MP4/M4A/AAC/WMA 等所有格式）
    try:
        return _read_duration_via_bass_probe(media_path)
    except Exception:
        pass

    return 0


def _read_mp3_duration(file_path: str) -> int:
    """通过读取 MP3 帧头估算时长。"""
    if not file_path or not Path(file_path).exists():
        return 0
    with open(file_path, "rb") as f:
        header = f.read(10)
        if header[:3] == b"ID3":
            size_bytes = header[6:10]
            id3_size = (
                (size_bytes[0] << 21)
                | (size_bytes[1] << 14)
                | (size_bytes[2] << 7)
                | size_bytes[3]
            )
            f.seek(id3_size + 10)
        else:
            f.seek(0)

        data = f.read(4096)
        sync_pos = -1
        for i in range(len(data) - 1):
            if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
                sync_pos = i
                break
        if sync_pos >= 0:
            f.seek(sync_pos)

        header_bytes = f.read(4)
        if len(header_bytes) < 4:
            return 0

        b1, b2, b3, b4 = header_bytes
        if b1 != 0xFF or (b2 & 0xE0) != 0xE0:
            return 0

        version_idx = (b2 >> 3) & 0x03
        bitrate_idx = (b3 >> 4) & 0x0F
        sample_rate_idx = (b3 >> 2) & 0x03

        bitrate_table = {
            1: [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0],
            2: [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0],
            3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
        }
        sample_rate_table = {
            0: [44100, 22050, 11025],
            1: [48000, 24000, 12000],
            2: [32000, 16000, 8000],
        }

        v = version_idx if version_idx != 3 else 1
        bitrate_kbps = bitrate_table.get(v, [0] * 16)[bitrate_idx] if bitrate_idx < 16 else 0
        sample_rate = sample_rate_table.get(version_idx, [0, 0, 0])[sample_rate_idx] if sample_rate_idx < 3 else 0

        if bitrate_kbps == 0 or sample_rate == 0:
            return 0

        file_size = Path(file_path).stat().st_size
        return int((file_size * 8) / (bitrate_kbps * 1000) * 1000)


def _read_duration_via_bass_probe(file_path: str) -> int:
    """通过 BASS 临时初始化解码读取音频/视频时长。

    使用设备 0（无声音），解码模式打开文件，读取长度后释放。
    仅支持 Windows（BASS DLL）。
    """
    import ctypes as _ct
    import sys as _sys

    if _sys.platform != "win32":
        return 0

    try:
        from strange_uta_game.backend.infrastructure.audio.bass_engine import (
            _bass,
            _BASS_DIR,
        )

        # 常量
        BASS_STREAM_DECODE = 0x200000
        BASS_POS_BYTE = 0
        BASS_UNICODE = 0x80000000
        BASS_DEVICE_LATENCY = 0x100

        # 确保拥有完整签名：BASS_ChannelBytes2Seconds 和 BASS_ChannelGetLength
        if not hasattr(_bass, "BASS_ChannelBytes2Seconds") or \
           not hasattr(_bass, "BASS_StreamFree"):
            # 补全缺失的函数签名
            try:
                _bass.BASS_ChannelBytes2Seconds.restype = _ct.c_int
                _bass.BASS_ChannelBytes2Seconds.argtypes = [
                    _ct.c_uint, _ct.c_uint64, _ct.POINTER(_ct.c_float),
                ]
                _bass.BASS_StreamFree.restype = _ct.c_int
                _bass.BASS_StreamFree.argtypes = [_ct.c_uint]
            except Exception:
                pass

        # 尝试用设备 0 初始化（不同设备号以 0=无声音 最轻量）
        tried_devices = [0, -1]  # -1 = 不初始化任何设备
        bass_inited = False
        for dev in tried_devices:
            try:
                if _bass.BASS_Init(dev, 44100, 0, None, None):
                    bass_inited = True
                    break
            except Exception:
                continue

        if not bass_inited:
            return 0

        try:
            # BASS_StreamCreateFile（Unicode 路径）
            try:
                _bass.BASS_StreamCreateFile.restype = _ct.c_uint
                _bass.BASS_StreamCreateFile.argtypes = [
                    _ct.c_int, _ct.c_void_p, _ct.c_uint64, _ct.c_uint64, _ct.c_uint,
                ]
            except Exception:
                pass

            flags = BASS_STREAM_DECODE | BASS_UNICODE
            handle = _bass.BASS_StreamCreateFile(False, str(file_path), 0, 0, flags, 0)
            if not handle:
                return 0

            try:
                byte_len = _bass.BASS_ChannelGetLength(handle, BASS_POS_BYTE)
                if byte_len <= 0:
                    return 0
                dur = _ct.c_float()
                ok = _bass.BASS_ChannelBytes2Seconds(handle, _ct.c_uint64(byte_len), _ct.byref(dur))
                if ok:
                    return int(dur.value * 1000)
                return 0
            finally:
                _bass.BASS_StreamFree(handle)
        finally:
            _bass.BASS_Free()
    except Exception:
        return 0


class SugConcatWorker(QObject):
    """在后台线程中拼接多个 SUG 项目。

    信号:
        progress(stage, current, total): 进度更新
        finished(project, entries_count): 拼接成功
        error(message): 拼接失败
    """

    progress = pyqtSignal(str, int, int)  # (stage_text, current, total)
    finished = pyqtSignal(object, int)    # (Project, entries_count)
    error = pyqtSignal(str)               # (error_message)

    def __init__(self, entries: list[SugEntry], output_name: str, uniform_offset: int):
        super().__init__()
        self._entries = entries
        self._output_name = output_name
        self._uniform_offset = uniform_offset

    def run(self) -> None:
        try:
            self._concat()
        except Exception as e:
            self.error.emit(str(e))

    def _concat(self) -> None:
        total = len(self._entries)
        all_sentences = []
        accumulated_time_ms = 0

        for idx, entry in enumerate(self._entries):
            file_path = entry.file_path
            self.progress.emit(
                f"读取 {Path(file_path).name} ({idx + 1}/{total})",
                idx + 1,
                total,
            )

            if not file_path or not Path(file_path).exists():
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            version = data.get("version", "1.0")
            if version != SugMigrator.CURRENT_VERSION:
                try:
                    data = SugMigrator.migrate(data, version)
                except Exception:
                    continue

            sentences_data = data.get("sentences", [])
            for sentence_data in sentences_data:
                sentence = SugProjectParser._dict_to_sentence(sentence_data)
                all_sentences.append(sentence)

            # 先撤销该 SUG 自身的偏移，还原到绝对音频时间轴
            # （global_offset_ms 会被引擎在显示时加上，所以存储时反扣）
            per_offset = entry.offset_ms
            newly_added = all_sentences[-len(sentences_data):]
            if per_offset != 0:
                for sentence in newly_added:
                    for char in sentence.characters:
                        char.timestamps = [ts - per_offset for ts in char.timestamps]
                        if char.sentence_end_ts is not None:
                            char.sentence_end_ts = char.sentence_end_ts - per_offset

            # 再应用累计时间偏移（从前序 SUG 的时长 + 间隔累加）
            if accumulated_time_ms > 0:
                for sentence in newly_added:
                    for char in sentence.characters:
                        char.timestamps = [ts + accumulated_time_ms for ts in char.timestamps]
                        if char.sentence_end_ts is not None:
                            char.sentence_end_ts = char.sentence_end_ts + accumulated_time_ms

            accumulated_time_ms += entry.duration_ms + entry.gap_ms

        if not all_sentences:
            self.error.emit("未能从任何 SUG 文件中读取到有效歌词数据。")
            return

        self.progress.emit("创建项目...", total, total)

        from strange_uta_game.backend.application import ProjectService

        project = ProjectService().create_project()
        project.metadata.title = self._output_name
        project.sentences.clear()
        for s in all_sentences:
            project.sentences.append(s)

        if self._uniform_offset != 0:
            project.global_offset_ms = self._uniform_offset

        self.finished.emit(project, total)
