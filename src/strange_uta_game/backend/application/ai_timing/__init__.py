"""AI 打轴应用层模块。

阶段 A：标注优先的发音计划（PronunciationPlan）与解析器
（PronunciationResolver）。后续阶段（对齐 worker、模型管理、缓存、
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

__all__ = [
    "PronunciationPlan",
    "PronunciationSource",
    "PronunciationUnit",
    "ScriptKind",
    "PronunciationResolver",
    "ProjectDriftError",
    "compute_annotation_digest",
    "script_kind_of",
]
