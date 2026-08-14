"""AI 打轴宿主协议（阶段 G，embedded 注入点）。

SUG standalone 运行时没有宿主（``None``），AI 打轴使用自身默认配置；
embedded 模式下由工作台在 ``for_embedding(..., ai_timing_host=...)``
注入一个满足本协议的对象，提供：

- 工作台当前分离环境状态（§6.2：跟随工作台设置，不装第二份 Runtime）；
- 会话人声查找（本次会话已分离、与原音频匹配的人声 → 零分离复用）；
- 人声分离执行（缺人声时调用一次工作台现有分离任务）；
- AI 缓存根目录（宿主注入的 ``.cache`` 范围，§7.2）。

协议方法使用纯 Python 类型（dict/Path/callable），宿主实现不依赖
SUG 的任何导入——跨仓库契约只靠鸭子类型 + 本文档。
"""

from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

ProgressFn = Callable[[str, int, str], None]
CancelFn = Callable[[], bool]


@runtime_checkable
class AiTimingHost(Protocol):
    """工作台向 SUG AI 打轴注入的能力集合（§9 契约）。"""

    def separation_status(self) -> dict:
        """当前分离环境状态。

        返回 ``{"available": bool, "model": str, "message": str}``；
        message 为中文（不可用时给出可操作原因）。
        """
        ...

    def effective_identity(self) -> dict:
        """当前生效的人声分离身份（人声缓存键组成，§6.4）。

        返回 ``{"model": str, "stem": str, "params": dict}``；
        embedded 始终跟随工作台当前「分离人声」设置。
        """
        ...

    def find_session_vocal(
        self, source_path: Path, media_sha256: str
    ) -> Optional[Path]:
        """查找本次会话已分离、可与 source_path 匹配的人声文件。

        匹配以工作台会话产物记录为准（严格命名 + 会话内产出），
        找不到返回 None。
        """
        ...

    def separate_vocal(
        self,
        source_path: Path,
        on_progress: ProgressFn,
        is_cancelled: CancelFn,
    ) -> Path:
        """执行一次人声分离（阻塞；完成后返回人声文件路径）。

        取消：``is_cancelled`` 返回 True 时应尽快停止并抛出异常
        （消息含「取消」）；失败抛异常（中文消息）。
        """
        ...

    def ai_cache_dir(self) -> Path:
        """SUG AI 缓存根目录（宿主 ``.cache`` 范围内）。"""
        ...

    def runtime_python(self) -> Optional[str]:
        """（可选，方案 B）宿主托管 Runtime 的 python.exe 路径。

        SUG 检测到该方法且路径存在时，「安装/修复」改为向该解释器
        **增量**安装 AI 依赖（不建 venv、不重装 torch，torchaudio 按
        其 torch 版本自动配对）；返回 None / 路径不存在时：嵌入模式
        引导去宿主分离页安装，或经确认后独立安装兜底。能力发现用
        ``getattr``，不实现也不影响协议。
        """
        ...

    def open_separation_page(self) -> bool:
        """（可选）跳转到宿主的分离环境页（如工作台第 2 步）。

        嵌入模式下宿主 Runtime 未安装时，SUG 弹窗展示「去安装」入口，
        点击后调用本方法完成页面跳转；返回 False / 未实现时 SUG 回落
        为文字提示。
        """
        ...


def is_ai_timing_host(obj: object) -> bool:
    """运行时判定对象是否满足宿主协议（全部方法存在即可）。"""
    if obj is None:
        return False
    required = (
        "separation_status",
        "effective_identity",
        "find_session_vocal",
        "separate_vocal",
        "ai_cache_dir",
    )
    return all(hasattr(obj, name) for name in required)


__all__ = ["AiTimingHost", "is_ai_timing_host", "ProgressFn", "CancelFn"]
