"""领域层模块。

领域层定义核心业务概念和业务规则，是整个应用的核心。
它不依赖任何外部框架或库，纯 Python 实现。
"""

from .models import (
    DomainError,
    ValidationError,
    TimeTagType,
    Checkpoint,
    CheckpointConfig,
    TimeTag,
    Ruby,
    LineTimingInfo,
)

from .entities import (
    Singer,
    LyricLine,
)

from .project import (
    ProjectMetadata,
    Project,
)

__all__ = [
    # 错误
    "DomainError",
    "ValidationError",
    # 枚举
    "TimeTagType",
    # 值对象
    "Checkpoint",
    "CheckpointConfig",
    "TimeTag",
    "Ruby",
    "LineTimingInfo",
    # 实体
    "Singer",
    "LyricLine",
    # 聚合根
    "ProjectMetadata",
    "Project",
]
