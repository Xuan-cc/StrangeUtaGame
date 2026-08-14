"""AI 打轴应用层模块。

阶段 A：标注优先的发音计划（PronunciationPlan）与解析器
（PronunciationResolver）。
阶段 B：版本化对齐请求/结果 schema、token span 映射与原子写回命令
（ApplyAiTimingCommand）。后续阶段（对齐 worker、模型管理、缓存、
embedded 宿主契约）在此包内继续扩展。
"""

from .pronunciation import (
    PronunciationPlan,
    PronunciationSource,
    PronunciationUnit,
    ScriptKind,
    compute_annotation_digest,
    script_kind_of,
)
from .resolver import ProjectDriftError, PronunciationResolver
from .alignment import (
    SCHEMA_VERSION,
    AlignmentRequest,
    AlignmentResult,
    AlignmentToken,
    AlignmentValidationError,
    EmissionSpan,
    MediaIdentity,
    build_alignment_request,
    build_alignment_tokens,
    checkpoint_timestamps,
    interpolate_structural_timestamps,
    validate_result,
)
from .commands import ApplyAiTimingCommand
from .host import AiTimingHost, is_ai_timing_host
from .settings import (
    AiTimingSettings,
    default_model_root,
    load_ai_timing_settings,
    resolve_model_root,
    save_ai_timing_settings,
)
from .models import (
    ModelDownloadService,
    ModelDownloadTransport,
    ModelManifest,
    ModelRegistry,
    ModelRegistryError,
    ModelStatus,
    HfHubTransport,
)
from .runtime import AiRuntimeError, AiRuntimeManager, RuntimeStatus
from .vocals import (
    AiCache,
    AiCacheError,
    VocalCandidate,
    VocalPreparationService,
    alignment_cache_metadata,
    default_ai_cache_root,
    find_sibling_vocals,
    sha256_of_path,
    vocal_cache_metadata,
)

__all__ = [
    # 阶段 A
    "PronunciationPlan",
    "PronunciationSource",
    "PronunciationUnit",
    "ScriptKind",
    "PronunciationResolver",
    "ProjectDriftError",
    "compute_annotation_digest",
    "script_kind_of",
    # 阶段 B
    "SCHEMA_VERSION",
    "AlignmentRequest",
    "AlignmentResult",
    "AlignmentToken",
    "AlignmentValidationError",
    "EmissionSpan",
    "MediaIdentity",
    "build_alignment_request",
    "build_alignment_tokens",
    "checkpoint_timestamps",
    "interpolate_structural_timestamps",
    "validate_result",
    "ApplyAiTimingCommand",
    # 阶段 G
    "AiTimingHost",
    "is_ai_timing_host",
    # 阶段 D
    "AiTimingSettings",
    "default_model_root",
    "load_ai_timing_settings",
    "resolve_model_root",
    "save_ai_timing_settings",
    "ModelDownloadService",
    "ModelDownloadTransport",
    "ModelManifest",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelStatus",
    "HfHubTransport",
    "AiRuntimeError",
    "AiRuntimeManager",
    "RuntimeStatus",
    # 阶段 E
    "AiCache",
    "AiCacheError",
    "VocalCandidate",
    "VocalPreparationService",
    "alignment_cache_metadata",
    "default_ai_cache_root",
    "find_sibling_vocals",
    "sha256_of_path",
    "vocal_cache_metadata",
]
