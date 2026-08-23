"""跨平台用户数据目录解析（单一真源）。

各处需要落盘的模块都应经由本模块取目录，确保口径一致，并避免在只读位置写入。

设计：
- **Windows / Linux**：保持原「便携」行为 —— 配置与缓存都写在程序所在目录，
  并支持程序目录下的 ``.config_redirect`` 把配置重定向到自定义位置。
- **macOS**：程序被装进只读的 ``.app`` bundle（或经 Gatekeeper App Translocation
  从只读临时挂载点运行），程序目录不可写。改用系统约定的可写位置：
    * 配置 / 项目 → ``~/Library/Application Support/StrangeUtaGame``
    * 缓存        → ``~/Library/Caches/StrangeUtaGame``
    * 日志        → ``~/Library/Logs/StrangeUtaGame``
- **任意平台兜底**：若上述目录最终不可写（例如 Windows 装在 ``Program Files`` 且
  无写权限），回退到 ``~/.strange_uta_game``（及其 ``cache`` / ``logs`` 子目录），
  保证程序绝不因目录不可写而崩溃。

注意：可写性以「能否真正建文件」为准（试写探针），而不是 ``mkdir(exist_ok=True)``
是否成功 —— 只读 bundle 内目标目录已存在，``mkdir`` 不会报错，但写入会失败。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

APP_NAME = "StrangeUtaGame"
_FALLBACK_ROOT = Path.home() / ".strange_uta_game"
_REDIRECT_FILENAME = ".config_redirect"


def program_dir() -> Path:
    """可执行文件（开发期为源码树根目录）所在目录。

    冻结（PyInstaller）环境下取 ``sys.executable``；源码运行时以本文件
    位置推导源码树根（``src/`` 的上一级），不再依赖 ``sys.argv[0]``——
    后者在 ``python -m pytest`` 等场景指向 site-packages 里的 runner 路径，
    会使配置目录的可写性探针写错位置（个别机器上对受保护目录的探针
    创建请求会长期阻塞，表现为测试无限卡死）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _is_dir_writable(d: Path) -> bool:
    """目录是否「真正可写」：能创建则建之，再用试写探针验证写权限。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    try:
        # 仅探测写权限：临时文件用完即删，不留痕迹。
        with tempfile.TemporaryFile(dir=str(d)):
            pass
        return True
    except OSError:
        return False


def _first_writable(*candidates: Path) -> Path:
    """返回首个可写目录；全不可写时返回最后一个候选（已尽力 mkdir，交由调用方兜底）。"""
    for c in candidates:
        if _is_dir_writable(c):
            return c
    last = candidates[-1]
    try:
        last.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return last


def redirect_marker_path() -> Path:
    """``.config_redirect`` 标记文件位置（始终在程序目录，保持便携语义）。"""
    return program_dir() / _REDIRECT_FILENAME


def _read_redirect() -> Optional[Path]:
    marker = redirect_marker_path()
    try:
        if marker.exists():
            custom = Path(marker.read_text(encoding="utf-8").strip())
            if custom.is_dir():
                return custom
    except OSError:
        pass
    return None


def config_dir() -> Path:
    """配置 / 项目目录（已确保存在且可写）。

    优先级：``SUG_CONFIG_DIR`` 环境变量 > ``.config_redirect`` > 平台默认
    > ``~/.strange_uta_game`` 兜底。环境变量最高优先，供测试 / 嵌入宿主
    把配置重定向到隔离位置（与 ``SUG_CACHE_DIR`` / ``SUG_BACKUP_DIR`` 口径
    一致，避免测试读写开发者的真实 config.json）。
    """
    env_dir = os.environ.get("SUG_CONFIG_DIR")
    if env_dir:
        return _first_writable(Path(env_dir), _FALLBACK_ROOT)
    redirected = _read_redirect()
    if redirected is not None:
        return _first_writable(redirected, _FALLBACK_ROOT)
    if sys.platform == "darwin":
        return _first_writable(
            Path.home() / "Library" / "Application Support" / APP_NAME,
            _FALLBACK_ROOT,
        )
    return _first_writable(program_dir(), _FALLBACK_ROOT)


def cache_dir() -> Path:
    """缓存目录（已确保存在且可写）。``SUG_CACHE_DIR`` 环境变量最高优先。"""
    env_dir = os.environ.get("SUG_CACHE_DIR")
    if env_dir:
        return _first_writable(Path(env_dir), _FALLBACK_ROOT / "cache")
    if sys.platform == "darwin":
        return _first_writable(
            Path.home() / "Library" / "Caches" / APP_NAME,
            _FALLBACK_ROOT / "cache",
        )
    return _first_writable(program_dir() / ".cache", _FALLBACK_ROOT / "cache")


def logs_dir() -> Path:
    """日志目录（已确保存在且可写）。

    ``SUG_LOGS_DIR`` 环境变量最高优先，与 ``SUG_CONFIG_DIR`` /
    ``SUG_CACHE_DIR`` 同口径：嵌入宿主把日志收进自己的数据目录
    （crash.log 与 ai_timing.log 都跟随本目录，不可写时回退
    ``~/.strange_uta_game/logs``）。
    """
    env_dir = os.environ.get("SUG_LOGS_DIR")
    if env_dir:
        return _first_writable(Path(env_dir), _FALLBACK_ROOT / "logs")
    if sys.platform == "darwin":
        return _first_writable(
            Path.home() / "Library" / "Logs" / APP_NAME,
            _FALLBACK_ROOT / "logs",
        )
    return _first_writable(program_dir() / "logs", _FALLBACK_ROOT / "logs")


def default_backup_dir() -> Path:
    """默认的项目备份根目录（``<config_dir>/ProjectBackup``）。

    备份与项目/配置同源：Windows/Linux 便携模式下在程序目录，macOS 在
    ``~/Library/Application Support``，最终兜底 ``~/.strange_uta_game``。
    """
    return config_dir() / "ProjectBackup"


def backup_dir(custom: Optional[str] = None) -> Path:
    """项目备份根目录（已确保存在且可写）。

    优先级：``SUG_BACKUP_DIR`` 环境变量 > ``custom``（用户设置）> 默认位置。

    Args:
        custom: 用户在设置中显式指定的备份位置；为空 / 不可写时回退到
            :func:`default_backup_dir`，再兜底到 ``~/.strange_uta_game/ProjectBackup``。
    """
    fallback = _FALLBACK_ROOT / "ProjectBackup"
    env_dir = os.environ.get("SUG_BACKUP_DIR")
    if env_dir:
        return _first_writable(Path(env_dir), default_backup_dir(), fallback)
    if custom and custom.strip():
        return _first_writable(Path(custom.strip()), default_backup_dir(), fallback)
    return _first_writable(default_backup_dir(), fallback)
