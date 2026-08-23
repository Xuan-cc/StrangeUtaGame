"""AI 打轴模块统一日志。

整个 ai_timing 模块的关键事件——运行环境探测与安装、显卡/驱动检测、
模型下载、人声分离、推理执行、弹窗用户动作、worker 设备选择——统一
追加写入 ``logs/ai_timing.log``。用户在 AI 打轴弹窗点「日志」即可定位
到该文件，整个发回给开发者即可排查（install.log 只覆盖安装流水）。

设计口径：

- 一行一条：``[时间] [pid·来源] 消息``。主进程与 worker 子进程共写
  同一文件：追加模式、每次写入独立 open、单条消息截断到 4KB 以内，
  多进程交错最多撕开一行，可接受；靠 pid 前缀区分进程。
- 超过 2MB 轮转为 ``ai_timing.log.1``（与 crash.log 同风格）。
- worker 子进程跑在外部解释器里，它对日志目录的解析（frozen 包根在
  _MEIPASS）与宿主不同：宿主启动 worker 时把解析好的绝对路径放进
  ``SUG_AI_TIMING_LOG`` 环境变量（见 worker/client 的 _build_env），
  本模块优先读它。
- 未设 ``SUG_AI_TIMING_LOG`` 时回落 ``logs_dir()``——嵌入宿主可用
  ``SUG_LOGS_DIR`` 把日志收进自己的数据目录（与 crash.log 同目录，
  见 app_dirs / EMBEDDING.md §4）。
- 绝不抛异常：任何落盘失败都静默（日志不能反过来打断业务）。
- 高频进度不进本日志（pip 逐行输出、下载字节条）——安装的完整流水
  仍在 ``<ai_runtime>/install.log``，按进度回调低频采样落盘。
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

__all__ = ["ai_log_path", "ailog"]

LOG_FILENAME = "ai_timing.log"
_MAX_LOG_BYTES = 2_000_000
_MAX_LINE_BYTES = 4 * 1024
_LOG_PATH_ENV = "SUG_AI_TIMING_LOG"

_lock = threading.Lock()


def ai_log_path() -> Path:
    """统一日志文件路径（环境变量覆盖优先，供 worker 对齐宿主）。"""
    env = os.environ.get(_LOG_PATH_ENV, "").strip()
    if env:
        return Path(env)
    from strange_uta_game.app_dirs import logs_dir

    return logs_dir() / LOG_FILENAME


def ailog(source: str, message: str) -> None:
    """追加一条事件日志；任何失败静默。"""
    try:
        line = (
            f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{os.getpid()}·{source}] {message}"
        )
        line = line.replace("\n", " ⏎ ").replace("\r", "")
        encoded = line.encode("utf-8", errors="replace")
        if len(encoded) > _MAX_LINE_BYTES:
            line = (
                encoded[: _MAX_LINE_BYTES - 12].decode("utf-8", errors="ignore")
                + "…(截断)"
            )
        with _lock:
            path = ai_log_path()
            try:
                if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
                    rotated = path.with_name(LOG_FILENAME + ".1")
                    try:
                        rotated.unlink(missing_ok=True)
                        path.rename(rotated)
                    except OSError:
                        # 轮转失败（文件被占用）：截断重写保证可继续追加
                        path.write_text("", encoding="utf-8")
            except OSError:
                pass
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(line + "\n")
    except Exception:
        pass
