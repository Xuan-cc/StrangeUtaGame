"""Application layer."""

from .command_manager import CommandManager
from .project_service import ProjectService, ProjectCallbacks, ProjectServiceError
from .auto_check_service import AutoCheckService, AutoCheckResult
from .singer_service import SingerService, SingerCallbacks
from .export_service import ExportService, ExportResult
from .timing_service import TimingService, TimingCallbacks, CheckpointPosition
from .project_import_service import ProjectImportService, ProjectImportError
from .calibration_service import (
    compute_tap_offset_ms,
    filtered_average_offset_ms,
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
