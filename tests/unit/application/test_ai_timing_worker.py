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

    def test_missing_runtime_converted_to_chinese_error(self):
        """本机无 PyTorch：load 应转换为中文错误而非 ImportError。"""
        from strange_uta_game.backend.application.ai_timing.worker.providers import (
            AlignmentProviderError,
            Wav2Vec2LatnProvider,
            create_provider,
        )

        provider = create_provider({"provider": "wav2vec2"})
        try:
            import torch  # noqa: F401

            pytest.skip("本机已安装 PyTorch，跳过缺运行环境路径测试")
        except ImportError:
            pass
        with pytest.raises(AlignmentProviderError, match="对齐运行环境"):
            provider.load(
                {"provider": "wav2vec2"},
                lambda p, m: None,
                lambda: False,
            )
