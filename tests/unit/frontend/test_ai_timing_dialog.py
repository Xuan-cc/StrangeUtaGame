"""AI 打轴阶段 F：弹窗与工具栏入口的离屏 UI 测试。

使用注入的假服务栈验证：状态卡渲染、阻断理由禁用执行按钮、
ETA 估算与取消二次确认文案；不启动真实后台任务。
"""

from pathlib import Path

import pytest

from strange_uta_game.backend.application.ai_timing.models import ModelRegistry
from strange_uta_game.backend.application.ai_timing.runtime import RuntimeStatus
from strange_uta_game.backend.application.ai_timing.service import (
    AiTimingService,
    AiTimingSnapshot,
)
from strange_uta_game.backend.application.ai_timing.settings import AiTimingSettings
from strange_uta_game.backend.application.ai_timing.vocals import VocalCandidate
from strange_uta_game.backend.domain import (
    Character,
    Project,
    Sentence,
)


class _FakeService:
    """跳过真实 snapshot 的假 AiTimingService。"""

    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.executed = False

    def snapshot(self, project, audio_path, *, probe_runtime=True):
        return self._snapshot

    @property
    def effective_model_id(self):
        return "NextFire/demo"

    def execute(self, project, audio_path, *, on_progress=None, is_cancelled=None):
        self.executed = True
        return None


def _project():
    project = Project()
    project.sentences = [
        Sentence(
            singer_id="s1",
            characters=[
                Character(char="あ", check_count=1, ruby=None, singer_id="s1"),
            ],
        )
    ]
    return project


def _ready_snapshot():
    return AiTimingSnapshot(
        audio_ok=True,
        audio_path="C:/x/song.flac",
        project_ok=True,
        has_content=True,
        vocal=VocalCandidate(state="sibling", path=Path("C:/x/song_人声.wav")),
        runtime=RuntimeStatus(
            available=True,
            torch_version="2.9.0",
            transformers_version="4.44.0",
        ),
        model=None,
    )


def _make_dialog(qapp, tmp_path, snapshot, applied_calls):
    from strange_uta_game.backend.application.ai_timing.models import (
        ModelDownloadService,
    )
    from strange_uta_game.backend.application.ai_timing.runtime import (
        AiRuntimeManager,
    )
    from strange_uta_game.frontend.editor.ai_timing_dialog import AiTimingDialog

    registry = ModelRegistry(tmp_path / "models")
    service = _FakeService(snapshot)
    dialog = AiTimingDialog(
        project=_project(),
        audio_path=str(tmp_path / "song.flac"),
        service=service,
        settings=AiTimingSettings(),
        registry=registry,
        runtime=AiRuntimeManager(),
        download_service=ModelDownloadService(registry, _NullTransport()),
        on_applied=applied_calls.append,
        parent=None,
    )
    return dialog, service


class _NullTransport:
    def list_files(self, repo_id, revision):
        return []

    def download_file(self, *a, **k):
        raise RuntimeError("测试不执行下载")


class TestAiTimingDialog:
    def test_ready_snapshot_enables_run(self, qapp, tmp_path):
        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._on_snapshot_ready(snap)
        assert dialog.btn_run.isEnabled()
        assert dialog.blocking_label.isHidden()

    def test_blocking_reasons_disable_run(self, qapp, tmp_path):
        snap = _ready_snapshot()
        snap.pending_units = 3
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._on_snapshot_ready(snap)
        assert not dialog.btn_run.isEnabled()
        assert not dialog.blocking_label.isHidden()
        assert "缺少读音" in dialog.blocking_label.text()

    def test_rows_show_states(self, qapp, tmp_path):
        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._on_snapshot_ready(snap)
        assert dialog.row_audio._state == "ok"
        assert "song.flac" in dialog.row_audio._state_label.text()
        assert dialog.row_vocal._state == "ok"
        assert dialog.row_runtime._state == "ok"

    def test_eta_estimating_then_estimated(self, qapp, tmp_path):
        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._eta_samples = [(0.0, 10)]
        # 样本不足 → 正在估算
        assert dialog._compute_eta(12) == "正在估算剩余时间…"
        # 足够样本后给出估算
        dialog._eta_samples = [(0.0, 10), (10.0, 60)]
        text = dialog._compute_eta(60)
        assert "预计剩余" in text

    def test_vocal_needs_choice_warns(self, qapp, tmp_path):
        snap = _ready_snapshot()
        snap.vocal = VocalCandidate(state="needs_choice", choices=[])
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._on_snapshot_ready(snap)
        assert dialog.row_vocal._state == "warn"
        assert not dialog.btn_run.isEnabled()


class TestToolbarButton:
    def test_toolbar_has_ai_timing_button(self, qapp):
        from strange_uta_game.frontend.editor.timing.toolbar import EditorToolBar

        bar = EditorToolBar()
        assert hasattr(bar, "btn_ai_timing")
        assert bar.btn_ai_timing.text() == "AI 打轴"

        fired = []
        bar.ai_timing_clicked.connect(lambda: fired.append(1))
        bar.btn_ai_timing.click()
        assert fired == [1]

    def test_guide_actions_live_in_edit_menu_not_standalone(self, qapp):
        """导唱符系列并入编辑管理菜单（AI 打轴按钮腾出工具栏空间）。"""
        from strange_uta_game.frontend.editor.timing.toolbar import EditorToolBar

        bar = EditorToolBar()
        assert not hasattr(bar, "btn_insert_guide")
        actions = [a.text() for a in bar.btn_edit.menu().actions()]
        assert "插入导唱符" in actions
        assert "自动插入导唱符" in actions
