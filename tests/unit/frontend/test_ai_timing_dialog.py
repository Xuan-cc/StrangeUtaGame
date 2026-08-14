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

    def test_eta_counts_down_between_messages(self, qapp, tmp_path):
        """两条进度消息之间 ETA 不冻结：按最近速率继续倒计时。"""
        import re as _re
        import time as _t

        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        now = _t.monotonic()
        dialog._eta_samples = [(now - 10.0, 10), (now - 5.0, 35)]
        dialog._eta_kind = "derived"  # 真实流程由 _on_task_progress 设置
        dialog._compute_eta(50)
        assert dialog._eta_rate > 0
        first = dialog._eta_tail_text()
        assert "预计剩余" in first
        # 模拟 3 秒没有新消息：剩余时间应随之递减
        dialog._eta_last = (dialog._eta_last[0] - 3.0, dialog._eta_last[1])
        second = dialog._eta_tail_text()

        def _secs(text):
            m = _re.search(r"(\d+):(\d{2})", text)
            return int(m.group(1)) * 60 + int(m.group(2))

        assert 0 < _secs(second) < _secs(first)
        assert _secs(first) - _secs(second) >= 2

    def test_eta_resets_on_phase_boundary_percent_drop(self, qapp, tmp_path):
        """阶段边界百分比回落（分离 100% → 对齐 20%）时速率重新累计。"""
        import time as _t

        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        now = _t.monotonic()
        # 分离阶段推进到 100% 的样本
        dialog._eta_samples = [(now - 8.0, 80), (now - 4.0, 95)]
        dialog._compute_eta(100)
        # 对齐阶段从 20% 重新开始：旧样本必须被清掉，速率不得为负
        text = dialog._compute_eta(20)
        assert dialog._eta_samples == [(dialog._eta_samples[-1][0], 20)]
        assert dialog._eta_rate == 0.0
        assert text == "正在估算剩余时间…"

    def test_transport_eta_and_speed_extracted(self, qapp, tmp_path):
        """上游消息自带剩余时间/速度时直接展示（分离、pip、下载通用）。"""
        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._on_task_progress(
            "separation", 70, "分离处理 3/10 块，预计剩余 0:12（2.1s/块）"
        )
        assert "预计剩余 0:12" in dialog.eta_label.text()
        assert "2.1s/块" in dialog.eta_label.text()
        # pip 包速度：无「预计剩余」→ 推算 ETA + 包/分速度同屏
        dialog._on_task_progress(
            "runtime", 40, "获取依赖 4/12（1.3 包/分）：Downloading torch"
        )
        assert "1.3 包/分" in dialog.eta_label.text()
        assert "已耗时" in dialog.eta_label.text()

    def test_stall_hint_is_task_specific(self, qapp, tmp_path):
        """长时间无输出的提示按任务区分（pip 安装不再显示推理文案）。"""
        import time as _t

        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._stall_text = (
            "正在安装（创建虚拟环境与下载大包期间可能数分钟没有输出）…"
        )
        now = _t.monotonic()
        dialog._task_started = now - 40
        dialog._last_msg_time = now - 20  # 超过 15s 无消息
        dialog._on_second_tick()
        assert "正在安装" in dialog.eta_label.text()
        assert "单步推理" not in dialog.eta_label.text()

    def test_vocal_needs_choice_warns(self, qapp, tmp_path):
        snap = _ready_snapshot()
        snap.vocal = VocalCandidate(state="needs_choice", choices=[])
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._on_snapshot_ready(snap)
        assert dialog.row_vocal._state == "warn"
        assert not dialog.btn_run.isEnabled()

    def test_snapshot_ready_clears_stale_eta(self, qapp, tmp_path):
        """检查完成回到就绪时，清掉上一轮任务残留的已耗时行。"""
        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog.eta_label.setText("已耗时 0:05 · 预计剩余 0:12")
        dialog._on_snapshot_ready(snap)
        assert dialog.eta_label.text() == ""

    def test_cache_dialog_starts_from_effective_root(self, qapp, tmp_path):
        """「更改…/浏览」起点 = 行内显示的生效缓存根，而非空设置项回退主目录。"""
        snap = _ready_snapshot()
        snap.cache_root = tmp_path / "cache" / "ai_timing"
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._on_snapshot_ready(snap)
        assert dialog._current_cache_root() == snap.cache_root

        # 无快照时回退到设置项
        dialog2, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog2._snapshot = None
        dialog2._settings.ai_cache_root = str(tmp_path / "cfg")
        assert dialog2._current_cache_root() == tmp_path / "cfg"

    def test_install_plan_prefers_managed_runtime(self, qapp, tmp_path):
        """方案 B：宿主托管解释器存在时走 shared 增量安装，否则自建 venv。"""
        snap = _ready_snapshot()
        exe = tmp_path / "managed" / "python.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#fake", encoding="utf-8")
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._managed_runtime_python = str(exe)
        mode, target = dialog._install_plan()
        assert mode == "shared" and str(target) == str(exe)

        # 路径失效（宿主卸载 runtime）→ 回落自建 venv
        dialog._managed_runtime_python = str(tmp_path / "gone" / "python.exe")
        mode, target = dialog._install_plan()
        assert mode == "venv" and target.name == "ai_runtime"

    def test_embedded_blocks_bare_venv_install(self, qapp, tmp_path, monkeypatch):
        """嵌入模式宿主 Runtime 未安装：默认引导，确认后才原生安装兜底。"""
        import strange_uta_game.frontend.editor.ai_timing_dialog as dlg_mod

        snap = _ready_snapshot()
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._embedded_mode = True
        dialog._managed_runtime_python = ""  # 宿主未提供（未安装/非托管配置）
        mode, target = dialog._install_plan()
        assert mode == "blocked"

        started = []
        dialog._run_task = lambda *a, **k: started.append(a)

        # 未确认 → 只引导，不发起安装
        monkeypatch.setattr(dlg_mod, "message_question", lambda *a, **k: False)
        dialog._on_install_runtime()
        assert started == []

        # 确认「独立安装」→ 走 standalone 同款 venv 路径
        monkeypatch.setattr(dlg_mod, "message_question", lambda *a, **k: True)
        dialog._on_install_runtime()
        assert len(started) == 1

        # 状态行给出去第 2 步的引导而非"缺少依赖请下载"
        snap.runtime = RuntimeStatus(
            available=False, message="缺少对齐依赖（transformers）"
        )
        dialog._on_snapshot_ready(snap)
        assert dialog.row_runtime._state == "warn"
        assert "音频分离" in dialog.row_runtime._state_label.text()

    def test_install_plan_prefers_user_chosen_interpreter(self, qapp, tmp_path):
        """用户显式选择/原生安装过的解释器优先于托管值（环境调用分支）。"""
        snap = _ready_snapshot()
        managed = tmp_path / "managed" / "python.exe"
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.write_text("#m", encoding="utf-8")
        chosen = tmp_path / "chosen" / "python.exe"
        chosen.parent.mkdir(parents=True, exist_ok=True)
        chosen.write_text("#c", encoding="utf-8")

        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._managed_runtime_python = str(managed)
        dialog._settings.runtime_python = str(chosen)
        mode, target = dialog._install_plan()
        assert mode == "shared" and str(target) == str(chosen)

        # 未显式选择时托管值生效
        dialog._settings.runtime_python = ""
        mode, target = dialog._install_plan()
        assert mode == "shared" and str(target) == str(managed)

    def test_goto_separation_button_jumps(self, qapp, tmp_path):
        """blocked 且宿主提供跳转能力时显示按钮，点击完成跳转。"""
        snap = _ready_snapshot()
        snap.runtime = RuntimeStatus(available=False, message="缺少对齐依赖")
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._embedded_mode = True
        dialog._managed_runtime_python = ""
        called = []
        dialog._open_separation_page = lambda: called.append(1) or True
        dialog._on_snapshot_ready(snap)
        assert not dialog.btn_goto_sep.isHidden()
        # 弹窗初始 refresh 尚未结束时会禁用动作按钮，测试先解禁
        dialog.btn_goto_sep.setEnabled(True)
        dialog.btn_goto_sep.click()
        assert called == [1]

        # 非阻塞状态隐藏按钮
        ok_snap = _ready_snapshot()
        dialog._open_separation_page = None
        dialog._on_snapshot_ready(ok_snap)
        assert dialog.btn_goto_sep.isHidden()

    def test_runtime_row_hints_cuda_upgrade(self, qapp, tmp_path):
        """CPU 版运行环境 + 检测到 NVIDIA 显卡：橙色提示可升级 CUDA 版。"""
        snap = _ready_snapshot()
        snap.runtime = RuntimeStatus(
            available=True,
            torch_version="2.9.1",
            transformers_version="5.15.0",
            cuda_available=False,
            gpu_name="NVIDIA GeForce RTX 4080",
        )
        dialog, _ = _make_dialog(qapp, tmp_path, snap, [])
        dialog._on_snapshot_ready(snap)
        assert dialog.row_runtime._state == "warn"
        combined = (
            dialog.row_runtime._state_label.text()
            + dialog.row_runtime._state_label.toolTip()
        )
        assert "安装 / 修复" in combined and "RTX 4080" in combined
        # 运行环境本身可用：不进阻断理由，执行按钮不受影响
        assert dialog.btn_run.isEnabled()

        # 无显卡信息时保持正常绿色状态
        snap2 = _ready_snapshot()
        dialog._on_snapshot_ready(snap2)
        assert dialog.row_runtime._state == "ok"


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
