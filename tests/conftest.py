"""仓库级测试配置。

- 把 pytest 的临时目录（tmp_path / tmp_path_factory 的根）重定向到仓库内
  ``.test_tmp``：规避 Windows 上 ``%TEMP%\\pytest-of-<user>`` 被提权进程
  创建后、普通权限运行持续 ``PermissionError``；也让测试临时产物与系统
  临时目录解耦。注意显式 basetemp 会在会话开始时清空该目录（已 gitignore）。
- 统一 Qt 平台环境：固定 offscreen，单文件运行与全量运行行为一致（此前
  部分测试模块在导入时各自 setdefault，收集阶段即污染整个进程，导致会话
  QApplication 平台取决于"恰好导入了哪些模块"）；Windows 的 offscreen
  默认无字体（QFontDatabase 为空、systemFont 只回通用别名），显式指向
  系统字体目录恢复真实字体库。必须在任何 QApplication 创建前设置。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def pytest_configure(config):
    # 每次会话使用唯一临时目录：进程原生崩溃后旧目录可能被 Windows ACL
    # 锁死，固定目录名只会在仓库里累积不可清理的目录。
    import time as _time

    _SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(_SESSION_ROOT)


def pytest_sessionfinish(session, exitstatus):
    """会话结束时 best-effort 清理本会话临时目录（崩溃残留交人工清理）。"""
    import shutil

    shutil.rmtree(str(_SESSION_ROOT), ignore_errors=True)
    try:
        parent = _SESSION_ROOT.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    os.environ.setdefault(
        "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    )

# 配置目录重定向到 basetemp 下的隔离位置：测试不再读写仓库根的真实
# config.json（SUG_CONFIG_DIR 最高优先，见 app_dirs.config_dir）。
# basetemp 会被 pytest 在会话开始时清空重建，AppSettings 首次访问时自建。
import time as _time

_SESSION_ROOT = (
    Path(__file__).resolve().parents[1]
    / ".test_sessions"
    / f"s{os.getpid()}-{int(_time.time())}"
)


def _session_subdir(name: str) -> str:
    """会话子目录（复用模块级 _SESSION_ROOT，全程唯一）。"""
    return str(_SESSION_ROOT / name)


# 配置目录重定向到会话隔离位置（SUG_CONFIG_DIR 最高优先，见 app_dirs）
os.environ.setdefault("SUG_CONFIG_DIR", _session_subdir("config"))
# AI 打轴统一日志同样隔离（SUG_AI_TIMING_LOG 最高优先，见 ailog.ai_log_path）
os.environ.setdefault(
    "SUG_AI_TIMING_LOG",
    str(Path(_session_subdir("logs")) / "ai_timing.log"),
)


@pytest.fixture(autouse=True)
def _isolated_font_cache():
    """每个测试前后清空进程级字体缓存。

    不少测试会 monkeypatch ``QFontDatabase.families``；字体枚举走
    ``font_cache`` 快照后，上一个测试留下的快照会让 monkeypatch 失效。
    本地化字体名的磁盘扫描昂贵（Windows 约 1~2 秒）且没有测试 mock 它，
    保留其缓存不清（``clear_alias_map=False``）。
    """
    from strange_uta_game.frontend import font_cache

    font_cache.invalidate(clear_alias_map=False)
    yield
    font_cache.invalidate(clear_alias_map=False)
