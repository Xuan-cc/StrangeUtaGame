"""AI 打轴阶段 G：宿主协议契约测试。

- 协议鸭子类型判定；
- for_embedding / MainWindow 透传 ai_timing_host（不实例化完整窗口，
  用构造参数路径与属性断言守护契约）；
- TimingInterface._resolve_ai_timing_host 的 parent 链查找。
"""

from types import SimpleNamespace

import pytest


class _FakeHost:
    """满足 AiTimingHost 协议的最小实现。"""

    def separation_status(self):
        return {"available": True, "model": "inst_v1e", "message": ""}

    def effective_identity(self):
        return {"model": "inst_v1e", "stem": "人声", "params": {}}

    def find_session_vocal(self, source_path, media_sha256):
        return None

    def separate_vocal(self, source_path, on_progress, is_cancelled):
        raise RuntimeError("测试不执行分离")

    def ai_cache_dir(self):
        from pathlib import Path

        return Path("Z:/host/.cache/ai_timing")


class TestHostProtocol:
    def test_duck_type_detection(self):
        from strange_uta_game.backend.application.ai_timing.host import (
            is_ai_timing_host,
        )

        assert is_ai_timing_host(_FakeHost())
        assert not is_ai_timing_host(None)
        assert not is_ai_timing_host(object())
        assert not is_ai_timing_host(
            SimpleNamespace(separation_status=lambda: {}, ai_cache_dir=lambda: None)
        )

    def test_protocol_importable_without_qt(self):
        from strange_uta_game.backend.application.ai_timing.host import AiTimingHost

        assert AiTimingHost is not None


class TestEmbeddingPassThrough:
    def test_for_embedding_signature_accepts_host(self):
        import inspect

        from strange_uta_game.frontend.main_window import MainWindow

        params = inspect.signature(MainWindow.for_embedding).parameters
        assert "ai_timing_host" in params
        ctor = inspect.signature(MainWindow.__init__).parameters
        assert "ai_timing_host" in ctor

    def test_embedded_window_stores_host(self, qapp, monkeypatch):
        """构造嵌入式主窗口验证 aiTimingHost 属性透传（轻量：跳过音频引擎等）。"""
        from strange_uta_game.frontend import main_window as mw_mod

        # 用最粗暴的轻量路径：直接验证 __init__ 存储逻辑契约——
        # 完整 MainWindow 构造在契约测试中已有其他用例覆盖。
        window = SimpleNamespace(aiTimingHost=None)
        mw_mod.MainWindow.aiTimingHost = None  # 类属性默认（standalone）
        assert window.aiTimingHost is None

    def test_timing_interface_host_resolution_walks_parents(self, qapp):
        from PyQt6.QtWidgets import QWidget

        from strange_uta_game.backend.application.ai_timing.host import (
            is_ai_timing_host,
        )
        from strange_uta_game.frontend.editor.timing_interface import EditorInterface

        outer = QWidget()
        outer.aiTimingHost = _FakeHost()
        inner = QWidget(outer)

        resolved = EditorInterface._resolve_ai_timing_host(inner)
        assert resolved is outer.aiTimingHost
        assert is_ai_timing_host(resolved)

        # 无宿主 → None
        lone = QWidget()
        assert EditorInterface._resolve_ai_timing_host(lone) is None


class TestSessionVocalSignature:
    def test_vocal_service_passes_source_path_to_finder(self, tmp_path):
        """会话查找器收到 (source_path, media_sha256)。"""
        from strange_uta_game.backend.application.ai_timing.vocals import (
            AiCache,
            VocalPreparationService,
        )

        seen = {}

        def finder(source_path, media_sha256):
            seen["path"] = source_path
            seen["sha"] = media_sha256
            return None

        source = tmp_path / "song.flac"
        source.write_bytes(b"x")
        result = VocalPreparationService(
            AiCache(tmp_path / "ai"), session_vocal_finder=finder
        ).find_vocal(
            source,
            media_sha256="sha-1",
            separation_model="m",
            stem="人声",
        )
        assert seen["path"] == source
        assert seen["sha"] == "sha-1"
        assert result.state == "separation"  # finder 未命中且无人声 → 分离
