"""AI 打轴完整弹窗（阶段 F）。

一次展示全部前置状态（音频/标注/人声/运行环境/模型）与执行区；
提供模型下载、运行环境安装、重新校验；执行时进度 + ETA、取消需
二次确认；成功后由宿主回调应用命令（CommandManager 执行并入撤销栈）。

复用现有 Fluent 组件与文案层级（§3.4），不另造视觉语言；对话框基类
沿用 SUG 嵌入式兼容的 ``Dialog``（普通顶层窗口，见 fluent_widgets 中
关于 MaskDialogBase 在嵌入式下不可用的说明）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import (
    BodyLabel,
    Dialog,
    FluentIcon as FIF,
    IndeterminateProgressRing,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)
from qfluentwidgets import InfoBar, InfoBarPosition

from strange_uta_game.backend.application.ai_timing.models import (
    ModelDownloadService,
    ModelRegistry,
    ModelRegistryError,
)
from strange_uta_game.backend.application.ai_timing.runtime import (
    AiRuntimeError,
    AiRuntimeManager,
)
from strange_uta_game.backend.application.ai_timing.service import (
    AiTimingError,
    AiTimingService,
    AiTimingSnapshot,
)
from strange_uta_game.backend.application.ai_timing.settings import (
    AiTimingSettings,
    resolve_model_root,
)
from strange_uta_game.backend.domain import Project
from strange_uta_game.frontend.fluent_widgets import message_question


class _TaskWorker(QObject):
    """在 QThread 中执行任意 ``(progress_cb, cancel_check) -> object`` 任务。"""

    progress = pyqtSignal(str, int, str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable):
        super().__init__()
        self._fn = fn
        self._cancelled = False

    def request_cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        def _progress(stage: str, percent: int, message: str) -> None:
            self.progress.emit(stage, percent, message)

        try:
            result = self._fn(_progress, lambda: self._cancelled)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 — 所有后台错误统一回主线程
            self.failed.emit(str(exc))


class _StateRow(QWidget):
    """状态卡中的一行：名称 + 彩点 + 状态文本。

    不继承 FluentLabelBase —— 其 ``__init__`` 是 singledispatchmethod，
    子类覆写构造器会与其内部 ``self.__init__(parent)`` 重派发形成无限
    递归；这里用组合方式持有一个名称标签与一个富文本状态标签。
    """

    _DOT_COLORS = {
        "ok": "#3ec46d",
        "warn": "#f5a623",
        "error": "#e85555",
        "busy": "#4a7de0",
    }

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._name = name
        self._state = "busy"
        from PyQt6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._name_label = BodyLabel(name, self)
        self._name_label.setFixedWidth(80)
        self._state_label = BodyLabel("", self)
        layout.addWidget(self._name_label)
        layout.addWidget(self._state_label, 1)
        self.set_state("busy", "检查中…")

    def set_state(self, state: str, text: str) -> None:
        color = self._DOT_COLORS.get(state, "#999999")
        self._state = state
        self._state_label.setText(
            f"<b style='color:{color}'>●</b> {text}"
        )


class AiTimingDialog(Dialog):
    """AI 打轴完整弹窗。"""

    def __init__(
        self,
        *,
        project: Project,
        audio_path: str,
        service: AiTimingService,
        settings: AiTimingSettings,
        registry: ModelRegistry,
        runtime: AiRuntimeManager,
        download_service: ModelDownloadService,
        on_applied: Callable,
        parent=None,
    ):
        super().__init__("AI 打轴", "", parent)
        self._project = project
        self._audio_path = audio_path
        self._service = service
        self._settings = settings
        self._registry = registry
        self._runtime = runtime
        self._download_service = download_service
        self._on_applied = on_applied

        self._snapshot: Optional[AiTimingSnapshot] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[_TaskWorker] = None
        self._busy = False
        self._eta_samples: List[tuple] = []

        self._build_ui()
        self.refresh()

    # ── UI ──

    def _build_ui(self) -> None:
        self.setTitleBarVisible(False)
        self.yesButton.hide()
        self.cancelButton.hide()
        self.contentLabel.hide()
        self.setFixedSize(680, 560)

        from PyQt6.QtWidgets import QVBoxLayout

        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)

        title = SubtitleLabel("AI 打轴", content)
        layout.addWidget(title)

        hint = BodyLabel(
            "自动对齐会把歌词标注对齐到人声音频；成功后覆盖全部时间戳，"
            "可在工具栏撤销一次恢复。",
            content,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 状态卡
        self.row_audio = _StateRow("原始音频", content)
        self.row_annotations = _StateRow("歌词标注", content)
        self.row_vocal = _StateRow("人声素材", content)
        self.row_runtime = _StateRow("对齐环境", content)
        self.row_model = _StateRow("对齐模型", content)
        for row in (
            self.row_audio,
            self.row_annotations,
            self.row_vocal,
            self.row_runtime,
            self.row_model,
        ):
            layout.addWidget(row)

        self.blocking_label = BodyLabel("", content)
        self.blocking_label.setWordWrap(True)
        self.blocking_label.setStyleSheet("color:#e85555;")
        self.blocking_label.hide()
        layout.addWidget(self.blocking_label)

        # 动作按钮
        from PyQt6.QtWidgets import QHBoxLayout

        actions = QHBoxLayout()
        self.btn_download_model = PushButton("下载对齐模型", content)
        self.btn_download_model.clicked.connect(self._on_download_model)
        self.btn_install_runtime = PushButton("安装对齐环境", content)
        self.btn_install_runtime.clicked.connect(self._on_install_runtime)
        self.btn_browse_model = PushButton("浏览模型目录", content)
        self.btn_browse_model.clicked.connect(self._on_browse_models)
        self.btn_recheck = PushButton("重新校验", content)
        self.btn_recheck.clicked.connect(lambda: self.refresh())
        for b in (
            self.btn_download_model,
            self.btn_install_runtime,
            self.btn_browse_model,
            self.btn_recheck,
        ):
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)

        layout.addStretch(1)

        # 进度与执行区
        self.progress = ProgressBar(content)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status_label = StrongBodyLabel("就绪", content)
        layout.addWidget(self.status_label)
        self.eta_label = BodyLabel("", content)
        layout.addWidget(self.eta_label)

        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self.btn_cancel = PushButton("取消", content)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.btn_run = PrimaryPushButton("自动对齐", content)
        self.btn_run.setIcon(FIF.ROBOT)
        self.btn_run.clicked.connect(self._on_run_clicked)
        run_row.addWidget(self.btn_cancel)
        run_row.addWidget(self.btn_run)
        layout.addLayout(run_row)

        self.vBoxLayout.insertWidget(1, content, 1)

    # ── 后台任务基础设施 ──

    def _run_task(self, fn: Callable, on_done: Callable, busy_text: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._eta_samples = []
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        for b in (
            self.btn_download_model,
            self.btn_install_runtime,
            self.btn_browse_model,
            self.btn_recheck,
        ):
            b.setEnabled(False)
        self.status_label.setText(busy_text)
        self.eta_label.setText("")

        self._thread = QThread(self)
        self._worker = _TaskWorker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_task_progress)
        self._worker.finished.connect(on_done)
        self._worker.failed.connect(self._on_task_failed)
        for sig in (self._worker.finished, self._worker.failed):
            sig.connect(self._cleanup_task)
        self._thread.start()

    def _cleanup_task(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self._busy = False
        self.btn_cancel.setEnabled(False)
        for b in (
            self.btn_download_model,
            self.btn_install_runtime,
            self.btn_browse_model,
            self.btn_recheck,
        ):
            b.setEnabled(True)

    def _on_task_progress(self, stage: str, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        if message:
            self.status_label.setText(message)
        self.eta_label.setText(self._compute_eta(percent))

    def _compute_eta(self, percent: int) -> str:
        """平滑 ETA：样本不足时显示「正在估算」（§8.2）。"""
        if percent <= 0:
            return "正在估算剩余时间…"
        now = time.monotonic()
        self._eta_samples.append((now, percent))
        self._eta_samples = self._eta_samples[-20:]
        first_t, first_p = self._eta_samples[0]
        elapsed = now - first_t
        gained = percent - first_p
        if gained < 15 or elapsed < 3:
            return "正在估算剩余时间…"
        rate = gained / elapsed  # 百分点 / 秒
        remaining = max(0, (100 - percent) / rate)
        minutes, seconds = divmod(int(remaining), 60)
        return f"预计剩余 {minutes}:{seconds:02d}"

    def _on_task_failed(self, message: str) -> None:
        self.progress.setValue(0)
        self.status_label.setText("失败")
        if "取消" in message:
            InfoBar.warning(
                title="已取消",
                content="AI 打轴已取消，未应用任何结果。",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            self.status_label.setText("已取消")
        else:
            InfoBar.error(
                title="AI 打轴失败",
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=8000,
                parent=self,
            )
        self.refresh()

    # ── 状态刷新 ──

    def refresh(self) -> None:
        """后台刷新状态快照（含 Runtime 探测）。"""
        if self._busy:
            return
        for row in (
            self.row_audio,
            self.row_annotations,
            self.row_vocal,
            self.row_runtime,
            self.row_model,
        ):
            row.set_state("busy", "检查中…")
        self.btn_run.setEnabled(False)

        def _task(progress_cb, cancel_check):
            return self._service.snapshot(
                self._project, self._audio_path, probe_runtime=True
            )

        self._run_task(
            _task,
            self._on_snapshot_ready,
            "正在检查执行条件…",
        )

    def _on_snapshot_ready(self, snapshot: AiTimingSnapshot) -> None:
        self._snapshot = snapshot
        self.progress.setValue(0)
        self.status_label.setText("就绪")

        if snapshot.audio_ok:
            self.row_audio.set_state("ok", Path(snapshot.audio_path).name)
        else:
            self.row_audio.set_state("error", "未加载音频")

        if snapshot.project_ok and snapshot.has_content:
            if snapshot.pending_units or snapshot.generation_errors:
                detail = "、".join(snapshot.generation_errors[:2])
                self.row_annotations.set_state(
                    "error",
                    f"{snapshot.pending_units} 个节奏点缺少读音 {detail}".strip(),
                )
            else:
                self.row_annotations.set_state("ok", "既有标注优先，缺口已补足")
        else:
            self.row_annotations.set_state("error", "工程没有可对齐正文")

        vocal = snapshot.vocal
        if vocal is None:
            self.row_vocal.set_state("warn", "未检查（缺少音频）")
        elif vocal.state in ("session", "cache", "sibling"):
            source_names = {
                "session": "工作台会话人声",
                "cache": "AI 缓存",
                "sibling": "同目录人声文件",
            }
            self.row_vocal.set_state(
                "ok", f"可复用（{source_names.get(vocal.state, vocal.state)}）"
            )
        elif vocal.state == "needs_choice":
            self.row_vocal.set_state("warn", "同目录存在多个人声文件，需选择")
        else:
            self.row_vocal.set_state("warn", "需要分离人声")

        runtime = snapshot.runtime
        if runtime is not None and runtime.available:
            self.row_runtime.set_state("ok", runtime.summary)
        else:
            self.row_runtime.set_state(
                "error", (runtime.message if runtime else "") or "对齐环境不可用"
            )

        model = snapshot.model
        if model is not None and model.is_ready:
            self.row_model.set_state("ok", str(model.model_dir))
        else:
            self.row_model.set_state(
                "error", (model.message if model else "") or "模型未安装"
            )

        reasons = snapshot.blocking_reasons
        if reasons:
            self.blocking_label.setText("执行前需解决：\n" + "\n".join(reasons))
            self.blocking_label.show()
            self.btn_run.setEnabled(False)
        else:
            self.blocking_label.hide()
            self.btn_run.setEnabled(True)

    # ── 动作 ──

    def _on_download_model(self) -> None:
        model_id = self._service.effective_model_id

        def _task(progress_cb, cancel_check):
            return self._download_service.download(
                model_id,
                "wav2vec2" if self._settings.provider == "wav2vec2" else "mms_fa",
                license_text="CC-BY-NC-SA-4.0（仅限非商业使用）",
                progress=lambda p, m: progress_cb("model", p, m),
                cancel=cancel_check,
            )

        self._run_task(_task, lambda result: self._on_download_done(), "正在下载模型…")

    def _on_download_done(self) -> None:
        InfoBar.success(
            title="模型下载完成",
            content="对齐模型已就绪。",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
        self.refresh()

    def _on_install_runtime(self) -> None:
        target = resolve_model_root(self._settings).parent / "ai_runtime"

        def _task(progress_cb, cancel_check):
            status = self._runtime.install(
                target,
                mirror=self._settings.download_mirror,
                progress=lambda p, m: progress_cb("runtime", p, m),
                cancel=cancel_check,
            )
            # 安装成功后记录解释器路径（standalone 持久化）
            self._settings.runtime_python = status.python_path
            return status

        def _done(status) -> None:
            InfoBar.success(
                title="对齐环境就绪",
                content=status.summary,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            self.refresh()

        self._run_task(_task, _done, "正在安装对齐环境…")

    def _on_browse_models(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        root = resolve_model_root(self._settings)
        root.mkdir(parents=True, exist_ok=True)
        QFileDialog.getOpenFileName(
            self, "模型目录（选择其中任意文件以定位）", str(root)
        )

    def _on_run_clicked(self) -> None:
        if self._snapshot is None or not self._snapshot.ready:
            return

        def _task(progress_cb, cancel_check):
            return self._service.execute(
                self._project,
                self._audio_path,
                on_progress=progress_cb,
                is_cancelled=cancel_check,
            )

        def _done(command) -> None:
            try:
                self._on_applied(command)
            except Exception as exc:  # noqa: BLE001
                self._on_task_failed(f"应用结果失败：{exc}")
                return
            InfoBar.success(
                title="AI 打轴完成",
                content="已覆盖全部时间戳，可撤销一次恢复。",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            self.close()

        self._run_task(_task, _done, "正在执行自动对齐…")

    def _on_cancel_clicked(self) -> None:
        """取消需二次确认；确认后丢弃未应用结果（§8.3）。"""
        confirmed = message_question(
            self,
            "取消 AI 打轴",
            "确定取消 AI 打轴吗？当前尚未应用的结果将被丢弃。",
            yes_text="取消任务",
            no_text="继续执行",
            default_cancel=True,
        )
        if confirmed and self._worker is not None:
            self._worker.request_cancel()
            self.status_label.setText("正在取消…")

    def closeEvent(self, event) -> None:
        if self._busy:
            reply = message_question(
                self,
                "任务进行中",
                "AI 打轴任务仍在进行，关闭窗口将取消任务并丢弃未应用的结果。"
                "确定关闭吗？",
                yes_text="取消并关闭",
                no_text="继续任务",
                default_cancel=True,
            )
            if not reply:
                event.ignore()
                return
            if self._worker is not None:
                self._worker.request_cancel()
        # 等待后台线程收尾，避免线程销毁竞态（§9 Qt teardown 坑）
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        super().closeEvent(event)


__all__ = ["AiTimingDialog"]
