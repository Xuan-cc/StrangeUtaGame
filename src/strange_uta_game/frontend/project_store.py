"""统一数据中心。

ProjectStore 是整个前端的唯一数据来源，替代之前的信号链同步模式。
所有 UI 模块订阅 ``data_changed`` 信号，根据 change_type 决定是否刷新自身。
所有数据变更后调用 ``store.notify(change_type)``，由 store 统一广播并自动保存。
"""

import sys

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from typing import Optional
from pathlib import Path

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.persistence.sug_parser import (
    SugProjectParser,
)


def _get_config_dir() -> Path:
    """获取配置文件目录（与 AppSettings.get_config_dir 逻辑一致）。

    优先使用程序目录下的 .config_redirect 文件指定的自定义位置，
    否则默认为程序所在目录。
    """
    program_dir = Path(sys.argv[0]).resolve().parent
    redirect_file = program_dir / ".config_redirect"
    if redirect_file.exists():
        try:
            custom_dir = Path(redirect_file.read_text(encoding="utf-8").strip())
            if custom_dir.is_dir():
                return custom_dir
        except Exception:
            pass
    return program_dir


# 配置目录（与 AppSettings 一致）
_CONFIG_DIR = _get_config_dir()
_UNTITLED_TEMP = _CONFIG_DIR / "untitled.sug.temp"


class ProjectStore(QObject):
    """统一数据中心 — 替代信号链的集中式数据管理。

    Change types:
        "project"      — 项目加载/创建（全量刷新）
        "audio"        — 音频路径变更
        "rubies"       — 注音变更
        "singers"      — 演唱者变更
        "lyrics"       — 歌词文本/字符变更
        "timetags"     — 时间标签变更
        "checkpoints"  — 节奏点变更
        "settings"     — 应用设置变更
    """

    # 单一变更通知信号
    data_changed = pyqtSignal(str)  # change_type

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

        self._project: Optional[Project] = None
        self._save_path: Optional[str] = None
        self._audio_path: Optional[str] = None
        self._dirty = False

        # 防抖 auto-save（2 秒无操作后写临时文件）
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(2000)
        self._auto_save_timer.timeout.connect(self._do_auto_save)

        # 定时 auto-save（周期性保存到 .sug.temp，用于闪退恢复）
        self._periodic_save_timer = QTimer(self)
        self._periodic_save_timer.setInterval(5 * 60 * 1000)  # 默认 5 分钟
        self._periodic_save_timer.timeout.connect(self._do_periodic_save)
        self._periodic_save_enabled = True

    # ── 属性 ──────────────────────────────────────

    @property
    def project(self) -> Optional[Project]:
        return self._project

    @property
    def save_path(self) -> Optional[str]:
        return self._save_path

    @property
    def audio_path(self) -> Optional[str]:
        return self._audio_path

    def set_audio_path(self, path: Optional[str]) -> None:
        """设置音频路径并广播变更。"""
        self._audio_path = path
        self.data_changed.emit("audio")

    @property
    def dirty(self) -> bool:
        return self._dirty

    # ── 项目生命周期 ─────────────────────────────

    def load_project(
        self,
        project: Project,
        save_path: Optional[str] = None,
        audio_path: Optional[str] = None,
    ) -> None:
        """加载（或替换）当前项目。

        所有 UI 模块应在收到 ``data_changed("project")`` 后全量刷新。
        """
        self._project = project
        self._save_path = save_path
        if audio_path is not None:
            self._audio_path = audio_path
        self._dirty = False
        self._start_periodic_save()
        self.data_changed.emit("project")

    def close_project(self) -> None:
        """关闭当前项目。"""
        self._auto_save_timer.stop()
        self._periodic_save_timer.stop()
        self._project = None
        self._save_path = None
        self._dirty = False

    # ── 变更通知 ─────────────────────────────────

    def notify(self, change_type: str) -> None:
        """通知数据已变更 — 广播 + 调度 auto-save。

        各 UI 模块在修改 domain 对象后调用此方法，
        而非自行发射独立信号。
        """
        self._dirty = True
        self._schedule_auto_save()
        self.data_changed.emit(change_type)

    # ── 保存 ─────────────────────────────────────

    def save(self, path: Optional[str] = None) -> bool:
        """手动保存项目到指定路径。

        Args:
            path: 保存路径。如果为 None 使用上次路径。

        Returns:
            是否成功。
        """
        if not self._project:
            return False

        target = path or self._save_path
        if not target:
            return False

        try:
            SugProjectParser.save(self._project, target)
            self._save_path = target
            self._dirty = False
            return True
        except Exception:
            return False

    # ── 定时 auto-save 配置 ──────────────────────

    def set_periodic_save_config(self, enabled: bool, interval_minutes: int) -> None:
        """配置定时自动保存参数。

        Args:
            enabled: 是否启用定时自动保存。
            interval_minutes: 保存间隔（分钟），范围 1~60。
        """
        self._periodic_save_enabled = enabled
        interval_ms = max(1, min(60, interval_minutes)) * 60 * 1000
        self._periodic_save_timer.setInterval(interval_ms)
        if self._project:
            self._start_periodic_save()

    # ── auto-save（内部） ────────────────────────

    def _schedule_auto_save(self) -> None:
        """重置防抖定时器。"""
        if self._project and self._save_path:
            self._auto_save_timer.start()

    def _do_auto_save(self) -> None:
        """执行 auto-save 到 ``<原路径>.autosave``。"""
        if not self._project or not self._save_path:
            return

        autosave_path = self._save_path + ".autosave"
        try:
            SugProjectParser.save(self._project, autosave_path)
        except Exception:
            pass  # auto-save 静默失败

    # ── 定时 auto-save（内部） ───────────────────

    def _start_periodic_save(self) -> None:
        """启动或重启定时自动保存。"""
        self._periodic_save_timer.stop()
        if self._periodic_save_enabled and self._project:
            self._periodic_save_timer.start()

    def _do_periodic_save(self) -> None:
        """执行定时保存到 .sug.temp 文件。

        有 save_path → ``<save_path>.temp``
        无 save_path → ``~/.strange_uta_game/untitled.sug.temp``
        """
        if not self._project:
            return

        temp_path = self.get_temp_path()
        try:
            _CONFIG_DIR.mkdir(exist_ok=True)
            SugProjectParser.save(self._project, str(temp_path))
        except Exception:
            pass  # 定时保存静默失败

    def get_temp_path(self) -> Path:
        """返回当前项目的临时保存路径。"""
        if self._save_path:
            return Path(self._save_path + ".temp")
        return _UNTITLED_TEMP

    def cleanup_temp_files(self) -> None:
        """删除当前项目关联的临时文件（含 .temp 与 .autosave，兼容旧 .autosave.sug）。"""
        temp = self.get_temp_path()
        try:
            if temp.exists():
                temp.unlink()
        except Exception:
            pass

        # 删除 autosave 文件（仅已保存项目才有）；兼容旧命名
        if self._save_path:
            for name in (
                self._save_path + ".autosave",
                self._save_path + ".autosave.sug",
            ):
                try:
                    p = Path(name)
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass

    @staticmethod
    def has_crash_recovery() -> bool:
        """检查是否有未命名项目的闪退恢复文件。"""
        return _UNTITLED_TEMP.exists()

    @staticmethod
    def load_crash_recovery() -> Optional[Project]:
        """加载闪退恢复文件。"""
        if not _UNTITLED_TEMP.exists():
            return None
        try:
            return SugProjectParser.load(str(_UNTITLED_TEMP))
        except Exception:
            return None

    @staticmethod
    def delete_crash_recovery() -> None:
        """删除闪退恢复文件。"""
        try:
            if _UNTITLED_TEMP.exists():
                _UNTITLED_TEMP.unlink()
        except Exception:
            pass
