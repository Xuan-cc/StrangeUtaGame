# -*- coding: utf-8 -*-
"""standalone 分离执行器：假子进程端到端测试（stdout 协议、取消、兜底轨名）。"""

import io
from pathlib import Path

import pytest

from strange_uta_game.backend.application.ai_timing import separation as sep_mod
from strange_uta_game.backend.application.ai_timing.separation import (
    StandaloneVocalSeparator,
    _SCRIPT,
)


class _FakeProc:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(l + "\n" for l in lines))
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def _separator(tmp_path, monkeypatch, lines):
    monkeypatch.setattr(StandaloneVocalSeparator, "available", lambda self: True)
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    vocal = tmp_path / "song_人声.wav"
    vocal.write_bytes(b"v")
    proc = _FakeProc(lines)
    monkeypatch.setattr(sep_mod.subprocess, "Popen", lambda *a, **k: proc)
    sep = StandaloneVocalSeparator(str(python), tmp_path / "models")
    return sep, proc, vocal


class TestStandaloneSeparator:
    def test_success_flow_and_normalized_output(self, tmp_path, monkeypatch):
        vocal = tmp_path / "song_人声.wav"
        lines = [
            "stage:load:加载分离模型",
            "stage:separate:分离处理中",
            "done:" + str(vocal),
        ]
        sep, proc, _ = _separator(tmp_path, monkeypatch, lines)
        events = []
        out = sep.separate(
            tmp_path / "song.flac", lambda *a: events.append(a), lambda: False
        )
        assert out == vocal
        assert not proc.killed
        assert events[-1][1] == 100

    def test_cancel_kills_and_waits_process(self, tmp_path, monkeypatch):
        lines = ["stage:load:加载分离模型", "stage:separate:分离处理中"]
        sep, proc, _ = _separator(tmp_path, monkeypatch, lines)
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 1

        with pytest.raises(RuntimeError, match="已取消"):
            sep.separate(tmp_path / "song.flac", lambda *a: None, cancel)
        assert proc.killed

    def test_no_done_line_raises(self, tmp_path, monkeypatch):
        sep, _, _ = _separator(tmp_path, monkeypatch, ["stage:load:加载分离模型"])
        with pytest.raises(RuntimeError, match="人声分离失败"):
            sep.separate(tmp_path / "song.flac", lambda *a: None, lambda: False)

    def test_vocal_track_fallback_in_script(self):
        """UVR 轨名兜底：脚本包含排除伴奏轨的回退逻辑。"""
        assert "nstrumental" in _SCRIPT

    def test_identity_shape(self):
        sep = StandaloneVocalSeparator("", None)
        ident = sep.identity()
        assert ident["model"].endswith(".onnx")
        assert ident["stem"] == "人声"
        assert ident["params"] == {}


class TestHostFirstSeparation:
    """embedded 分离编排：宿主优先，宿主未配置时回落 AI Runtime 内置分离。"""

    def _fake_host(self, available):
        class _H:
            def __init__(self):
                self.calls = []

            def separation_status(self):
                return {"available": available, "model": "m", "message": ""}

            def effective_identity(self):
                return {"model": "host-model", "stem": "人声", "params": {}}

            def separate_vocal(self, source, progress, cancel):
                self.calls.append(("host", str(source)))
                return Path("C:/host_vocal.wav")

        return _H()

    @staticmethod
    def _fake_standalone():
        class _S:
            name = "builtin"

            def __init__(self):
                self.calls = []

            def identity(self):
                return {"model": "builtin.onnx", "stem": "人声", "params": {}}

            def available(self):
                return True

            def separate(self, source, progress, cancel):
                self.calls.append(("builtin", str(source)))
                progress("vocal", 12, "内置分离")
                return Path("C:/builtin_vocal.wav")

        return _S()

    def test_host_available_uses_host(self):
        host, sa = self._fake_host(True), self._fake_standalone()
        executor, identity, prober, follows = sep_mod.host_first_separation(
            host, sa
        )
        assert follows is True
        assert prober() is True
        assert identity() == {"model": "host-model", "stem": "人声", "params": {}}
        out = executor(Path("s.flac"), lambda *a: None, lambda: False)
        assert out == Path("C:/host_vocal.wav")
        assert host.calls and not sa.calls

    def test_host_unavailable_falls_back_to_builtin(self):
        host, sa = self._fake_host(False), self._fake_standalone()
        executor, identity, prober, follows = sep_mod.host_first_separation(
            host, sa
        )
        assert follows is False
        msgs = []
        out = executor(
            Path("s.flac"), lambda s, p, m: msgs.append(m), lambda: False
        )
        assert out == Path("C:/builtin_vocal.wav")
        assert any("内置分离" in m for m in msgs)
        assert identity()["model"] == "builtin.onnx"
        assert prober() is True  # 内置分离可用兜底

    def test_neither_available_reports_false(self):
        host, sa = self._fake_host(False), self._fake_standalone()
        sa.available = lambda: False
        _, _, prober, follows = sep_mod.host_first_separation(host, sa)
        assert follows is False and prober() is False

    def test_host_status_exception_treated_unavailable(self):
        class _BadHost:
            def separation_status(self):
                raise RuntimeError("boom")

            def effective_identity(self):
                return {"model": "host-model", "stem": "人声", "params": {}}

            def separate_vocal(self, *a):
                raise AssertionError("不应走到宿主分离")

        sa = self._fake_standalone()
        executor, _, prober, follows = sep_mod.host_first_separation(
            _BadHost(), sa
        )
        assert follows is False and prober() is True
        out = executor(Path("s.flac"), lambda *a: None, lambda: False)
        assert out == Path("C:/builtin_vocal.wav")
