"""AI 打轴完整弹窗（阶段 F，2026-08 审查版）。

按计划文档 §3.2/§3.3 一次展示全部前置状态与执行区，并包含：

- 状态卡：原始音频（含时长）、歌词标注、人声素材（多候选时下拉选择）、
  分离环境（embedded 显示「跟随工作台设置」）、对齐环境、对齐模型
  （模型 ID、许可证与非商业提示、模型页链接）；
- 存储位置：模型根 / AI 缓存根 / Runtime；
- 高级选项：对齐模型（微调 / MMS_FA）、设备、尾音修正、下载镜像；
- 动作：下载模型、安装环境、浏览模型 / Runtime 目录、更改模型 /
  缓存位置、恢复推荐设置、深度重新校验；
- 执行区：进度 + 平滑 ETA、二次确认取消；
- 底部标注：对齐思路参考 FA-Kara / yohane。

复用现有 Fluent 组件与文案层级（§3.4）。对话框与工具栏其他弹窗
（批量变更等）同构：原生 ``QDialog``（系统关闭按钮、跟随应用主题、
不置顶）+ 内部 Fluent 控件 + ``tr()`` 多语言文案。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFileDialog, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
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
)
from strange_uta_game.backend.application.ai_timing.runtime import (
    AiRuntimeManager,
)
from strange_uta_game.backend.application.ai_timing.service import (
    AiTimingService,
    AiTimingSnapshot,
)
from strange_uta_game.backend.application.ai_timing.settings import (
    AiTimingSettings,
    resolve_model_root,
)
from strange_uta_game.backend.domain import Project
from strange_uta_game.frontend.editor.timing.dialogs import (
    FONT_DIALOG_BASE,
    char_dialog_font,
)
from strange_uta_game.frontend.fluent_widgets import message_question
from strange_uta_game.frontend.window_sizing import fit_to_screen

DEFAULT_WAV2VEC2_MODEL_ID = "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"
MODEL_PAGE_URL = (
    "https://huggingface.co/NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"
)
MODEL_LICENSE_TEXT = (
    f"默认模型 {DEFAULT_WAV2VEC2_MODEL_ID}（CC-BY-NC-SA-4.0，仅限非商业使用）"
)
CREDIT_TEXT = "对齐思路参考开源项目 FA-Kara 与 yohane（本项目不内嵌其代码）"


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
        self.set_state("busy", self.tr("检查中…"))

    def set_state(self, state: str, text: str) -> None:
        color = self._DOT_COLORS.get(state, "#999999")
        self._state = state
        self._state_label.setText(f"<b style='color:{color}'>●</b> {text}")

    def add_action(self, text: str, clicked: Callable) -> PushButton:
        """在行尾放一个小动作按钮（缺什么就在该行旁边给什么操作）。"""
        btn = PushButton(text, self)
        btn.setFixedHeight(26)
        btn.clicked.connect(clicked)
        self.layout().addWidget(btn)
        return btn


class AiTimingDialog(QDialog):
    """AI 打轴完整弹窗。

    与工具栏其他弹窗（批量变更等）同构：原生 QDialog（系统关闭按钮、
    跟随系统与应用主题、不置顶）+ 内部 Fluent 控件；全部用户可见文案
    走 ``self.tr()`` 以支持多语言。
    """

    def __init__(
        self,
        *,
        project: Project,
        audio_path: str,
        audio_duration_ms: int = 0,
        service: AiTimingService,
        settings: AiTimingSettings,
        registry: ModelRegistry,
        runtime: AiRuntimeManager,
        download_service: ModelDownloadService,
        on_applied: Callable,
        save_settings: Optional[Callable[[AiTimingSettings], None]] = None,
        download_proxy: str = "",
        context_checker: Optional[Callable[[], bool]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._project = project
        self._audio_path = audio_path
        self._audio_duration_ms = int(audio_duration_ms or 0)
        self._service = service
        self._settings = settings
        self._registry = registry
        self._runtime = runtime
        self._download_service = download_service
        self._on_applied = on_applied
        self._save_settings = save_settings
        self._download_proxy = download_proxy
        # 返回 True 表示打开时的工程/音频仍然有效（未切换/未关闭）
        self._context_checker = context_checker

        self._snapshot: Optional[AiTimingSnapshot] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[_TaskWorker] = None
        self._busy = False
        self._eta_samples: List[tuple] = []

        self._build_ui()
        self.refresh()

    # ── UI ──

    def _build_ui(self) -> None:
        from PyQt6.QtWidgets import QHBoxLayout

        self.setWindowTitle(self.tr("AI 打轴"))
        fit_to_screen(self, 720, 700)
        self.setFont(char_dialog_font(FONT_DIALOG_BASE))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = SubtitleLabel(self.tr("AI 打轴"), self)
        layout.addWidget(title)

        hint = BodyLabel(
            self.tr(
                "自动对齐会把歌词标注对齐到主唱人声；和声、重叠人声或伴唱"
                "可能导致对齐偏差，完成后请人工复核。成功后覆盖全部时间戳，"
                "可在工具栏撤销一次恢复。"
            ),
            self,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        from strange_uta_game.frontend.fluent_widgets import FluentGroupBox

        self._box_status = FluentGroupBox(self.tr("执行前状态"), self)
        layout.addWidget(self._box_status)
        # 状态卡
        self.row_audio = _StateRow(self.tr("原始音频"), self)
        self.row_annotations = _StateRow(self.tr("歌词标注"), self)
        self.row_vocal = _StateRow(self.tr("人声素材"), self)
        self.row_separation = _StateRow(self.tr("分离环境"), self)
        self.row_runtime = _StateRow(self.tr("对齐环境"), self)
        self.row_model = _StateRow(self.tr("对齐模型"), self)
        for row in (
            self.row_audio,
            self.row_annotations,
            self.row_vocal,
            self.row_separation,
            self.row_runtime,
            self.row_model,
        ):
            self._box_status.contentLayout.addWidget(row)
        # 行内动作：对齐环境的「安装/修复」、模型的「下载」「校验」
        # （检测到缺什么，同行直接给操作，不用去底部找）
        self.btn_install_runtime = self.row_runtime.add_action(
            self.tr("安装 / 修复"), self._on_install_runtime
        )
        self.btn_download_model = self.row_model.add_action(
            self.tr("下载模型"), self._on_download_model
        )
        self.btn_recheck = self.row_model.add_action(
            self.tr("校验"), self._on_deep_recheck
        )

        # 多人声候选选择（§6.1：多个严格候选时在弹窗内选择）
        self.vocal_combo = ComboBox(self)
        self.vocal_combo.hide()
        self._box_status.contentLayout.addWidget(self.vocal_combo)

        # 模型卡：许可证 + 非商业 + 可点击链接（§1.9/§3.3）
        model_credit = BodyLabel(
            f'<a href="{MODEL_PAGE_URL}">{MODEL_LICENSE_TEXT}</a>', self
        )
        model_credit.setOpenExternalLinks(True)
        model_credit.setWordWrap(True)
        self._box_status.contentLayout.addWidget(model_credit)

        self.blocking_label = BodyLabel("", self)
        self.blocking_label.setWordWrap(True)
        self.blocking_label.setStyleSheet("color:#e85555;")
        self.blocking_label.hide()
        self._box_status.contentLayout.addWidget(self.blocking_label)

        self._box_opts = FluentGroupBox(self.tr("选项与存储"), self)
        layout.addWidget(self._box_opts)
        # 高级选项（§3.2）
        advanced = QHBoxLayout()
        advanced.addWidget(BodyLabel(self.tr("模型:"), self))
        self.combo_model = ComboBox(self)
        self.combo_model.addItems([self.tr("微调模型（效果优先）"), self.tr("MMS_FA（备选）")])
        advanced.addWidget(self.combo_model)
        advanced.addWidget(BodyLabel(self.tr("设备:"), self))
        self.combo_device = ComboBox(self)
        self.combo_device.addItems([self.tr("自动"), "CPU", "CUDA"])
        advanced.addWidget(self.combo_device)
        self.chk_tail_snap = CheckBox(self.tr("尾音修正"), self)
        self.chk_tail_snap.setChecked(self._settings.tail_snap)
        advanced.addWidget(self.chk_tail_snap)
        self._box_opts.contentLayout.addLayout(advanced)
        mirror_row = QHBoxLayout()
        mirror_row.addWidget(BodyLabel(self.tr("下载镜像:"), self))
        self.edit_mirror = LineEdit(self)
        self.edit_mirror.setText(self._settings.download_mirror)
        self.edit_mirror.setPlaceholderText(self.tr("留空使用官方源，如 https://hf-mirror.com"))
        mirror_row.addWidget(self.edit_mirror, 1)
        self._box_opts.contentLayout.addLayout(mirror_row)

        # 存储位置（§3.2）
        self.storage_label = BodyLabel("", self)
        self.storage_label.setWordWrap(True)
        self._box_opts.contentLayout.addWidget(self.storage_label)

        # 存储位置的动作（§3.3 浏览/更改/恢复推荐；下载与安装已上移到对应行内）
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.btn_browse_model = PushButton(self.tr("浏览模型目录"), self)
        self.btn_browse_model.clicked.connect(
            lambda: self._open_dir(resolve_model_root(self._settings))
        )
        self.btn_browse_runtime = PushButton(self.tr("浏览运行环境"), self)
        self.btn_browse_runtime.clicked.connect(
            lambda: self._open_dir(
                resolve_model_root(self._settings).parent / "ai_runtime"
            )
        )
        self.btn_change_model_dir = PushButton(self.tr("更改模型位置"), self)
        self.btn_change_model_dir.clicked.connect(self._on_change_model_dir)
        self.btn_change_cache_dir = PushButton(self.tr("更改缓存位置"), self)
        self.btn_change_cache_dir.clicked.connect(self._on_change_cache_dir)
        self.btn_reset = PushButton(self.tr("恢复推荐设置"), self)
        self.btn_reset.clicked.connect(self._on_reset_settings)
        for b in (
            self.btn_browse_model,
            self.btn_browse_runtime,
            self.btn_change_model_dir,
            self.btn_change_cache_dir,
            self.btn_reset,
        ):
            b.setFixedHeight(26)
            actions.addWidget(b)
        actions.addStretch(1)
        self._box_opts.contentLayout.addLayout(actions)

        layout.addStretch(1)

        # 进度与执行区
        self.progress = ProgressBar(self)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status_label = StrongBodyLabel(self.tr("就绪"), self)
        layout.addWidget(self.status_label)
        self.eta_label = BodyLabel("", self)
        layout.addWidget(self.eta_label)

        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self.btn_cancel = PushButton(self.tr("取消"), self)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.btn_run = PrimaryPushButton(self.tr("自动对齐"), self)
        self.btn_run.setIcon(FIF.ROBOT)
        self.btn_run.clicked.connect(self._on_run_clicked)
        run_row.addWidget(self.btn_cancel)
        run_row.addWidget(self.btn_run)
        layout.addLayout(run_row)

        # 参考标注（用户要求：UI 内标注参考 FA-Kara）
        credit = BodyLabel(CREDIT_TEXT, self)
        credit.setStyleSheet("color:#888888;")
        layout.addWidget(credit)

    # ── 后台任务基础设施 ──

    def _run_task(self, fn: Callable, on_done: Callable, busy_text: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._eta_samples = []
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        for b in self._action_buttons():
            b.setEnabled(False)
        self.status_label.setText(busy_text)
        self.eta_label.setText("")

        self._thread = QThread(self)
        self._worker = _TaskWorker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_task_progress)
        # cleanup 先连：_done/_on_task_failed 执行时 busy 已复位，
        # 其内部的 refresh() 才不会被挡掉（安装完成状态不刷新的根因）
        for sig in (self._worker.finished, self._worker.failed):
            sig.connect(self._cleanup_task)
        self._worker.finished.connect(on_done)
        self._worker.failed.connect(self._on_task_failed)
        self._thread.start()

    def _action_buttons(self) -> list:
        return [
            self.btn_download_model,
            self.btn_install_runtime,
            self.btn_browse_model,
            self.btn_browse_runtime,
            self.btn_change_model_dir,
            self.btn_change_cache_dir,
            self.btn_reset,
            self.btn_recheck,
        ]  # 全部已存在：前三个在状态行内，其余在存储动作行

    def _cleanup_task(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self._busy = False
        self.btn_cancel.setEnabled(False)
        for b in self._action_buttons():
            b.setEnabled(True)

    def _on_task_progress(self, stage: str, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        if message:
            # pip 解析行可能极长，截断避免窗口被拉长
            shown = message if len(message) <= 80 else message[:79] + "…"
            self.status_label.setText(shown)
        # 传输层给出真实速度/剩余时优先展示，否则退回百分比估算
        if "MB/s" in message and "预计剩余" in message:
            tail = message.split("，")[-1].rstrip("）")
            self.eta_label.setText(tail + "　·　" + [
                p for p in message.split("，") if "MB/s" in p
            ][0])
        else:
            self.eta_label.setText(self._compute_eta(percent))

    def _compute_eta(self, percent: int) -> str:
        """平滑 ETA：样本不足时显示「正在估算」（§8.2）。"""
        if percent <= 0:
            return self.tr("正在估算剩余时间…")
        now = time.monotonic()
        self._eta_samples.append((now, percent))
        self._eta_samples = self._eta_samples[-20:]
        first_t, first_p = self._eta_samples[0]
        elapsed = now - first_t
        gained = percent - first_p
        if gained < 5 or elapsed < 2:
            return self.tr("正在估算剩余时间…")
        rate = gained / elapsed  # 百分点 / 秒
        remaining = max(0, (100 - percent) / rate)
        minutes, seconds = divmod(int(remaining), 60)
        return self.tr("预计剩余 {m}:{s:02d}").format(m=minutes, s=seconds)

    def _on_task_failed(self, message: str) -> None:
        self.progress.setValue(0)
        if "取消" in message:
            InfoBar.warning(
                title=self.tr("已取消"),
                content=self.tr("AI 打轴已取消，未应用任何结果。"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            self.status_label.setText(self.tr("已取消"))
        else:
            InfoBar.error(
                title=self.tr("AI 打轴失败"),
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=8000,
                parent=self,
            )
            self.status_label.setText(self.tr("失败"))
        self.refresh()

    # ── 设置读写 ──

    def _persist_settings(self) -> None:
        self._settings.provider = (
            "wav2vec2" if self.combo_model.currentIndex() == 0 else "mms_fa"
        )
        self._settings.device = ("auto", "cpu", "cuda")[
            self.combo_device.currentIndex()
        ]
        self._settings.tail_snap = self.chk_tail_snap.isChecked()
        self._settings.download_mirror = self.edit_mirror.text().strip()
        if self._save_settings is not None:
            try:
                self._save_settings(self._settings)
            except Exception:
                pass

    # ── 状态刷新 ──

    def refresh(self) -> None:
        """后台刷新状态快照（含 Runtime 探测）。"""
        if self._busy:
            return
        for row in (
            self.row_audio,
            self.row_annotations,
            self.row_vocal,
            self.row_separation,
            self.row_runtime,
            self.row_model,
        ):
            row.set_state("busy", "检查中…")
        self.btn_run.setEnabled(False)

        def _task(progress_cb, cancel_check):
            return self._service.snapshot(
                self._project, self._audio_path, probe_runtime=True
            )

        self._run_task(_task, self._on_snapshot_ready, self.tr("正在检查执行条件…"))

    def _on_snapshot_ready(self, snapshot: AiTimingSnapshot) -> None:
        self._snapshot = snapshot
        self.progress.setValue(0)
        self.status_label.setText(self.tr("就绪"))

        # 原始音频（含时长，§3.2）
        if snapshot.audio_ok:
            duration = ""
            if self._audio_duration_ms > 0:
                secs = self._audio_duration_ms / 1000
                duration = f"，时长 {int(secs // 60)}:{int(secs % 60):02d}"
            self.row_audio.set_state("ok", Path(snapshot.audio_path).name + duration)
        else:
            self.row_audio.set_state("error", self.tr("未加载音频"))

        if snapshot.project_ok and snapshot.has_content:
            if snapshot.pending_units or snapshot.generation_errors:
                detail = "、".join(snapshot.generation_errors[:2])
                self.row_annotations.set_state(
                    "error",
                    f"{snapshot.pending_units} 个节奏点缺少读音 {detail}".strip(),
                )
            else:
                self.row_annotations.set_state("ok", self.tr("既有标注优先，缺口已补足"))
        else:
            self.row_annotations.set_state("error", self.tr("工程没有可对齐正文"))

        # 人声素材：多候选时展示下拉选择（§6.1）
        vocal = snapshot.vocal
        self.vocal_combo.clear()
        self.vocal_combo.hide()
        if vocal is None:
            self.row_vocal.set_state("warn", self.tr("未检查（缺少音频）"))
        elif vocal.state in ("session", "cache", "sibling", "separated"):
            source_names = {
                "session": self.tr("工作台会话人声"),
                "cache": self.tr("AI 缓存"),
                "sibling": self.tr("同目录人声文件"),
                "separated": self.tr("本次分离的人声"),
            }
            self.row_vocal.set_state(
                "ok", self.tr("可复用（{source}）").format(source=source_names.get(vocal.state, vocal.state))
            )
        elif vocal.state == "needs_choice":
            self.row_vocal.set_state("warn", self.tr("同目录存在多个人声文件，请选择"))
            for path in vocal.choices:
                self.vocal_combo.addItem(path.name, userData=str(path))
            if vocal.choices:
                self.vocal_combo.setCurrentIndex(0)
            self.vocal_combo.show()
        else:
            self.row_vocal.set_state("warn", self.tr("需要分离人声"))

        # 分离环境（§3.2/§6.2）：embedded 跟随工作台设置；
        # standalone = 共享 Runtime（含 audio-separator）是否已装
        if snapshot.separation_follows_host:
            self.row_separation.set_state(
                "ok", self.tr("跟随工作台「分离人声」设置")
            )
        elif snapshot.separation_available:
            self.row_separation.set_state(
                "ok", self.tr("就绪：无人声时自动分离（UVR-MDX 人声模型）")
            )
        else:
            self.row_separation.set_state(
                "warn",
                self.tr("未安装：点击「对齐环境 → 安装 / 修复」可一并安装分离能力"),
            )

        runtime = snapshot.runtime
        if runtime is not None and runtime.available:
            self.row_runtime.set_state("ok", runtime.summary)
        else:
            self.row_runtime.set_state(
                "error",
                (runtime.message if runtime else "") or self.tr("对齐环境不可用"),
            )

        model = snapshot.model
        is_mms = self.combo_model.currentIndex() == 1
        self.btn_download_model.setVisible(not is_mms)
        self.btn_recheck.setVisible(not is_mms)
        if is_mms:
            self.row_model.set_state(
                "ok", "MMS_FA（备选）：随对齐环境自动获取，无需下载"
            )
        elif model is not None and model.is_ready:
            self.row_model.set_state("ok", self._elide_text(model.model_dir))
        else:
            # 告知目标位置：用户在下载前就知道模型会放到哪里（可更改）
            self.row_model.set_state(
                "error",
                self.tr("未安装，将下载到 {path}").format(
                    path=resolve_model_root(self._settings)
                ),
            )

        # 存储位置（§3.2）：分行明确标注，避免与状态行混淆
        lines = [
            self.tr("模型目录：{path}").format(
                path=resolve_model_root(self._settings)
            )
        ]
        if snapshot.cache_root is not None:
            lines.append(
                self.tr("AI 缓存（人声/对齐结果，自动清理）：{path}").format(
                    path=snapshot.cache_root
                )
            )
        lines.append(
            self.tr("运行环境（Python/torch）：{path}").format(
                path=self._settings.runtime_python
                or self.tr("当前解释器（未安装专用环境）")
            )
        )
        self.storage_label.setText("\n".join(lines))


        reasons = snapshot.blocking_reasons
        if reasons:
            self.blocking_label.setText(
                self.tr("执行前需解决：\n")
                + "\n".join(reasons)
            )
            self.blocking_label.show()
            self.btn_run.setEnabled(False)
        else:
            self.blocking_label.hide()
            self.btn_run.setEnabled(True)

    # ── 动作 ──

    @staticmethod
    def _elide_text(text, width=48):
        text = str(text)
        if len(text) <= width:
            return text
        return text[: width // 2 - 4] + "…" + text[-width // 2:]

    @staticmethod
    def _open_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        QFileDialog.getOpenFileName(None, self.tr("选择目录内任意文件以打开该目录"), str(path))

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

        def _done(result) -> None:
            InfoBar.success(
                title=self.tr("模型下载完成"),
                content=self.tr("对齐模型已就绪。"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            self.refresh()

        self._run_task(_task, _done, self.tr("正在下载模型…"))

    def _on_install_runtime(self) -> None:
        target = resolve_model_root(self._settings).parent / "ai_runtime"

        def _task(progress_cb, cancel_check):
            status = self._runtime.install(
                target,
                mirror=self._settings.download_mirror,
                proxy=self._download_proxy,
                progress=lambda p, m: progress_cb("runtime", p, m),
                cancel=cancel_check,
            )
            # 安装成功后记录解释器路径（_done 里持久化）
            self._settings.runtime_python = status.python_path
            return status

        def _done(status) -> None:
            self._persist_settings()
            InfoBar.success(
                title=self.tr("对齐环境就绪"),
                content=status.summary,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            self.refresh()

        self._run_task(_task, _done, self.tr("正在安装对齐环境…"))

    def _on_change_model_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, self.tr("选择模型根目录"), str(resolve_model_root(self._settings))
        )
        if chosen:
            self._settings.model_root = chosen
            self._persist_settings()
            self.refresh()

    def _on_change_cache_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            self.tr("选择 AI 缓存根目录（独立运行模式生效）"),
            self._settings.ai_cache_root or str(Path.home()),
        )
        if chosen:
            self._settings.ai_cache_root = chosen
            self._persist_settings()
            InfoBar.info(
                title=self.tr("缓存位置已更新"),
                content=self.tr("独立运行模式将使用新位置；嵌入模式由工作台注入。"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            self.refresh()

    def _on_reset_settings(self) -> None:
        self._settings.provider = "wav2vec2"
        self._settings.device = "auto"
        self._settings.tail_snap = True
        self._settings.download_mirror = ""
        self.combo_model.setCurrentIndex(0)
        self.combo_device.setCurrentIndex(0)
        self.chk_tail_snap.setChecked(True)
        self.edit_mirror.clear()
        self._persist_settings()
        self.refresh()

    def _on_deep_recheck(self) -> None:
        """深度重新校验（sha256 重算，§3.3「重新校验」）。"""
        model_id = self._service.effective_model_id

        def _task(progress_cb, cancel_check):
            status = self._registry.validate(model_id, deep=True)
            progress_cb("recheck", 100, self.tr("校验完成"))
            return status

        def _done(status) -> None:
            state_text = {
                "ok": self.tr("校验通过"),
                "corrupt": self.tr("校验失败"),
                "incomplete": self.tr("下载未完成"),
                "missing": self.tr("模型未安装"),
            }.get(status.state, status.state)
            (InfoBar.success if status.is_ready else InfoBar.warning)(
                title=self.tr("模型深度校验"),
                content=state_text
                + (f"：{status.message}" if status.message else ""),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            self.refresh()

        self._run_task(_task, _done, self.tr("正在深度校验模型…"))

    def _on_run_clicked(self) -> None:
        if self._snapshot is None or not self._snapshot.ready:
            return
        if self._context_checker is not None and not self._context_checker():
            InfoBar.warning(
                title=self.tr("工程已变化"),
                content=self.tr(
                    "工程或音频在弹窗打开后发生了切换，请关闭本窗口后重新打开"
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=6000,
                parent=self,
            )
            return
        self._persist_settings()
        vocal_choice = None
        if self.vocal_combo.isVisible() and self.vocal_combo.count() > 0:
            vocal_choice = self.vocal_combo.currentData()

        def _task(progress_cb, cancel_check):
            return self._service.execute(
                self._project,
                self._audio_path,
                on_progress=progress_cb,
                is_cancelled=cancel_check,
                vocal_choice=Path(vocal_choice) if vocal_choice else None,
            )

        def _done(command) -> None:
            try:
                self._on_applied(command)
            except Exception as exc:  # noqa: BLE001
                self._on_task_failed(f"应用结果失败：{exc}")
                return
            InfoBar.success(
                title=self.tr("AI 打轴完成"),
                content=self.tr("已覆盖全部时间戳，可撤销一次恢复。"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            self.close()

        self._run_task(_task, _done, self.tr("正在执行自动对齐…"))

    def _on_cancel_clicked(self) -> None:
        """取消需二次确认；确认后丢弃未应用结果（§8.3）。"""
        confirmed = message_question(
            self,
            self.tr("取消 AI 打轴"),
            self.tr("确定取消 AI 打轴吗？当前尚未应用的结果将被丢弃。"),
            yes_text=self.tr("取消任务"),
            no_text=self.tr("继续执行"),
            default_cancel=True,
        )
        if confirmed and self._worker is not None:
            self._worker.request_cancel()
            self.status_label.setText(self.tr("正在取消…"))

    def closeEvent(self, event) -> None:
        if self._busy:
            reply = message_question(
                self,
                self.tr("任务进行中"),
                self.tr("AI 打轴任务仍在进行，关闭窗口将取消任务并丢弃未应用的结果。确定关闭吗？"),
                yes_text=self.tr("取消并关闭"),
                no_text=self.tr("继续任务"),
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
