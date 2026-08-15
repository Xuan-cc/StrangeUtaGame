# -*- coding: utf-8 -*-
"""standalone 分离执行器：假子进程端到端测试（stdout 协议、取消、兜底轨名）。"""

import io
import json
import os
from pathlib import Path

import pytest

from strange_uta_game.backend.application.ai_timing import separation as sep_mod
from strange_uta_game.backend.application.ai_timing.separation import (
    StandaloneVocalSeparator,
    _SCRIPT,
)


class _FakeProc:
    def __init__(self, lines, returncode=0):
        self.stdout = io.StringIO("".join(l + "\n" for l in lines))
        self.killed = False
        self._returncode = returncode

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self._returncode


def _separator(
    tmp_path, monkeypatch, lines, *, proxy="", returncode=0, embedded=False
):
    monkeypatch.setattr(StandaloneVocalSeparator, "available", lambda self: True)
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    vocal = tmp_path / "song_人声.wav"
    vocal.write_bytes(b"v")
    ffmpeg = tmp_path / "tools" / "ffmpeg.exe"
    ffmpeg.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.write_bytes(b"")
    monkeypatch.setattr(sep_mod, "resolve_ffmpeg_exe", lambda: str(ffmpeg))
    proc = _FakeProc(lines, returncode=returncode)
    captured = {}

    def _popen(*args, **kwargs):
        captured.update(kwargs=kwargs)
        return proc

    monkeypatch.setattr(sep_mod.subprocess, "Popen", _popen)
    sep = StandaloneVocalSeparator(
        str(python), tmp_path / "models", proxy=proxy, embedded=embedded
    )
    return sep, proc, vocal, captured


class TestStandaloneSeparator:
    def test_success_flow_and_normalized_output(self, tmp_path, monkeypatch):
        vocal = tmp_path / "song_人声.wav"
        lines = [
            "stage:load:加载分离模型",
            "stage:separate:分离处理中",
            "done:" + str(vocal),
        ]
        sep, proc, _, _ = _separator(tmp_path, monkeypatch, lines)
        events = []
        out = sep.separate(
            tmp_path / "song.flac", lambda *a: events.append(a), lambda: False
        )
        assert out == vocal
        assert not proc.killed
        assert events[-1][1] == 100

    def test_cancel_kills_and_waits_process(self, tmp_path, monkeypatch):
        lines = ["stage:load:加载分离模型", "stage:separate:分离处理中"]
        sep, proc, _, _ = _separator(tmp_path, monkeypatch, lines)
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 1

        with pytest.raises(RuntimeError, match="已取消"):
            sep.separate(tmp_path / "song.flac", lambda *a: None, cancel)
        assert proc.killed

    def test_no_done_line_raises(self, tmp_path, monkeypatch):
        sep, _, _, _ = _separator(
            tmp_path, monkeypatch, ["stage:load:加载分离模型"]
        )
        with pytest.raises(RuntimeError, match="人声分离失败"):
            sep.separate(tmp_path / "song.flac", lambda *a: None, lambda: False)

    def test_missing_ffmpeg_fails_fast_without_spawn(self, tmp_path, monkeypatch):
        """audio-separator 构造即探测 ffmpeg：缺失时启动前给出可操作
        中文错误（GitHub issue：只报「返回码 1」无法定位）。"""
        monkeypatch.setattr(StandaloneVocalSeparator, "available", lambda self: True)
        python = tmp_path / "python.exe"
        python.write_bytes(b"")
        monkeypatch.setattr(sep_mod, "resolve_ffmpeg_exe", lambda: "")
        spawned = []
        monkeypatch.setattr(
            sep_mod.subprocess,
            "Popen",
            lambda *a, **k: spawned.append(a) or _FakeProc([]),
        )
        sep = StandaloneVocalSeparator(str(python), tmp_path / "models")
        with pytest.raises(RuntimeError, match="FFmpeg"):
            sep.separate(tmp_path / "song.flac", lambda *a: None, lambda: False)
        assert spawned == []  # 未启动子进程即失败

    def test_child_env_injects_ffmpeg_dir_and_proxy(self, tmp_path, monkeypatch):
        """配置的 ffmpeg 路径与代理必须注入子进程环境：前者供
        audio-separator 探测，后者供模型首次下载（GitHub）使用。"""
        lines = ["done:" + str(tmp_path / "song_人声.wav")]
        sep, _, _, captured = _separator(
            tmp_path, monkeypatch, lines, proxy="http://127.0.0.1:7890"
        )
        sep.separate(tmp_path / "song.flac", lambda *a: None, lambda: False)
        env = captured["kwargs"]["env"]
        assert env["PATH"].startswith(str(tmp_path / "tools") + os.pathsep)
        assert env["HTTP_PROXY"] == "http://127.0.0.1:7890"
        assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"

    def test_failure_message_carries_child_tail_and_hint(self, tmp_path, monkeypatch):
        """失败时异常消息带上子进程输出尾部与常见原因提示，外部
        用户反馈不再只剩返回码。"""
        lines = [
            "stage:load:加载分离模型",
            "FFmpeg is not installed. Please install FFmpeg to use this package.",
            "Traceback (most recent call last):",
            "    raise",
            "FileNotFoundError: [WinError 2] 系统找不到指定的文件。",
        ]
        sep, _, _, _ = _separator(tmp_path, monkeypatch, lines, returncode=1)
        with pytest.raises(RuntimeError) as excinfo:
            sep.separate(tmp_path / "song.flac", lambda *a: None, lambda: False)
        message = str(excinfo.value)
        assert "返回码 1" in message
        assert "WinError 2" in message
        assert "FFmpeg" in message

    def test_failure_hint_for_model_download_error(self, tmp_path, monkeypatch):
        lines = [
            "Downloading file from https://github.com/TRvlvr/model_repo/...",
            "requests.exceptions.ConnectionError: Max retries exceeded",
        ]
        sep, _, _, _ = _separator(tmp_path, monkeypatch, lines, returncode=1)
        with pytest.raises(RuntimeError, match="代理"):
            sep.separate(tmp_path / "song.flac", lambda *a: None, lambda: False)

    def test_missing_ffmpeg_message_differs_by_mode(self, tmp_path, monkeypatch):
        """embedded 模式 SUG 自身的 ffmpeg 设置入口隐藏（EMBEDDING §5），
        失败提示必须引导到工作台；standalone 引导到 SUG 设置。"""
        monkeypatch.setattr(StandaloneVocalSeparator, "available", lambda self: True)
        python = tmp_path / "python.exe"
        python.write_bytes(b"")
        monkeypatch.setattr(sep_mod, "resolve_ffmpeg_exe", lambda: "")
        monkeypatch.setattr(
            sep_mod.subprocess, "Popen", lambda *a, **k: _FakeProc([])
        )

        def _make(embedded):
            return StandaloneVocalSeparator(
                str(python), tmp_path / "models", embedded=embedded
            )

        with pytest.raises(RuntimeError) as standalone_err:
            _make(embedded=False).separate(
                tmp_path / "song.flac", lambda *a: None, lambda: False
            )
        assert "设置 → 关于/语言" in str(standalone_err.value)

        with pytest.raises(RuntimeError) as embedded_err:
            _make(embedded=True).separate(
                tmp_path / "song.flac", lambda *a: None, lambda: False
            )
        assert "工作台" in str(embedded_err.value)
        assert "设置 → 关于/语言" not in str(embedded_err.value)

    def test_embedded_failure_hint_points_to_workbench(self, tmp_path, monkeypatch):
        lines = [
            "stage:load:加载分离模型",
            "FFmpeg is not installed. Please install FFmpeg to use this package.",
            "Traceback (most recent call last):",
            "FileNotFoundError: [WinError 2] 系统找不到指定的文件。",
        ]
        sep, _, _, _ = _separator(
            tmp_path, monkeypatch, lines, returncode=1, embedded=True
        )
        with pytest.raises(RuntimeError) as excinfo:
            sep.separate(tmp_path / "song.flac", lambda *a: None, lambda: False)
        message = str(excinfo.value)
        assert "工作台" in message
        assert "设置 → 关于/语言" not in message

    def test_corrupt_model_deleted_before_spawn(self, tmp_path, monkeypatch):
        """分离启动前的模型体检：残缺模型自动删除并提示重下
        （audio-separator 非原子下载的中断残留，实测打包版复现）。"""
        vocal = tmp_path / "song_人声.wav"
        lines = ["done:" + str(vocal)]
        sep, _, _, _ = _separator(tmp_path, monkeypatch, lines)
        models = tmp_path / "models"
        model = models / sep_mod.SEPARATION_MODEL
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"truncated-payload")
        (models / "mdx_model_data.json").write_text(
            json.dumps({"00000000000000000000000000000000": {}}),
            encoding="utf-8",
        )
        messages = []
        sep.separate(
            tmp_path / "song.flac",
            lambda s, p, m: messages.append(m),
            lambda: False,
        )
        assert any("重新下载" in m for m in messages)
        assert not model.exists()


class TestSeparationModelPreflight:
    """ensure_separation_model：与 audio-separator 子进程同口径的体检自愈。"""

    @staticmethod
    def _write_table(models: Path, hashes):
        models.mkdir(parents=True, exist_ok=True)
        (models / "mdx_model_data.json").write_text(
            json.dumps({h: {} for h in hashes}), encoding="utf-8"
        )

    def test_healthy_model_passes_silently(self, tmp_path):
        models = tmp_path / "models"
        models.mkdir(parents=True)
        model = models / sep_mod.SEPARATION_MODEL
        model.write_bytes(b"model-bytes")
        good = sep_mod._uvr_partial_md5(model)
        self._write_table(models, [good])
        assert sep_mod.ensure_separation_model(models) == ""
        assert model.is_file()

    def test_partial_hash_matches_audio_separator_algorithm(self, tmp_path):
        """末 10MB 取样口径：小于窗口时等于全文件 MD5。"""
        import hashlib

        p = tmp_path / "m.bin"
        p.write_bytes(b"abc")
        assert (
            sep_mod._uvr_partial_md5(p)
            == hashlib.md5(b"abc").hexdigest()
        )

    def test_corrupt_model_deleted_with_note(self, tmp_path):
        models = tmp_path / "models"
        models.mkdir(parents=True)
        model = models / sep_mod.SEPARATION_MODEL
        model.write_bytes(b"truncated")
        self._write_table(models, ["ffffffffffffffffffffffffffffffff"])
        note = sep_mod.ensure_separation_model(models)
        assert "不完整" in note and "重新下载" in note
        assert not model.exists()

    def test_broken_data_table_reset_model_kept(self, tmp_path):
        """数据表损坏 → 只重置表；无有效表可查时不误删模型。"""
        models = tmp_path / "models"
        models.mkdir(parents=True)
        model = models / sep_mod.SEPARATION_MODEL
        model.write_bytes(b"maybe-good")
        (models / "mdx_model_data.json").write_text(
            "{broken-json", encoding="utf-8"
        )
        note = sep_mod.ensure_separation_model(models)
        assert "数据表" in note
        assert not (models / "mdx_model_data.json").exists()
        assert model.is_file()

    def test_missing_model_or_root_is_noop(self, tmp_path):
        assert sep_mod.ensure_separation_model(tmp_path / "models") == ""
        assert sep_mod.ensure_separation_model(None) == ""

    def test_vocal_track_fallback_in_script(self):
        """UVR 轨名兜底：脚本包含排除伴奏轨的回退逻辑。"""
        assert "nstrumental" in _SCRIPT

    def test_identity_shape(self):
        sep = StandaloneVocalSeparator("", None)
        ident = sep.identity()
        assert ident["model"].endswith(".onnx")
        assert ident["stem"] == "人声"
        assert ident["params"] == {}

    def test_callable_runtime_python_resolved_lazily(self, tmp_path, monkeypatch):
        """解释器路径惰性读取：安装完成后路径才写入设置，同一分离器
        实例的 available() 必须立即反映新值（分离环境行不再卡在未安装）。"""
        state = {"python": ""}
        probed = []

        def _fake_run(cmd, **kwargs):
            probed.append(cmd[0])
            rc = 0 if cmd[0] == state["python"] else 1

            class _C:
                returncode = rc

            return _C()

        monkeypatch.setattr(sep_mod.subprocess, "run", _fake_run)
        sep = StandaloneVocalSeparator(lambda: state["python"], None)
        assert sep.available() is False  # 未安装：路径为空
        assert probed == []  # 空路径不 spawn 子进程

        exe = tmp_path / "runtime" / "python.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        state["python"] = str(exe)
        assert sep.available() is True
        assert probed == [str(exe)]

        # 字符串形式（旧用法）仍然可用
        sep2 = StandaloneVocalSeparator(str(exe), None)
        assert sep2.available() is True


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
