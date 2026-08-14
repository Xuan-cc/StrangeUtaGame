"""AI 打轴编排服务（阶段 F）。

把阶段 A–E 的能力串成两条入口：

- ``snapshot``：弹窗打开时的状态快照（音频/工程/人声/Runtime/模型/
  缺口），任一不满足给出中文原因与对应状态；
- ``execute``：§8 的完整执行——重校验、指纹、人声发现、对齐缓存、
  worker 推理、结果校验、缓存登记，最终构建（未执行的）
  ``ApplyAiTimingCommand`` 交由调用方在主线程通过 CommandManager
  执行并入撤销栈。

本层不依赖 Qt：进度/取消通过回调注入；宿主能力（会话人声、分离
执行器）通过可调用对象注入（阶段 G 的 AiTimingHost 在此接线）。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from strange_uta_game.backend.application.ai_timing.alignment import (
    AlignmentRequest,
    AlignmentResult,
    AlignmentValidationError,
    EmissionSpan,
    build_alignment_request,
    validate_result,
)
from strange_uta_game.backend.application.ai_timing.commands import (
    ApplyAiTimingCommand,
)
from strange_uta_game.backend.application.ai_timing.models import (
    ModelRegistry,
    ModelStatus,
)
from strange_uta_game.backend.application.ai_timing.pronunciation import (
    PronunciationPlan,
    compute_annotation_digest,
)
from strange_uta_game.backend.application.ai_timing.resolver import (
    PronunciationResolver,
)
from strange_uta_game.backend.application.ai_timing.runtime import (
    AiRuntimeManager,
    RuntimeStatus,
)
from strange_uta_game.backend.application.ai_timing.settings import (
    AiTimingSettings,
    resolve_model_root,
)
from strange_uta_game.backend.application.ai_timing.vocals import (
    AiCache,
    VocalCandidate,
    VocalPreparationService,
    alignment_cache_metadata,
    sha256_of_path,
)
from strange_uta_game.backend.application.ai_timing.worker.providers import (
    DEFAULT_WAV2VEC2_MODEL,
)
from strange_uta_game.backend.domain import Project

ProgressFn = Callable[[str, int, str], None]
"""on_progress(stage_id, percent, message)；stage ∈ fingerprint/vocal/
cache/align/apply。"""

CancelFn = Callable[[], bool]


class AiTimingError(RuntimeError):
    """执行前阻断或执行失败（中文消息，可直接展示）。"""

    def __init__(self, message: str, *, vocal_choices: Optional[List[Path]] = None):
        super().__init__(message)
        self.vocal_choices = vocal_choices or []


class WorkerLike:
    """worker 客户端最小接口（AlignmentWorkerClient 满足）。"""

    def run(
        self,
        request: AlignmentRequest,
        audio_path: str,
        model_spec: dict,
        on_progress=None,
        timeout_s=None,
    ) -> AlignmentResult: ...  # pragma: no cover


@dataclass
class AiTimingSnapshot:
    """弹窗状态快照（各状态卡数据）。"""

    audio_ok: bool = False
    audio_path: str = ""
    project_ok: bool = False
    has_content: bool = False
    pending_units: int = 0
    generation_errors: List[str] = field(default_factory=list)
    vocal: Optional[VocalCandidate] = None
    runtime: Optional[RuntimeStatus] = None
    model: Optional[ModelStatus] = None
    separation_follows_host: bool = False
    """embedded：分离能力由宿主注入（跟随工作台设置）。"""

    separation_available: bool = False
    """分离环境可用（embedded 跟随宿主；standalone = 共享 Runtime 已装）。"""

    cache_root: Optional[Path] = None
    """当前 AI 缓存根目录（存储位置状态卡显示用）。"""

    @property
    def blocking_reasons(self) -> List[str]:
        reasons: List[str] = []
        if not self.project_ok:
            reasons.append("当前没有可对齐的工程")
        elif not self.has_content:
            reasons.append("工程中没有歌词正文")
        if not self.audio_ok:
            reasons.append("未加载音频")
        if self.pending_units:
            reasons.append(f"{self.pending_units} 个节奏点缺少读音，无法对齐")
        reasons.extend(self.generation_errors)
        if self.vocal is not None and self.vocal.state == "needs_choice":
            reasons.append("同目录存在多个人声文件，请先选择")
        elif (
            self.vocal is not None
            and self.vocal.state == "separation"
            and not self.separation_available
        ):
            # 分离可用时不阻断：执行阶段会自动分离（§6.1 ④）
            reasons.append(
                "没有可复用的人声，且分离组件未就绪：请点击「对齐环境 → "
                "安装 / 修复」自动补装（已装过环境只会补缺失组件），"
                "之后即可一键自动分离并对齐"
            )
        if self.runtime is not None and not self.runtime.available:
            reasons.append(self.runtime.message or "对齐运行环境不可用")
        if self.model is not None and not self.model.is_ready:
            reasons.append(self.model.message or "对齐模型未就绪")
        return reasons

    @property
    def ready(self) -> bool:
        return not self.blocking_reasons


class AiTimingService:
    """AI 打轴编排（standalone / embedded 共用）。"""

    def __init__(
        self,
        *,
        settings: AiTimingSettings,
        cache: AiCache,
        registry: Optional[ModelRegistry] = None,
        runtime: Optional[AiRuntimeManager] = None,
        vocal_service: Optional[VocalPreparationService] = None,
        resolver: Optional[PronunciationResolver] = None,
        worker_factory: Optional[Callable[[str], WorkerLike]] = None,
        separation_prober: Optional[Callable[[], bool]] = None,
        separation_follows_host: bool = False,
        separation_executor: Optional[
            Callable[[Path, ProgressFn, CancelFn], Path]
        ] = None,
        separation_identity: Callable[[], dict] = None,
    ):
        """Args:
            settings: 用户设置（模型/设备/Runtime/尾音等）。
            cache: AI 缓存根。
            registry / runtime / vocal_service / resolver：可注入，
                缺省按 settings 构建。
            worker_factory: 每次执行按 python 路径创建 worker 客户端
                （默认 AlignmentWorkerClient；测试可注入进程内假实现）。
            separation_executor: 人声缺失时执行分离
                ``(source_path, progress, cancel) -> vocal_path``；
                standalone 未配置时给出中文阻断（阶段 G 由宿主注入）。
            separation_identity: 当前生效的分离身份
                ``{"model": str, "stem": str, "params": dict}``
                （缓存键组成，embedded 跟随工作台设置）。
        """
        self._settings = settings
        self._cache = cache
        self._registry = registry or ModelRegistry(resolve_model_root(settings))
        self._runtime = runtime or AiRuntimeManager()
        self._vocal_service = vocal_service or VocalPreparationService(cache)
        self._resolver = resolver or PronunciationResolver(
            chinese_mode=None
        )
        self._worker_factory = worker_factory or self._default_worker_factory
        self._separation_prober = separation_prober
        self._separation_follows_host = separation_follows_host
        self._separation_executor = separation_executor
        self._separation_identity = separation_identity or (
            lambda: {"model": "unknown", "stem": "人声", "params": {}}
        )

    # ── 基础解析 ──

    @property
    def effective_model_id(self) -> str:
        if self._settings.provider != "wav2vec2":
            return "MMS_FA"
        return self._settings.wav2vec2_model_id or DEFAULT_WAV2VEC2_MODEL

    def _worker_python(self) -> str:
        return self._settings.runtime_python or ""

    @staticmethod
    def _default_worker_factory(python_exe: str = "") -> WorkerLike:
        from strange_uta_game.backend.application.ai_timing.worker.client import (
            AlignmentWorkerClient,
        )

        return AlignmentWorkerClient(python_exe=python_exe or None)

    # ── 状态快照 ──

    def snapshot(
        self,
        project: Optional[Project],
        audio_path: Optional[str],
        *,
        probe_runtime: bool = True,
    ) -> AiTimingSnapshot:
        snap = AiTimingSnapshot()
        snap.audio_ok = bool(audio_path) and Path(audio_path).is_file()
        snap.audio_path = str(audio_path or "")
        snap.project_ok = project is not None
        if project is not None:
            snap.has_content = any(
                len(s.characters) > 0 for s in project.sentences
            )

        plan: Optional[PronunciationPlan] = None
        if project is not None and snap.has_content:
            plan = self._resolver.resolve_project(project, fill_missing=True)
            snap.pending_units = len(plan.pending_units)
            snap.generation_errors = list(plan.generation_errors)

        if snap.audio_ok:
            identity = self._separation_identity()
            snap.vocal = self._vocal_service.find_vocal(
                Path(audio_path),
                media_sha256=sha256_of_path(Path(audio_path)),
                separation_model=str(identity.get("model", "")),
                stem=str(identity.get("stem", "")),
                params=identity.get("params"),
            )
        if probe_runtime:
            snap.runtime = self._runtime.probe(self._worker_python())
        if self._settings.provider == "mms_fa":
            snap.model = ModelStatus(state="ok", message="随对齐环境自动获取")
        else:
            snap.model = self._registry.validate(self.effective_model_id)
        snap.separation_follows_host = self._separation_follows_host
        if self._separation_prober is not None:
            try:
                snap.separation_available = bool(self._separation_prober())
            except Exception:
                snap.separation_available = False
        else:
            snap.separation_available = (
                self._separation_executor is not None
            )
        snap.cache_root = self._cache.root
        return snap

    # ── 执行 ──

    def execute(
        self,
        project: Project,
        audio_path: str,
        *,
        on_progress: Optional[ProgressFn] = None,
        is_cancelled: Optional[CancelFn] = None,
        vocal_choice: Optional[Path] = None,
    ) -> ApplyAiTimingCommand:
        """完整执行并返回待应用命令（调用方负责 CommandManager 执行）。

        Raises:
            AiTimingError: 任一前置条件不满足或执行失败（中文消息）。
        """
        progress = on_progress or (lambda s, p, m: None)
        cancel = is_cancelled or (lambda: False)

        def _check_cancel():
            if cancel():
                raise AiTimingError("已取消 AI 打轴")

        # §8.1-1/3：工程与音频
        if project is None or not any(
            len(s.characters) > 0 for s in project.sentences
        ):
            raise AiTimingError("当前工程没有可对齐的歌词正文")
        if not audio_path or not Path(audio_path).is_file():
            raise AiTimingError("音频文件不可用，请先加载音频")
        vocal_source = Path(audio_path)
        if not vocal_source.is_file():
            raise AiTimingError(f"音频文件不存在：{audio_path}")

        # §8.1-6/7：工作目录清理与冲突检查（对话框单任务互斥由 UI 保证）
        try:
            self._cache.clean_work()
        except Exception:
            pass

        # §8.1-5：对齐 Runtime 与模型快检（完整探测在弹窗快照里做；
        # 这里只做快速存在性/注册表校验，避免点击后才在 worker 里失败）
        if self._settings.provider == "mms_fa":
            # MMS_FA 是 torchaudio bundle：由 worker 加载时自动获取，
            # 不走受控模型注册表（那套只服务 HF 仓模型）
            pass
        else:
            model_status = self._registry.validate(self.effective_model_id)
            if not model_status.is_ready:
                raise AiTimingError(
                    f"对齐模型未就绪：{model_status.message}。请先在弹窗中下载模型"
                )
        runtime_python = self._worker_python()
        if runtime_python and not Path(runtime_python).is_file():
            raise AiTimingError(
                f"对齐运行环境解释器不存在：{runtime_python}。请重新安装对齐环境"
            )

        # §8.1-6：磁盘空间预检（模型 ~1.3GB + 分离人声 wav + 对齐缓存，余量按 3GB）
        import shutil as _shutil

        try:
            free = _shutil.disk_usage(vocal_source.anchor).free
            if free < 3 * 1024 * 1024 * 1024:
                raise AiTimingError(
                    f"磁盘剩余空间不足（可用 {free // 1024 // 1024 // 1024}GB，"
                    "至少需要 3GB 用于模型/人声与缓存）"
                )
        except AiTimingError:
            raise
        except Exception:
            pass  # 空间查询失败不阻断

        # §8.1-2：标注解析（缺口在此时阻断）
        progress("prepare", 2, "分析歌词标注")
        plan = self._resolver.resolve_project(project, fill_missing=True)
        if plan.generation_errors:
            raise AiTimingError("；".join(plan.generation_errors))
        if plan.pending_units:
            u = plan.pending_units[0]
            raise AiTimingError(
                f"第 {u.line_idx + 1} 行第 {u.char_idx + 1} 个字符"
                f"「{u.char_text}」缺少读音，无法对齐"
            )

        # 媒体指纹
        progress("fingerprint", 5, "校验音频内容")
        media_sha = sha256_of_path(vocal_source)
        _check_cancel()

        # 人声准备（§8.2-4）
        identity = self._separation_identity()
        separation_model = str(identity.get("model", ""))
        stem = str(identity.get("stem", ""))
        params = identity.get("params") or {}
        progress("vocal", 8, "查找可复用人声")
        candidate = self._vocal_service.find_vocal(
            vocal_source,
            media_sha256=media_sha,
            separation_model=separation_model,
            stem=stem,
            params=params,
            explicit_choice=vocal_choice,
        )
        if candidate.state == "needs_choice":
            raise AiTimingError(
                "原音频同目录存在多个人声文件，请在弹窗中选择要使用的一个",
                vocal_choices=candidate.choices,
            )
        if candidate.state == "separation":
            if self._separation_executor is None:
                raise AiTimingError(
                    "没有可复用的人声。请先在人声分离页完成分离，"
                    "或配置分离能力后重试"
                )
            progress("vocal", 12, "执行人声分离")
            vocal_path = self._separation_executor(
                vocal_source, progress, cancel
            )
            _check_cancel()
            candidate = VocalCandidate(
                state="separated",
                path=self._vocal_service.register_separated_vocal(
                    vocal_source,
                    media_sha256=media_sha,
                    separation_model=separation_model,
                    stem=stem,
                    params=params,
                    vocal_path=Path(vocal_path),
                ),
                source_detail="本次分离的人声",
            )
        vocal_path = candidate.path
        if vocal_path is None or not Path(vocal_path).is_file():
            raise AiTimingError("人声文件不可用")
        _check_cancel()

        # 对齐请求
        progress("prepare", 15, "构建对齐请求")
        # audio_speed（音频倍速预处理）尚未实现：不进选项与缓存键，
        # 避免用户以为设置生效（实现于 worker 后再接入）
        options = {
            "tail_snap": self._settings.tail_snap,
        }
        request = build_alignment_request(
            plan,
            media=None,
            options=options,
        )

        # 对齐缓存命中 → 跳过推理
        cache_meta = alignment_cache_metadata(
            media_sha256=media_sha,
            alignment_model=self.effective_model_id,
            annotation_digest=plan.annotation_digest,
            options=options,
        )
        progress("cache", 18, "检查对齐缓存")
        cached = self._cache.lookup_alignment(cache_meta)
        if cached is not None:
            result = self._result_from_cache(cached, request)
            progress("align", 100, "命中对齐缓存")
            return self._build_command(project, plan, request, result)

        # 模型路径（已下载 → 本地目录；否则交给 provider 按 id 处理）
        local_model = self._registry.resolve_model_path(self.effective_model_id)
        model_spec = {
            "provider": self._settings.provider,
            "model_id": str(local_model) if local_model else self.effective_model_id,
            "device": self._settings.device,
        }

        worker = self._worker_factory(self._worker_python())
        progress("align", 20, "启动对齐进程")
        _check_cancel()

        def _map_worker_progress(stage: str, percent: int, message: str) -> None:
            # worker 的 load/align 全程映射到整体 20-95 区间
            mapped = 20 + int(percent * 0.75)
            progress("align", min(95, mapped), message)

        try:
            result = worker.run(
                request,
                audio_path=str(vocal_path),
                model_spec=model_spec,
                on_progress=_map_worker_progress,
            )
        except Exception as exc:  # worker 层已转换中文错误
            if "取消" in str(exc):
                raise AiTimingError(str(exc)) from exc
            raise AiTimingError(f"AI 打轴执行失败：{exc}") from exc
        _check_cancel()

        # 校验与缓存
        progress("apply", 96, "校验对齐结果")
        try:
            validate_result(result, request)
        except AlignmentValidationError as exc:
            raise AiTimingError(f"对齐结果未通过校验：{exc}") from exc
        self._cache.store_alignment(
            cache_meta,
            {
                "schema_version": result.schema_version,
                "annotation_digest": result.annotation_digest,
                "model_id": result.model_id,
                "spans": [
                    {
                        "token_index": s.token_index,
                        "start_ms": s.start_ms,
                        "end_ms": s.end_ms,
                        "score": s.score,
                    }
                    for s in result.spans
                ],
            },
        )
        progress("apply", 99, "准备应用结果")
        return self._build_command(project, plan, request, result)

    # ── 内部 ──

    @staticmethod
    def _result_from_cache(
        payload: dict, request: AlignmentRequest
    ) -> AlignmentResult:
        try:
            result = AlignmentResult(
                schema_version=int(payload.get("schema_version", 1)),
                annotation_digest=str(payload.get("annotation_digest", "")),
                model_id=str(payload.get("model_id", "")),
                spans=[
                    EmissionSpan(
                        token_index=int(s["token_index"]),
                        start_ms=int(s["start_ms"]),
                        end_ms=int(s["end_ms"]),
                        score=float(s.get("score", 1.0)),
                    )
                    for s in payload.get("spans", [])
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AiTimingError(f"对齐缓存数据无效，请重新执行：{exc}") from exc
        validate_result(result, request)
        return result

    @staticmethod
    def _build_command(
        project: Project,
        plan: PronunciationPlan,
        request: AlignmentRequest,
        result: AlignmentResult,
    ) -> ApplyAiTimingCommand:
        # 应用前最后一次漂移检查（构建命令内部 execute 时仍会复核）
        if compute_annotation_digest(project) != plan.annotation_digest:
            raise AiTimingError(
                "工程标注在执行期间发生了变化，请重新执行 AI 打轴"
            )
        return ApplyAiTimingCommand(project, plan, request, result)


__all__ = [
    "AiTimingError",
    "AiTimingService",
    "AiTimingSnapshot",
    "WorkerLike",
]
