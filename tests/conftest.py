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
    basetemp = Path(__file__).resolve().parents[1] / ".test_tmp"
    if not basetemp.exists():
        basetemp.mkdir(parents=True)
    config.option.basetemp = str(basetemp)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    os.environ.setdefault(
        "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    )

# 配置目录重定向到 basetemp 下的隔离位置：测试不再读写仓库根的真实
# config.json（SUG_CONFIG_DIR 最高优先，见 app_dirs.config_dir）。
# basetemp 会被 pytest 在会话开始时清空重建，AppSettings 首次访问时自建。
os.environ.setdefault(
    "SUG_CONFIG_DIR",
    str(Path(__file__).resolve().parents[1] / ".test_tmp" / "config"),
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
