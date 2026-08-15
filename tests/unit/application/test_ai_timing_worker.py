"""AI 打轴阶段 C：worker 协议、provider 与进程生命周期测试。

使用 fake provider 通过真实子进程验证：协议编解码、进度回调、
正常执行、协作取消（含二次确认后的丢弃语义边界——进程退出）、
崩溃隔离与超时终止。真实模型（wav2vec2 / MMS_FA）烟测为手动/定期
项（§12.3），不在本文件范围。
"""

import threading
import time

import pytest

from strange_uta_game.backend.application import AlignmentRequest
from strange_uta_game.backend.application.ai_timing.alignment import AlignmentToken
from strange_uta_game.backend.application.ai_timing.worker import (
    AlignmentWorkerCancelled,
    AlignmentWorkerClient,
    AlignmentWorkerError,
    AlignmentWorkerTimeout,
    decode_message,
    deserialize_request,
    encode_message,
    normalize_latn_text,
    serialize_request,
)
from strange_uta_game.backend.application.ai_timing.worker.protocol import (
    WorkerProtocolError,
    deserialize_result,
    serialize_result,
)

_FAKE_MODEL = {"provider": "fake"}


def _fake_request(n_tokens: int = 4, **options) -> AlignmentRequest:
    tokens = [
        AlignmentToken(
            index=i,
            text=f"to{k}",
            raw_reading=f"と{k}",
            location=(0, i, 0),
            line_idx=0,
        )
        for i, k in enumerate(range(n_tokens))
    ]
    return AlignmentRequest(
        annotation_digest="digest-test",
        tokens=tokens,
        options=dict(options),
    )


class TestProtocol:
    """JSON Lines 协议编解码与请求/结果序列化 roundtrip。"""

    def test_request_roundtrip(self):
        request = _fake_request()
        payload = serialize_request(request)
        restored = deserialize_request(payload)
        assert restored.annotation_digest == request.annotation_digest
        assert [t.location for t in restored.tokens] == [
            t.location for t in request.tokens
        ]
        assert [t.text for t in restored.tokens] == [t.text for t in request.tokens]

    def test_result_roundtrip(self):
        from strange_uta_game.backend.application.ai_timing.alignment import (
            AlignmentResult,
            EmissionSpan,
        )

        result = AlignmentResult(
            annotation_digest="d",
            model_id="fake",
            spans=[EmissionSpan(0, 0, 100, 0.9), EmissionSpan(1, 100, 250, 0.8)],
        )
        restored = deserialize_result(serialize_result(result))
        assert restored.spans[1].token_index == 1
        assert restored.spans[1].start_ms == 100
        assert abs(restored.spans[1].score - 0.8) < 1e-6

    def test_message_encode_decode(self):
        line = encode_message({"type": "progress", "percent": 50, "message": "中文"})
        assert decode_message(line)["message"] == "中文"

    def test_invalid_json_rejected(self):
        with pytest.raises(WorkerProtocolError):
            decode_message("not-json")

    def test_missing_type_rejected(self):
        with pytest.raises(WorkerProtocolError):
            decode_message('{"a": 1}')

    def test_bad_request_payload_rejected(self):
        with pytest.raises(WorkerProtocolError):
            deserialize_request({"tokens": [{"index": "x"}]})


class TestNormalizeLatn:
    """yohane 口径的 Latn 转写归一化。"""

    def test_lowercase_and_apostrophe(self):
        assert normalize_latn_text("KyO'") == "kyo'"
        assert normalize_latn_text("’") == "'"

    def test_non_latn_replaced_by_space(self):
        # 注：声调剥离在阶段 B build_alignment_tokens 已完成，provider 层
        # 的归一化只是防御性兜底——带调字符在此被替换为空格属预期行为
        assert normalize_latn_text("あa") == "a"
        assert normalize_latn_text("nǐ hǎo") == "n h o"
        assert normalize_latn_text("ni hao") == "ni hao"

    def test_collapse_spaces(self):
        assert normalize_latn_text("  a   b  ") == "a b"

    def test_empty(self):
        assert normalize_latn_text("🎵") == ""
        assert normalize_latn_text("ー") == ""


class TestWorkerProcessLifecycle:
    """真实子进程：fake provider 驱动的全链路。"""

    def test_run_success_with_progress(self, tmp_path):
        request = _fake_request(n_tokens=4, fake_duration_ms=800)
        progress_events = []
        with AlignmentWorkerClient() as client:
            result = client.run(
                request,
                audio_path=str(tmp_path / "vocal.wav"),
                model_spec=_FAKE_MODEL,
                on_progress=lambda s, p, m: progress_events.append((s, p, m)),
            )
        assert result.annotation_digest == "digest-test"
        assert result.model_id == "fake"
        assert len(result.spans) == 4
        # 均分区间：0/200/400/600
        starts = [s.start_ms for s in result.spans]
        assert starts == [0, 200, 400, 600]
        assert starts == sorted(starts)
        # 进度回调覆盖 load 与 align 两个阶段
        stages = {s for s, _, _ in progress_events}
        assert "load" in stages and "align" in stages
        assert max(p for _, p, _ in progress_events) == 100

    def test_cooperative_cancel_from_another_thread(self, tmp_path):
        request = _fake_request(n_tokens=50, fake_duration_ms=5000, fake_delay_ms=60)
        client = AlignmentWorkerClient()
        outcomes = {}

        def _run():
            try:
                client.run(request, audio_path=str(tmp_path / "v.wav"), model_spec=_FAKE_MODEL)
                outcomes["status"] = "done"
            except AlignmentWorkerCancelled:
                outcomes["status"] = "cancelled"
            except Exception as exc:  # noqa: BLE001
                outcomes["status"] = f"error: {exc}"

        thread = threading.Thread(target=_run)
        thread.start()
        time.sleep(1.0)  # 让对齐进行到一半
        client.cancel()
        thread.join(timeout=15)
        client.close()

        assert outcomes.get("status") == "cancelled"
        # 进程无残留
        assert client._proc is None or client._proc.poll() is not None

    def test_provider_crash_isolated_as_error(self, tmp_path):
        request = _fake_request()
        with AlignmentWorkerClient() as client:
            with pytest.raises(AlignmentWorkerError, match="测试崩溃"):
                client.run(
                    request,
                    audio_path=str(tmp_path / "v.wav"),
                    model_spec={"provider": "fake", "fake_crash": True},
                )

    def test_process_death_converted_to_error(self, tmp_path):
        """worker 进程直接死亡（os._exit）→ 中文错误，宿主不崩。"""
        request = _fake_request()
        with AlignmentWorkerClient() as client:
            with pytest.raises(AlignmentWorkerError):
                client.run(
                    request,
                    audio_path=str(tmp_path / "v.wav"),
                    model_spec={"provider": "fake", "fake_crash_process": True},
                    timeout_s=30,
                )

    def test_timeout_kills_worker(self, tmp_path):
        request = _fake_request(n_tokens=100, fake_duration_ms=100000, fake_delay_ms=100)
        with AlignmentWorkerClient() as client:
            with pytest.raises(AlignmentWorkerTimeout):
                client.run(
                    request,
                    audio_path=str(tmp_path / "v.wav"),
                    model_spec=_FAKE_MODEL,
                    timeout_s=2.0,
                )

    def test_unknown_provider_rejected(self, tmp_path):
        request = _fake_request()
        with AlignmentWorkerClient() as client:
            with pytest.raises(AlignmentWorkerError, match="未知的对齐 provider"):
                client.run(
                    request,
                    audio_path=str(tmp_path / "v.wav"),
                    model_spec={"provider": "nonexistent"},
                )

    def test_missing_audio_path_rejected(self):
        request = _fake_request()
        with AlignmentWorkerClient() as client:
            with pytest.raises(AlignmentWorkerError, match="音频"):
                client.run(request, audio_path="", model_spec=_FAKE_MODEL)


class TestProviderRegistry:
    """provider 注册与环境变量强制 fake。"""

    def test_env_forces_fake_provider(self, monkeypatch):
        monkeypatch.setenv("SUG_AITIMING_FAKE_PROVIDER", "1")
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            FakeProvider,
            create_provider,
        )

        assert isinstance(create_provider({"provider": "wav2vec2"}), FakeProvider)

    def test_default_provider_is_wav2vec2(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            Wav2Vec2LatnProvider,
            create_provider,
        )

        assert isinstance(create_provider({}), Wav2Vec2LatnProvider)

    def test_missing_runtime_converted_to_chinese_error(self, monkeypatch):
        """模拟无 PyTorch：load 应转换为中文错误而非 ImportError。

        sys.modules 中置 None 可使 ``import torch`` 抛 ImportError，
        测试因此不依赖本机是否真实安装 PyTorch（与 _fake_venv 的
        ``sys.modules`` 注入手法一致）。
        """
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            AlignmentProviderError,
            Wav2Vec2LatnProvider,
            create_provider,
        )

        provider = create_provider({"provider": "wav2vec2"})
        import sys as _sys

        monkeypatch.setitem(_sys.modules, "torch", None)
        with pytest.raises(AlignmentProviderError, match="对齐运行环境"):
            provider.load(
                {"provider": "wav2vec2"},
                lambda p, m: None,
                lambda: False,
            )


class TestLayerProgressForward:
    """推理真实进度：encoder 层 forward hook（fake 层对象，不依赖 torch）。

    FA-Kara / yohane 的推理是一次整段 forward、无任何中间进度；我们用
    层完成时刻观察进度，计算与输出保持逐位一致。
    """

    def _fake_layers(self, n: int):
        class _Handle:
            def __init__(self, layer, cb):
                self._layer, self._cb = layer, cb

            def remove(self):
                self._layer.hooks.remove(self._cb)

        class _Layer:
            def __init__(self):
                self.hooks = []

            def register_forward_hook(self, cb):
                self.hooks.append(cb)
                return _Handle(self, cb)

        return [_Layer() for _ in range(n)]

    def test_find_encoder_layers_hf_and_torchaudio_shapes(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            _find_encoder_layers,
        )

        class _NS:
            pass

        hf = _NS()
        hf.wav2vec2 = _NS()
        hf.wav2vec2.encoder = _NS()
        hf.wav2vec2.encoder.layers = self._fake_layers(3)
        assert len(_find_encoder_layers(hf)) == 3

        ta = _NS()
        ta.encoder = _NS()
        ta.encoder.layers = self._fake_layers(2)
        assert len(_find_encoder_layers(ta)) == 2

        # 结构不可识别 → 空列表（退化为无层进度）
        assert _find_encoder_layers(object()) == []

    def test_layer_progress_emitted_per_layer(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            Wav2Vec2LatnProvider,
        )

        provider = Wav2Vec2LatnProvider()
        layers = self._fake_layers(4)

        class _NS:
            pass

        root = _NS()
        root.wav2vec2 = _NS()
        root.wav2vec2.encoder = _NS()
        root.wav2vec2.encoder.layers = layers
        provider._model = root

        events = []

        def _forward():
            for layer in layers:
                for cb in list(layer.hooks):
                    cb(layer, None, None)
            return "emission"

        result = provider._forward_with_layer_progress(
            _forward, lambda p, m: events.append((p, m)), lo=66, hi=84
        )
        assert result == "emission"
        # 66 + int(18 * k / 4)
        assert [p for p, _ in events] == [70, 75, 79, 84]
        assert all("编码器层" in m for _, m in events)
        assert "4/4" in events[-1][1]
        # forward 结束后 hooks 全部移除，重复 forward 不会重复累计
        assert all(not layer.hooks for layer in layers)

    def test_forward_exception_still_removes_hooks(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            Wav2Vec2LatnProvider,
        )

        provider = Wav2Vec2LatnProvider()
        layers = self._fake_layers(2)

        class _NS:
            pass

        root = _NS()
        root.wav2vec2 = _NS()
        root.wav2vec2.encoder = _NS()
        root.wav2vec2.encoder.layers = layers
        provider._model = root

        def _boom():
            raise RuntimeError("推理失败")

        with pytest.raises(RuntimeError, match="推理失败"):
            provider._forward_with_layer_progress(_boom, lambda p, m: None)
        assert all(not layer.hooks for layer in layers)

    def test_find_layers_torchaudio_modern_shape(self):
        """新版 torchaudio（MMS_FA 包装器）：model.encoder.transformer.layers。"""
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            _find_encoder_layers,
        )

        class _NS:
            pass

        layers = self._fake_layers(24)
        root = _NS()  # _Wav2Vec2Model 包装器（真实模型有 named_modules）
        root.named_modules = lambda: [
            ("model.encoder.transformer.layers", layers)
        ]
        assert _find_encoder_layers(root) == layers

    def test_resolve_device_pref_priority(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            resolve_device_pref,
        )

        # auto：CUDA > MPS > CPU
        assert resolve_device_pref("auto", True, True) == "cuda"
        assert resolve_device_pref("auto", False, True) == "mps"
        assert resolve_device_pref("auto", False, False) == "cpu"
        # 显式偏好原样（不可用回退由 _select_device 处理）
        assert resolve_device_pref("mps", True, False) == "mps"
        assert resolve_device_pref("cuda", False, False) == "cuda"

    def test_no_layer_structure_degrades_silently(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            Wav2Vec2LatnProvider,
        )

        provider = Wav2Vec2LatnProvider()
        provider._model = object()  # 无 encoder.layers
        events = []
        result = provider._forward_with_layer_progress(
            lambda: 1, lambda p, m: events.append((p, m))
        )
        assert result == 1
        assert events == []  # 只保留外层阶段消息，不报层进度


class TestTailSilenceCriterion:
    """尾音静音判据：相对整轨平均功率的比例（fake 波形，不依赖 torch）。

    帧能量直接由 fake 波形构造（每帧 320 个幅度 sqrt(E) 的样本），
    20ms/帧（16kHz × 320 样本），与真实 emission 帧率一致。
    """

    SPF = 320  # samples per frame
    SR = 16000

    def _wave(self, energies):
        import numpy as np

        samples = []
        for e in energies:
            amp = float(e) ** 0.5
            samples.extend([amp] * self.SPF)

        arr = np.array(samples, dtype="float32")

        class _W:
            def detach(self):
                return self

            def cpu(self):
                return self

            def numpy(self):
                return arr

        return _W()

    def _spans(self, groups, energies, **kw):
        from strange_uta_game.backend.application.ai_timing.alignment import (
            AlignmentRequest,
            AlignmentToken,
        )
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            Wav2Vec2LatnProvider,
        )

        tokens = [
            AlignmentToken(
                index=i, text=f"t{i}", raw_reading=f"t{i}", location=(0, i, 0)
            )
            for i in range(len(groups))
        ]
        request = AlignmentRequest(
            tokens=tokens, options={"tail_snap": True}
        )
        provider = Wav2Vec2LatnProvider()
        num_frames = len(energies)
        return provider._frames_to_spans(
            request,
            groups,
            num_frames=num_frames,
            num_samples=num_frames * self.SPF,
            sample_rate=self.SR,
            tail_snap=True,
            waveform=self._wave(energies),
            **kw,
        )

    def test_tail_capped_at_silence_before_next_token(self):
        """跨长间奏的尾音裁到静音边界，而不是延伸到下一 token 起点。"""
        # 帧 0-14 有声、15-39 静音；token0 的 CTC 终点在第 10 帧，
        # 下一 token 第 30 帧才起 —— 旧逻辑会把尾音拉到 600ms
        energies = [1.0] * 15 + [0.0] * 25
        spans = self._spans([(0, 10), (30, 32)], energies)
        # 静音从第 15 帧起持续 ≥4 帧 → 边界=15 帧=300ms
        assert spans[0].end_ms == 300
        assert spans[1].end_ms == 32 * 20  # 末 token 已在静音内，不延长

    def test_last_token_tail_extends_to_silence(self):
        """末个 token 被 CTC 截断的真实尾音延伸到静音边界。"""
        # 帧 0-25 有声、26-39 静音；token 终点第 24 帧 → 延伸到 26 帧
        energies = [1.0] * 26 + [0.0] * 14
        spans = self._spans([(0, 10), (12, 20), (22, 24)], energies)
        assert spans[2].end_ms == 26 * 20
        # 中间 token 之间全程有声：保持吸附下一起点
        assert spans[1].end_ms == 22 * 20

    def test_no_waveform_falls_back_to_plain_snap(self):
        """无波形（旧调用路径）：退回纯吸附下一起点行为。"""
        from strange_uta_game.backend.application.ai_timing.alignment import (
            AlignmentRequest,
            AlignmentToken,
        )
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            Wav2Vec2LatnProvider,
        )

        tokens = [
            AlignmentToken(
                index=i, text=f"t{i}", raw_reading=f"t{i}", location=(0, i, 0)
            )
            for i in range(2)
        ]
        spans = Wav2Vec2LatnProvider()._frames_to_spans(
            AlignmentRequest(tokens=tokens, options={"tail_snap": True}),
            [(0, 10), (30, 32)],
            num_frames=40,
            num_samples=40 * self.SPF,
            sample_rate=self.SR,
            tail_snap=True,
            waveform=None,
        )
        assert spans[0].end_ms == 30 * 20  # 吸附到下一 token 起点

    def test_silence_boundary_min_frames(self):
        """持续静音需连续 ≥TAIL_SILENCE_MIN_FRAMES 帧，瞬时低谷不截断。"""
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            TAIL_SILENCE_MIN_FRAMES,
            _silence_boundary,
        )

        energies = [1.0] * 20 + [0.0] * 3 + [1.0] * 7 + [0.0] * 10
        # mean = 27/40 → 阈值 0.1×0.675 = 0.0675；3 帧低谷不算静音
        b = _silence_boundary(energies, sum(energies) / len(energies), 5, 40)
        assert b == 30  # 第一段 ≥4 帧静音的起点
        assert TAIL_SILENCE_MIN_FRAMES == 4


class TestExternalInterpreterBootstrap:
    """外部解释器（托管 PyMSS runtime 等）的 worker 启动方式。

    嵌入式 Python 发行版带 ``pythonXXX._pth``，会完全忽略 PYTHONPATH：
    ``-m`` 直接 ModuleNotFoundError。必须用 runpy 引导把包根注入
    sys.path（2026-08 托管 runtime 上实测复现并验证）。
    """

    def _capture_popen(self, monkeypatch):
        captured = {}

        class _FakeProc:
            def __init__(self):
                self.stdin = None
                self.stdout = None

            def poll(self):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env")
            return _FakeProc()

        monkeypatch.setattr(
            "strange_uta_game.backend.application.ai_timing.worker.client.subprocess.Popen",
            _fake_popen,
        )
        return captured

    def test_external_interpreter_uses_runpy_bootstrap(self, monkeypatch):
        from strange_uta_game.backend.application.ai_timing.worker.client import (
            AlignmentWorkerClient,
        )

        captured = self._capture_popen(monkeypatch)
        client = AlignmentWorkerClient(python_exe="C:/managed/python.exe")
        client._ensure_started()
        cmd = captured["cmd"]
        assert cmd[0] == "C:/managed/python.exe"
        assert cmd[1] == "-c"
        # 包根作为 argv 注入，模块名进 runpy 代码
        assert len(cmd) == 4 and cmd[3].endswith("src")
        assert "strange_uta_game.backend.application.ai_timing.worker" in cmd[2]
        assert "runpy" in cmd[2]

    def test_bootstrap_appends_package_root_not_insert(self, monkeypatch):
        """包根必须 append 到 sys.path 尾部（不能 insert(0)）。

        回归：frozen 包根（_internal）里混着 PyInstaller 为宿主 Python
        （3.13）收集的 stdlib 扩展与 pythonXXX.dll；insert(0) 会让运行
        环境（嵌入式 3.12）import unicodedata 时加载到 3.13 版 .pyd，
        直接 "Module use of python313.dll conflicts"（打包版实测）。
        append 保证运行环境自带 stdlib 优先，包根仅作兜底。
        """
        from strange_uta_game.backend.application.ai_timing.worker.client import (
            AlignmentWorkerClient,
        )

        captured = self._capture_popen(monkeypatch)
        client = AlignmentWorkerClient(python_exe="C:/managed/python.exe")
        client._ensure_started()
        bootstrap = captured["cmd"][2]
        assert "sys.path.append(sys.argv.pop(1))" in bootstrap
        assert "insert" not in bootstrap
        # 包根也不进 PYTHONPATH：对非嵌入式 Python 它排在 stdlib 前，
        # 同样会遮蔽运行环境自带版本
        env = captured["env"]
        assert "PYTHONPATH" not in env

    def test_same_interpreter_keeps_dash_m(self, monkeypatch):
        import sys as _sys

        from strange_uta_game.backend.application.ai_timing.worker.client import (
            AlignmentWorkerClient,
        )

        captured = self._capture_popen(monkeypatch)
        client = AlignmentWorkerClient(python_exe=_sys.executable)
        client._ensure_started()
        cmd = captured["cmd"]
        assert cmd[1:3] == [
            "-m",
            "strange_uta_game.backend.application.ai_timing.worker",
        ]


class TestWordGroupResplit:
    """拉丁词组词内边界按子 token 数比例切分（手工拆分英文音节修正）。"""

    def test_proportional_split(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            apply_word_group_resplit,
        )

        # 权重 2/1/3，词区间 [10, 22)：边界 10, 14, 16, 22
        grouped = [(10, 12), (12, 13), (13, 22)]  # Viterbi 原始词内边界（不可靠）
        groups = [[5, 6], [7], [8, 9, 10]]
        out = apply_word_group_resplit(grouped, groups, [[0, 1, 2]])
        assert out == [(10, 14), (14, 16), (16, 22)]

    def test_single_and_invalid_groups_ignored(self):
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            apply_word_group_resplit,
        )

        grouped = [(0, 10), (10, 20)]
        # 单元素组、越界索引：原样保留
        out = apply_word_group_resplit(grouped, [[1], [2]], [[0], [0, 5]])
        assert out == grouped
        # 词区间非法（末组终点 2 早于首组起点 5）：原样保留
        out2 = apply_word_group_resplit(
            [(5, 8), (3, 2)], [[1], [2]], [[0, 1]]
        )
        assert out2 == [(5, 8), (3, 2)]


class TestFrozenPackageRoot:
    """frozen 包根：spec datas 已把源码树收进 _internal（sys._MEIPASS）。"""

    def test_frozen_uses_meipass(self, monkeypatch, tmp_path):
        import sys as _sys
        from pathlib import Path

        from strange_uta_game.backend.application.ai_timing.worker.client import (
            AlignmentWorkerClient,
        )

        internal = tmp_path / "_internal"
        internal.mkdir()
        monkeypatch.setattr(_sys, "frozen", True, raising=False)
        monkeypatch.setattr(_sys, "_MEIPASS", str(internal), raising=False)
        assert AlignmentWorkerClient._package_root() == internal
        # frozen 宿主永远走外部解释器路径（runpy 引导）
        assert AlignmentWorkerClient()._is_same_interpreter() is False

    def test_frozen_without_meipass_falls_back_to_exe_dir(
        self, monkeypatch
    ):
        import sys as _sys
        from pathlib import Path

        from strange_uta_game.backend.application.ai_timing.worker.client import (
            AlignmentWorkerClient,
        )

        monkeypatch.setattr(_sys, "frozen", True, raising=False)
        monkeypatch.delattr(_sys, "_MEIPASS", raising=False)
        assert AlignmentWorkerClient._package_root() == Path(
            _sys.executable
        ).resolve().parent
