"""AI 打轴阶段 F：编排服务测试（无 Qt、无真实模型）。

worker / 分离执行器全部进程内注入；验证执行链路、缓存命中跳过推理、
前置阻断（缺音频/缺口/缺人声/多候选）、漂移检测与取消传播。
"""

from pathlib import Path

import pytest

from strange_uta_game.backend.application.ai_timing.alignment import (
    AlignmentRequest,
    AlignmentResult,
    EmissionSpan,
    build_alignment_request,
)
from strange_uta_game.backend.application.ai_timing.models import (
    ModelManifest,
    ModelRegistry,
)
from strange_uta_game.backend.application.ai_timing.resolver import (
    PronunciationResolver,
)
from strange_uta_game.backend.application.ai_timing.runtime import AiRuntimeManager
from strange_uta_game.backend.application.ai_timing.service import (
    AiTimingError,
    AiTimingService,
)
from strange_uta_game.backend.application.ai_timing.settings import AiTimingSettings
from strange_uta_game.backend.application.ai_timing.vocals import (
    AiCache,
    VocalPreparationService,
)
from strange_uta_game.backend.domain import (
    Character,
    Project,
    Ruby,
    RubyPart,
    Sentence,
)
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    DummyAnalyzer,
)


class TestPackageExports:
    def test_service_reachable_from_package_root(self):
        """包级导出回归：timing_interface._build_ai_timing_service 从包根导入
        全部名字——2026-08 曾漏导出 AiTimingService 导致点击按钮即
        ImportError（测试都走子模块直连路径而漏测）。"""
        from strange_uta_game.backend.application import ai_timing

        for name in (
            "AiCache",
            "AiRuntimeManager",
            "AiTimingService",
            "ModelDownloadService",
            "ModelRegistry",
            "PronunciationResolver",
            "VocalPreparationService",
            "load_ai_timing_settings",
            "resolve_model_root",
        ):
            assert hasattr(ai_timing, name), name


class _FakeWorker:
    """进程内假 worker：按 token 均分区间，可注入失败。"""

    def __init__(self, fail=False, spans_override=None):
        self.fail = fail
        self.spans_override = spans_override
        self.calls = []

    def run(self, request, audio_path, model_spec, on_progress=None, timeout_s=None):
        self.calls.append((request, audio_path, model_spec))
        if on_progress:
            on_progress("load", 50, "加载模型")
        if self.fail:
            raise RuntimeError("engine exploded")
        if self.spans_override is not None:
            spans = self.spans_override
        else:
            n = len(request.tokens)
            step = 1000 // max(1, n)
            spans = [
                EmissionSpan(
                    token_index=t.index,
                    start_ms=i * step,
                    end_ms=(i + 1) * step,
                )
                for i, t in enumerate(request.tokens)
            ]
        return AlignmentResult(
            annotation_digest=request.annotation_digest,
            model_id=model_spec.get("model_id", "fake"),
            spans=spans,
        )


def _project():
    """两行全假名工程（假名自读 → 无缺口）。"""
    project = Project()
    s1 = Sentence(
        singer_id="s1",
        characters=[
            Character(char="あ", check_count=1, ruby=None, singer_id="s1"),
            Character(char="か", check_count=1, ruby=None, singer_id="s1"),
        ],
    )
    s2 = Sentence(
        singer_id="s1",
        characters=[Character(char="さ", check_count=1, ruby=None, singer_id="s1")],
    )
    project.sentences = [s1, s2]
    return project


def _make_service(
    tmp_path,
    *,
    worker=None,
    separation_executor=None,
    vocal_file=None,
    session_finder=None,
    separation_identity=None,
    settings=None,
):
    audio = tmp_path / "song.flac"
    if not audio.exists():
        audio.write_bytes(b"audio-bytes")
    vocal = vocal_file or (tmp_path / "song_人声.wav")
    if not vocal.exists():
        vocal.write_bytes(b"vocal-bytes")
    cache = AiCache(tmp_path / "ai_cache")
    vocal_service = VocalPreparationService(
        cache, session_vocal_finder=session_finder
    )
    settings = settings or AiTimingSettings(provider="wav2vec2")
    registry = ModelRegistry(tmp_path / "models")
    # §8.1 执行前快检要求模型已注册；测试统一预装默认模型
    registry.register(
        ModelManifest(
            model_id="NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn",
            provider="wav2vec2",
            revision="main",
        )
    )
    worker = worker or _FakeWorker()
    service = AiTimingService(
        settings=settings,
        cache=cache,
        registry=registry,
        runtime=AiRuntimeManager(),
        vocal_service=vocal_service,
        resolver=PronunciationResolver(analyzer=DummyAnalyzer(), chinese_mode=False),
        worker_factory=lambda python: worker,
        separation_executor=separation_executor,
        separation_identity=separation_identity
        or (lambda: {"model": "inst_v1e", "stem": "人声", "params": {}}),
    )
    return service, audio


class TestSnapshot:
    def test_snapshot_ok_with_sibling_vocal(self, tmp_path):
        service, audio = _make_service(tmp_path)
        snap = service.snapshot(_project(), str(audio), probe_runtime=False)
        assert snap.audio_ok and snap.project_ok and snap.has_content
        assert snap.pending_units == 0
        assert snap.vocal.state == "sibling"
        # 测试夹具已预装模型 → 模型状态就绪，不构成阻断
        assert snap.model is not None and snap.model.is_ready
        assert not any("模型" in r for r in snap.blocking_reasons)
        assert snap.separation_follows_host is False  # 未注入宿主
        assert snap.cache_root is not None

    def test_snapshot_no_audio(self, tmp_path):
        service, _ = _make_service(tmp_path)
        snap = service.snapshot(_project(), None, probe_runtime=False)
        assert not snap.audio_ok
        assert "未加载音频" in snap.blocking_reasons

    def test_snapshot_pending_units(self, tmp_path):
        service, audio = _make_service(tmp_path)
        project = _project()
        # 制造缺口：汉字无 ruby
        project.sentences[0].characters[0] = Character(
            char="赤", check_count=1, ruby=None, singer_id="s1"
        )
        snap = service.snapshot(project, str(audio), probe_runtime=False)
        assert snap.pending_units == 1
        assert any("缺少读音" in r for r in snap.blocking_reasons)
        # 阻断提示直接指出缺注音的字（行/列/字符），不再是纯数量
        reason = next(
            r for r in snap.blocking_reasons if "缺少读音" in r
        )
        assert "第 1 行第 1 字「赤」" in reason
        assert snap.pending_units_detail == ["第 1 行第 1 字「赤」"]


class TestExecute:
    def test_full_run_returns_applicable_command(self, tmp_path):
        service, audio = _make_service(tmp_path)
        events = []
        cmd = service.execute(
            _project(),
            str(audio),
            on_progress=lambda s, p, m: events.append((s, p, m)),
        )
        assert cmd is not None
        # 成功收尾必须补发 100：UI 进度条依赖最后一条信号走满
        # （2026-08 用户反馈任务完成但进度条停在 99% 未走完）
        assert events[-1][:2] == ("apply", 100)
        # 调用方（CommandManager）执行命令后时间戳被覆盖
        cmd.execute()
        project = _project()  # 命令持有的是自己的 project 实例
        cmd_project = cmd._project
        assert cmd_project.sentences[0].characters[0].timestamps == [0]
        assert cmd_project.sentences[0].characters[1].timestamps == [333]
        assert cmd_project.sentences[1].characters[0].timestamps == [666]

    def test_cache_hit_skips_worker(self, tmp_path):
        worker = _FakeWorker()
        service, audio = _make_service(tmp_path, worker=worker)
        cmd = service.execute(_project(), str(audio))
        assert len(worker.calls) == 1
        worker2 = _FakeWorker()
        service2, audio2 = _make_service(tmp_path, worker=worker2)
        # 同一 tmp_path：复用同一缓存（第二次执行命中缓存）
        service2._cache = service._cache
        service2._vocal_service._cache = service._cache
        cmd2 = service2.execute(_project(), str(audio))
        assert len(worker2.calls) == 0  # 未再推理
        assert cmd2 is not None

    def test_missing_audio_blocks(self, tmp_path):
        service, _ = _make_service(tmp_path)
        with pytest.raises(AiTimingError, match="音频"):
            service.execute(_project(), str(tmp_path / "nope.flac"))

    def test_pending_units_block_with_location(self, tmp_path):
        service, audio = _make_service(tmp_path)
        project = _project()
        project.sentences[0].characters[0] = Character(
            char="赤", check_count=1, ruby=None, singer_id="s1"
        )
        with pytest.raises(AiTimingError, match="赤.*缺少读音"):
            service.execute(project, str(audio))

    def test_multiple_sibling_vocals_need_choice(self, tmp_path):
        (tmp_path / "song_人声.wav").write_bytes(b"a")
        (tmp_path / "song_人声.flac").write_bytes(b"b")
        service, audio = _make_service(tmp_path)
        with pytest.raises(AiTimingError, match="多个人声文件") as exc_info:
            service.execute(_project(), str(audio))
        assert len(exc_info.value.vocal_choices) == 2

    def test_separation_missing_blocks_without_executor(self, tmp_path):
        service, audio = _make_service(tmp_path)
        (tmp_path / "song_人声.wav").unlink()  # 制造无可用人声状态
        with pytest.raises(AiTimingError, match="没有可复用的人声"):
            service.execute(_project(), str(audio))

    def test_separation_executor_registers_vocal(self, tmp_path):
        fresh = tmp_path / "fresh_vocal.wav"
        fresh.write_bytes(b"fresh")
        calls = {}

        def executor(source, progress, cancel):
            calls["source"] = source
            return fresh

        service, audio = _make_service(tmp_path, separation_executor=executor)
        (tmp_path / "song_人声.wav").unlink()  # 制造无可用人声状态
        cmd = service.execute(_project(), str(audio))
        assert cmd is not None
        assert calls["source"] == audio
        # 分离产物已入缓存：再执行一次命中人声缓存，不再调分离
        calls.pop("source", None)
        cmd2 = service.execute(_project(), str(audio))
        assert cmd2 is not None
        assert "source" not in calls

    def test_separation_progress_band_and_monotonic(self, tmp_path):
        """分离内部 0-100 必须压进 12-14 区间，整体进度全程单调：
        2026-08 用户反馈任务期间进度冲到 100 又回落（分离 100 →
        对齐 15），观感如同进度条错乱。"""
        fresh = tmp_path / "fresh_vocal.wav"
        fresh.write_bytes(b"fresh")

        def executor(source, progress, cancel):
            for pct in (0, 30, 60, 100):
                progress("separation", pct, f"分离中 {pct}%")
            return fresh

        service, audio = _make_service(tmp_path, separation_executor=executor)
        (tmp_path / "song_人声.wav").unlink()  # 制造无可用人声状态
        events = []
        cmd = service.execute(
            _project(),
            str(audio),
            on_progress=lambda s, p, m: events.append((s, p, m)),
        )
        assert cmd is not None
        sep = [p for s, p, _ in events if s == "separation"]
        assert all(12 <= p <= 14 for p in sep)  # 不冲顶、不越 prepare 15
        assert sep[-1] == 14  # 分离完成给后续阶段留出空间
        percents = [p for _, p, _ in events]
        assert all(b >= a for a, b in zip(percents, percents[1:]))

    def test_worker_failure_becomes_chinese_error(self, tmp_path):
        worker = _FakeWorker(fail=True)
        service, audio = _make_service(tmp_path, worker=worker)
        with pytest.raises(AiTimingError, match="AI 打轴执行失败"):
            service.execute(_project(), str(audio))

    def test_invalid_result_blocks_and_not_cached(self, tmp_path):
        worker = _FakeWorker(spans_override=[EmissionSpan(0, 0, 10)])
        service, audio = _make_service(tmp_path, worker=worker)
        with pytest.raises(AiTimingError, match="覆盖不完整|校验|结果"):
            service.execute(_project(), str(audio))

    def test_drift_after_run_blocks_command_build(self, tmp_path):
        """worker 完成后工程标注被修改 → 命令构建前阻断。"""
        project = _project()
        resolver = PronunciationResolver(
            analyzer=DummyAnalyzer(), chinese_mode=False
        )
        plan = resolver.resolve_project(project)
        request = build_alignment_request(plan)
        result = AlignmentResult(
            annotation_digest=plan.annotation_digest,
            model_id="fake",
            spans=[
                EmissionSpan(t.index, i * 100, i * 100 + 50)
                for i, t in enumerate(request.tokens)
            ],
        )
        # 模拟执行期间工程被修改（标注变化）
        project.sentences[0].characters[0].set_ruby(
            Ruby(parts=[RubyPart(text="あ")])
        )
        with pytest.raises(AiTimingError, match="发生了变化"):
            AiTimingService._build_command(project, plan, request, result)

    def test_cancel_before_worker_propagates(self, tmp_path):
        service, audio = _make_service(tmp_path)
        with pytest.raises(AiTimingError, match="已取消"):
            service.execute(_project(), str(audio), is_cancelled=lambda: True)

    def test_progress_receives_all_stages(self, tmp_path):
        service, audio = _make_service(tmp_path)
        stages = []
        service.execute(
            _project(),
            str(audio),
            on_progress=lambda s, p, m: stages.append(s),
        )
        for expected in ("prepare", "fingerprint", "vocal", "cache", "align", "apply"):
            assert expected in stages


class TestModelResolution:
    def test_worker_gets_local_model_path_when_installed(self, tmp_path):
        registry = ModelRegistry(tmp_path / "models")
        registry.register(
            ModelManifest(
                model_id="NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn",
                provider="wav2vec2",
                revision="main",
            )
        )
        worker = _FakeWorker()
        cache = AiCache(tmp_path / "ai_cache")
        (tmp_path / "song.flac").write_bytes(b"a")
        (tmp_path / "song_人声.wav").write_bytes(b"v")
        service = AiTimingService(
            settings=AiTimingSettings(),
            cache=cache,
            registry=registry,
            vocal_service=VocalPreparationService(cache),
            resolver=PronunciationResolver(
                analyzer=DummyAnalyzer(), chinese_mode=False
            ),
            worker_factory=lambda python: worker,
            separation_identity=lambda: {
                "model": "inst_v1e",
                "stem": "人声",
                "params": {},
            },
        )
        cmd = service.execute(_project(), str(tmp_path / "song.flac"))
        assert cmd is not None
        _, _, model_spec = worker.calls[0]
        assert str(tmp_path / "models") in model_spec["model_id"]


class TestExecutePrechecks:
    """2026-08 审查补充：§8.1 执行前快检。"""

    def test_missing_model_blocks_before_worker(self, tmp_path):
        """模型未注册 → 不启动 worker 直接中文阻断。"""
        worker = _FakeWorker()
        service, audio = _make_service(tmp_path, worker=worker)
        # 制造模型缺失：换一个未注册的 model_id
        service._settings.wav2vec2_model_id = "someone/other-model"
        with pytest.raises(AiTimingError, match="对齐模型未就绪"):
            service.execute(_project(), str(audio))
        assert worker.calls == []

    def test_bad_runtime_python_blocks(self, tmp_path):
        """配置的 Runtime 解释器不存在 → 阻断。"""
        worker = _FakeWorker()
        service, audio = _make_service(tmp_path, worker=worker)
        service._settings.runtime_python = str(tmp_path / "nope" / "python.exe")
        with pytest.raises(AiTimingError, match="解释器不存在"):
            service.execute(_project(), str(audio))
        assert worker.calls == []

    def test_vocal_choice_overrides_discovery(self, tmp_path):
        """弹窗内选择的人声文件优先于发现顺序（§6.1 多候选）。"""
        chosen = tmp_path / "picked_人声.flac"
        chosen.write_bytes(b"picked")
        worker = _FakeWorker()
        service, audio = _make_service(tmp_path, worker=worker)
        cmd = service.execute(_project(), str(audio), vocal_choice=chosen)
        assert cmd is not None
        _, used_audio, _ = worker.calls[0]
        assert Path(used_audio) == chosen


class TestWorkerLifecycleClose:
    """生命周期卫生：execute 结束（成败皆然）回收 worker 进程/管道。"""

    def test_worker_closed_on_success_and_failure(self, tmp_path):
        closed = []

        class _ClosableWorker(_FakeWorker):
            def close(self):
                closed.append(1)

        # 成功路径也回收
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        service, audio = _make_service(ok_dir, worker=_ClosableWorker())
        cmd = service.execute(_project(), str(audio), vocal_choice=None)
        assert cmd is not None and closed == [1]

        # 失败路径同样回收（独立目录，避免命中上一次的对齐缓存）
        closed.clear()
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        service, audio = _make_service(
            bad_dir, worker=_ClosableWorker(fail=True)
        )
        with pytest.raises(Exception, match="AI 打轴执行失败"):
            service.execute(_project(), str(audio), vocal_choice=None)
        assert closed == [1]
