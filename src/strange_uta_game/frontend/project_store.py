"""统一数据中心。

ProjectStore 是整个前端的唯一数据来源，替代之前的信号链同步模式。
所有 UI 模块订阅 ``data_changed`` 信号，根据 change_type 决定是否刷新自身。
所有数据变更后调用 ``store.notify(change_type)``，由 store 统一广播并自动保存。
"""

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from typing import Optional

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.persistence.sug_parser import (
    SugProjectParser,
)


class ProjectStore(QObject):
    """统一数据中心 — 替代信号链的集中式数据管理。

    Change types:
        "project"      — 项目加载/创建（全量刷新）
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
        self.data_changed.emit("project")

    def close_project(self) -> None:
        """关闭当前项目。"""
        self._auto_save_timer.stop()
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

    # ── auto-save（内部） ────────────────────────

    def _schedule_auto_save(self) -> None:
        """重置防抖定时器。"""
        if self._project and self._save_path:
            self._auto_save_timer.start()

    def _do_auto_save(self) -> None:
        """执行 auto-save 到 ``<原路径>.autosave.sug``。"""
        if not self._project or not self._save_path:
            return

        autosave_path = self._save_path + ".autosave.sug"
        try:
            SugProjectParser.save(self._project, autosave_path)
        except Exception:
            pass  # auto-save 静默失败
