"""仓库级测试配置。

把 pytest 的临时目录（tmp_path / tmp_path_factory 的根）重定向到仓库内
``.test_tmp``：

- 规避 Windows 上 ``%TEMP%\\pytest-of-<user>`` 被提权进程创建后、普通权限
  运行持续 ``PermissionError``（189 个 setup 错误的根因，删除该目录需要
  管理员权限，机器侧未必总能处理）；
- 顺带让测试的临时产物与系统临时目录解耦，便于排查与清理。

注意：显式 basetemp 会在每次会话开始时清空该目录，勿在其中放置需保留
的内容；已加入 .gitignore。
"""

from __future__ import annotations

from pathlib import Path


def pytest_configure(config):
    basetemp = Path(__file__).resolve().parents[1] / ".test_tmp"
    if not basetemp.exists():
        basetemp.mkdir(parents=True)
    config.option.basetemp = str(basetemp)
