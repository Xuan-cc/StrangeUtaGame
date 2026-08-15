"""SUG 拼接功能回归测试。

覆盖 SugEntry 数据类、SUG 元信息读取、音频时长探测、
SugConcatWorker 拼接逻辑和 SugConcatDialog 对话框。
"""

import json
import struct
from pathlib import Path

import pytest

# ---- offscreen QApplication 夹具 ----



@pytest.fixture(scope="module")
def qapp():
    """提供模块级 QApplication（offscreen，避免弹窗）。"""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


# ---- SugEntry 文本 ----

from strange_uta_game.frontend.editor.timing.sug_concat_dialog import (
    SugEntry,
    SugConcatDialog,
    _read_sug_entry,
)

from strange_uta_game.frontend.editor.timing.sug_concat_worker import (
    SugConcatWorker,
    _probe_audio_duration_multi,
    _read_mp3_duration,
)


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _make_sug_project(
    tmp_path: Path,
    filename: str,
    *,
    title: str = "Test",
    sentences_data: list | None = None,
    global_offset_ms: int | None = None,
    audio_duration_ms: int = 0,
    media_path: str = "",
) -> Path:
    """在 tmp_path 下创建一个最小 .sug 文件并返回路径。

    sentences_data 格式: [{"text": "あ", "timestamps": [0]}, ...]
    """
    from strange_uta_game.backend.domain import Project, Singer, Sentence

    project = Project()
    # 添加一个默认歌手
    singer = Singer(name="Default", is_default=True)
    # 清空 Project 自带的默认歌手后加入
    project.singers.clear()
    project.singers.append(singer)

    if sentences_data is None:
        sentences_data = [{"text": "あ", "timestamps": [0]}]

    for sd in sentences_data:
        s = Sentence.from_text(sd["text"], singer.id)
        for i, ts in enumerate(sd.get("timestamps", [0])):
            if i < len(s.characters):
                s.characters[i].add_timestamp(ts)
        if sd.get("sentence_end_ts"):
            if s.characters:
                s.characters[-1].set_sentence_end_ts(sd["sentence_end_ts"])
        project.add_sentence(s)

    if global_offset_ms is not None:
        project.global_offset_ms = global_offset_ms
    if audio_duration_ms:
        project.audio_duration_ms = audio_duration_ms

    # 修改 metadata title
    project.metadata.title = title

    # 写入 JSON（含 media_path）
    from strange_uta_game.backend.infrastructure.persistence.sug_io import (
        SugProjectParser,
    )

    file_path = tmp_path / filename
    SugProjectParser.save(project, str(file_path), media_path=media_path or None)
    return file_path


def _make_tiny_wav(tmp_path: Path, filename: str, duration_sec: float = 1.0) -> Path:
    """生成一个最小 WAV 文件（单声道 44100Hz 16bit）。"""
    import soundfile as sf
    import numpy as np

    sr = 44100
    samples = int(sr * duration_sec)
    data = np.zeros(samples, dtype=np.float32)
    wav_path = tmp_path / filename
    sf.write(str(wav_path), data, sr, subtype="PCM_16")
    return wav_path


# ═══════════════════════════════════════════════════════════════
# SugEntry 组
# ═══════════════════════════════════════════════════════════════


class TestSugEntry:
    def test_name_from_path(self):
        e = SugEntry(file_path=r"C:\foo\bar\mysong.sug")
        assert e.name == "mysong"

    def test_name_empty(self):
        e = SugEntry(file_path="")
        assert e.name == ""

    def test_default_values(self):
        e = SugEntry()
        assert e.offset_ms == 0
        assert e.duration_ms == 0
        assert e.gap_ms == 300
        assert e.title == ""
        assert e.media_path == ""


# ═══════════════════════════════════════════════════════════════
# _read_sug_entry 组
# ═══════════════════════════════════════════════════════════════


class TestReadSugEntry:
    def test_read_basic(self, tmp_path):
        path = _make_sug_project(tmp_path, "basic.sug", title="My Song")
        entry = _read_sug_entry(str(path))
        assert entry.title == "My Song"
        assert entry.file_path == str(path)
        assert entry.offset_ms == 0
        assert entry.duration_ms == 0

    def test_read_with_offset(self, tmp_path):
        path = _make_sug_project(tmp_path, "off.sug", global_offset_ms=-100)
        entry = _read_sug_entry(str(path))
        assert entry.offset_ms == -100

    def test_read_with_audio_duration(self, tmp_path):
        path = _make_sug_project(tmp_path, "dur.sug", audio_duration_ms=120000)
        entry = _read_sug_entry(str(path))
        assert entry.duration_ms == 120000

    def test_read_with_media_path(self, tmp_path):
        mp = r"C:\some\audio.mp3"
        path = _make_sug_project(tmp_path, "mp.sug", media_path=mp)
        entry = _read_sug_entry(str(path))
        assert entry.media_path == mp

    def test_read_nonexistent_file(self, tmp_path):
        entry = _read_sug_entry(str(tmp_path / "nonexistent.sug"))
        assert entry.file_path.endswith("nonexistent.sug")
        assert entry.title == ""

    def test_read_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.sug"
        bad.write_text("not valid json{{{", encoding="utf-8")
        entry = _read_sug_entry(str(bad))
        assert entry.file_path == str(bad)


# ═══════════════════════════════════════════════════════════════
# 音频时长探测 组
# ═══════════════════════════════════════════════════════════════


class TestAudioDurationProbe:
    def test_probe_wav(self, tmp_path):
        wav = _make_tiny_wav(tmp_path, "test.wav", duration_sec=0.5)
        dur = _probe_audio_duration_multi(str(wav))
        assert abs(dur - 500) < 50, f"Expected ~500ms, got {dur}"

    def test_probe_nonexistent(self):
        dur = _probe_audio_duration_multi(r"Z:\nonexistent\file.wav")
        assert dur == 0

    def test_probe_empty_path(self):
        assert _probe_audio_duration_multi("") == 0

    def test_read_mp3_duration_empty(self):
        """空文件应返回 0。"""
        assert _read_mp3_duration("") == 0


# ═══════════════════════════════════════════════════════════════
# SugConcatWorker 组
# ═══════════════════════════════════════════════════════════════


class TestSugConcatWorker:
    """通过直接调用 _concat 测试拼接逻辑（主线程，无需 QThread）。"""

    def _collect_result(self, entries, output_name="拼接项目", uniform_offset=0):
        """运行 worker 的 _concat 并捕获 finished/error 结果。"""
        worker = SugConcatWorker(entries, output_name, uniform_offset)
        result = {"project": None, "count": 0, "error": None}

        def on_finished(proj, cnt):
            result["project"] = proj
            result["count"] = cnt

        def on_error(msg):
            result["error"] = msg

        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        worker._concat()
        return result

    def test_basic_concat_two_sugs(self, tmp_path, qapp):
        """两个 SUG 各一行，验证拼接后行数正确。"""
        p1 = _make_sug_project(
            tmp_path, "s1.sug",
            title="SongA",
            sentences_data=[{"text": "あいう", "timestamps": [0, 500, 1000]}],
        )
        p2 = _make_sug_project(
            tmp_path, "s2.sug",
            title="SongB",
            sentences_data=[{"text": "かきく", "timestamps": [100, 600, 1100]}],
        )
        e1 = SugEntry(file_path=str(p1), duration_ms=5000, gap_ms=300)
        e2 = SugEntry(file_path=str(p2), duration_ms=4000, gap_ms=0)

        res = self._collect_result([e1, e2], output_name="AB")
        assert res["error"] is None
        proj = res["project"]
        assert proj is not None
        assert res["count"] == 2
        assert len(proj.sentences) == 2
        assert proj.sentences[0].text == "あいう"
        assert proj.sentences[1].text == "かきく"

    def test_timestamp_shifting(self, tmp_path, qapp):
        """验证第二首的时间戳被偏移了 duration+gap。"""
        p1 = _make_sug_project(
            tmp_path, "shift1.sug",
            sentences_data=[{"text": "A", "timestamps": [0]}],
        )
        p2 = _make_sug_project(
            tmp_path, "shift2.sug",
            sentences_data=[{"text": "B", "timestamps": [100]}],
        )
        e1 = SugEntry(file_path=str(p1), duration_ms=10000, gap_ms=500)
        e2 = SugEntry(file_path=str(p2), duration_ms=0, gap_ms=0)

        res = self._collect_result([e1, e2])
        proj = res["project"]
        # A 的 timestamps 在原位: 0
        assert proj.sentences[0].characters[0].timestamps[0] == 0
        # B 的 timestamps 应偏移: 100 + 10000 + 500 = 10600
        assert proj.sentences[1].characters[0].timestamps[0] == 100 + 10000 + 500

    def test_sentence_end_ts_shifting(self, tmp_path, qapp):
        """验证 sentence_end_ts 也被正确偏移。"""
        p1 = _make_sug_project(
            tmp_path, "se1.sug",
            sentences_data=[{"text": "X", "timestamps": [0], "sentence_end_ts": 3000}],
        )
        p2 = _make_sug_project(
            tmp_path, "se2.sug",
            sentences_data=[{"text": "Y", "timestamps": [50], "sentence_end_ts": 2000}],
        )
        e1 = SugEntry(file_path=str(p1), duration_ms=8000, gap_ms=200)
        e2 = SugEntry(file_path=str(p2), duration_ms=0, gap_ms=0)

        res = self._collect_result([e1, e2])
        proj = res["project"]
        # X sentence_end_ts: 3000 (no shift, first entry)
        assert proj.sentences[0].characters[0].sentence_end_ts == 3000
        # Y sentence_end_ts: 2000 + 8000 + 200 = 10200
        assert proj.sentences[1].characters[0].sentence_end_ts == 2000 + 8000 + 200

    def test_uniform_offset(self, tmp_path, qapp):
        """验证统一偏移被应用到 project.global_offset_ms。"""
        p1 = _make_sug_project(tmp_path, "uo1.sug", global_offset_ms=-50)
        e1 = SugEntry(file_path=str(p1), duration_ms=1000, gap_ms=0)

        res = self._collect_result([e1], uniform_offset=-200)
        proj = res["project"]
        assert proj.global_offset_ms == -200

    def test_uniform_offset_zero(self, tmp_path, qapp):
        """最终偏移为 0 时 global_offset_ms 为 None（不写入）。"""
        p1 = _make_sug_project(tmp_path, "uo_zero.sug", global_offset_ms=-50)
        e1 = SugEntry(file_path=str(p1), duration_ms=1000, gap_ms=0)

        res = self._collect_result([e1], uniform_offset=0)
        proj = res["project"]
        assert proj.global_offset_ms is None

    def test_per_entry_offset_applied(self, tmp_path, qapp):
        """每首 SUG 自身的偏移会从原始时间戳中撤销（反扣），还原到绝对时间轴。"""
        p1 = _make_sug_project(
            tmp_path, "po1.sug",
            global_offset_ms=-50,
            sentences_data=[{"text": "A", "timestamps": [1000]}],
        )
        e1 = _read_sug_entry(str(p1))
        e1.duration_ms = 10000
        e1.gap_ms = 0

        res = self._collect_result([e1], uniform_offset=0)
        proj = res["project"]
        # 原始 1000 - 自身偏移(-50) = 1000 + 50 = 1050
        assert proj.sentences[0].characters[0].timestamps[0] == 1050
        assert proj.global_offset_ms is None

    def test_no_entries(self, tmp_path, qapp):
        """空条目应触发 error 信号。"""
        res = self._collect_result([])
        assert res["error"] is not None
        assert res["project"] is None

    def test_all_files_missing(self, tmp_path, qapp):
        """所有文件不存在应触发 error。"""
        e1 = SugEntry(file_path=str(tmp_path / "ghost1.sug"))
        e2 = SugEntry(file_path=str(tmp_path / "ghost2.sug"))
        res = self._collect_result([e1, e2])
        assert res["error"] is not None

    def test_output_name(self, tmp_path, qapp):
        """验证输出项目名称设置。"""
        p1 = _make_sug_project(tmp_path, "name.sug")
        e1 = SugEntry(file_path=str(p1), duration_ms=1000, gap_ms=0)

        res = self._collect_result([e1], output_name="自定义名称")
        assert res["project"].metadata.title == "自定义名称"

    def test_multi_sentence_per_sug(self, tmp_path, qapp):
        """每个 SUG 内多行歌词。"""
        p1 = _make_sug_project(
            tmp_path, "multi1.sug",
            sentences_data=[
                {"text": "A1", "timestamps": [0]},
                {"text": "A2", "timestamps": [500]},
                {"text": "A3", "timestamps": [1000]},
            ],
        )
        p2 = _make_sug_project(
            tmp_path, "multi2.sug",
            sentences_data=[
                {"text": "B1", "timestamps": [0]},
                {"text": "B2", "timestamps": [300]},
            ],
        )
        e1 = SugEntry(file_path=str(p1), duration_ms=20000, gap_ms=100)
        e2 = SugEntry(file_path=str(p2), duration_ms=0, gap_ms=0)

        res = self._collect_result([e1, e2])
        assert len(res["project"].sentences) == 5
        # B1 timestamps: 0 + 20000 + 100 = 20100
        assert res["project"].sentences[3].characters[0].timestamps[0] == 20100
        # B2 timestamps: 300 + 20000 + 100 = 20400
        assert res["project"].sentences[4].characters[0].timestamps[0] == 20400


# ═══════════════════════════════════════════════════════════════
# SugConcatDialog 组
# ═══════════════════════════════════════════════════════════════


class TestSugConcatDialog:
    def test_dialog_creation(self, qapp):
        """对话框可以正常创建和销毁。"""
        dlg = SugConcatDialog()
        assert dlg.windowTitle() == "多项目拼接"
        assert not dlg.was_apply_clicked()
        dlg.close()

    def test_default_output_name(self, qapp):
        """未设置时默认名称。"""
        dlg = SugConcatDialog()
        assert dlg.get_output_name() == "拼接项目"
        dlg.close()

    def test_get_entries_empty(self, qapp):
        """未添加任何 SUG 时条目列表为空。"""
        dlg = SugConcatDialog()
        assert dlg.get_entries() == []
        dlg.close()

    def test_uniform_offset_default(self, qapp, monkeypatch):
        """默认最终偏移采用用户全局偏移配置（export.offset_ms）。"""
        class _FakeSettings:
            def get(self, key, default=None):
                if key == "sug_concat.default_gap_ms":
                    return 300
                if key == "sug_concat.final_offset_ms":
                    return None  # 无上次值 → 回退到 export.offset_ms
                if key == "export.offset_ms":
                    return -150
                return default
            def save(self): pass

        import strange_uta_game.frontend.settings.app_settings as _asmod
        monkeypatch.setattr(_asmod, "AppSettings", lambda: _FakeSettings())

        dlg = SugConcatDialog()
        assert dlg.get_uniform_offset() == -150
        dlg.close()
