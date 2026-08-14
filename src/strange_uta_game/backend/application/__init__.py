"""Application layer."""

from .command_manager import CommandManager
from .project_service import ProjectService, ProjectCallbacks, ProjectServiceError
from .auto_check_service import AutoCheckService, AutoCheckResult, is_chinese_lyrics
from .singer_service import SingerService, SingerCallbacks
from .export_service import ExportService, ExportResult
# timing_service 依赖 PyQt6：无头环境（AI Runtime worker）没有 Qt，
# 导入失败不阻断整个 application 包（worker 只需要纯逻辑服务）
try:
    from .timing_service import TimingService, TimingCallbacks, CheckpointPosition
except ImportError:  # pragma: no cover - 无 Qt 环境
    TimingService = None  # type: ignore[assignment,misc]
    TimingCallbacks = None  # type: ignore[assignment,misc]
    CheckpointPosition = None  # type: ignore[assignment,misc]
from .project_import_service import ProjectImportService, ProjectImportError
from .calibration_service import (
    compute_tap_offset_ms,
    filtered_average_offset_ms,
)
from .ai_timing import (
    PronunciationPlan,
    PronunciationSource,
    PronunciationUnit,
    ScriptKind,
    PronunciationResolver,
    ProjectDriftError,
    AlignmentRequest,
    AlignmentResult,
    AlignmentValidationError,
    ApplyAiTimingCommand,
)
from .commands import (
    Command,
    BatchCommand,
    CommandState,
    AddTimeTagCommand,
    RemoveTimeTagCommand,
    ClearLineTimeTagsCommand,
    UpdateCharacterCommand,
    AddRubyCommand,
    RemoveRubyCommand,
    AddSentenceCommand,
    RemoveSentenceCommand,
    AddSingerCommand,
    RemoveSingerCommand,
)

__all__ = [
    "CommandManager",
    "ProjectService",
    "ProjectCallbacks",
    "ProjectServiceError",
    "AutoCheckService",
    "AutoCheckResult",
    "is_chinese_lyrics",
    "SingerService",
    "SingerCallbacks",
    "ExportService",
    "ExportResult",
    "TimingService",
    "TimingCallbacks",
    "CheckpointPosition",
    "ProjectImportService",
    "ProjectImportError",
    "compute_tap_offset_ms",
    "filtered_average_offset_ms",
    "PronunciationPlan",
    "PronunciationSource",
    "PronunciationUnit",
    "ScriptKind",
    "PronunciationResolver",
    "ProjectDriftError",
    "AlignmentRequest",
    "AlignmentResult",
    "AlignmentValidationError",
    "ApplyAiTimingCommand",
    "Command",
    "BatchCommand",
    "CommandState",
    "AddTimeTagCommand",
    "RemoveTimeTagCommand",
    "ClearLineTimeTagsCommand",
    "UpdateCharacterCommand",
    "AddRubyCommand",
    "RemoveRubyCommand",
    "AddSentenceCommand",
    "RemoveSentenceCommand",
    "AddSingerCommand",
    "RemoveSingerCommand",
]
