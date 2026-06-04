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
from strange_uta_game.backend.infrastructure.persistence.sug_io import (
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


def _get_cache_dir() -> Path:
    """获取缓存目录（程序所在目录下的 .cache 文件夹）。
    
    与 video_converter.py 中的缓存目录保持一致。
    """
    program_dir = Path(sys.argv[0]).resolve().parent
    cache_dir = program_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


# 配置目录（与 AppSettings 一致）
_CONFIG_DIR = _get_config_dir()
_CACHE_DIR = _get_cache_dir()
_UNTITLED_TEMP = _CACHE_DIR / ".untitled.sug.temp"


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
        # 用户加载的原始媒体文件路径（音频直接路径 或 视频原始路径）。
        # 与 _audio_path 的区别：视频加载后 _audio_path 为 .cache 提取音频，
        # 而此字段始终存储原始文件路径，用于持久化到 .sug。
        self._original_media_path: Optional[str] = None
        # 最近一次被加载/导入的歌词文件所在目录（不持久化，仅运行时使用，
        # 优先级介于 audio 与 last_export_dir 之间）
        self._last_lyric_dir: Optional[str] = None
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
        """设置音频路径并广播变更。路径未变则不广播，避免回环。"""
        if self._audio_path == path:
            return
        self._audio_path = path
        # 音频是用户当前工作上下文 → 同步刷新默认目录到 config
        if path:
            self._persist_last_export_dir(str(Path(path).parent))
        self.data_changed.emit("audio")

    @property
    def original_media_path(self) -> Optional[str]:
        return self._original_media_path

    def set_original_media_path(self, path: Optional[str]) -> None:
        """设置原始媒体文件路径，值有变化时标记 dirty。

        用于用户手动加载音频/视频的场景。路径未变化（含自动恢复后的二次调用）则跳过。
        """
        if self._original_media_path == path:
            return
        self._original_media_path = path
        if self._project:
            self._dirty = True
            self._schedule_auto_save()

    def restore_media_path(self, path: Optional[str]) -> None:
        """静默恢复媒体路径，不标记 dirty。

        专用于从 .sug 文件自动恢复媒体路径的场景。恢复后再次调用
        set_original_media_path() 传入相同路径时会被当作 no-op，不会触发 dirty。
        """
        self._original_media_path = path

    def mark_dirty(self) -> None:
        """手动标记项目为已修改，并广播通知以刷新标题栏等订阅者。

        用于不经过 notify() 的外部变更场景（如 nicokara_tags 修改）。
        """
        if not self._project:
            return
        self._dirty = True
        self._schedule_auto_save()
        self.data_changed.emit("dirty")

    def get_saveable_media_path(self) -> Optional[str]:
        """返回可持久化的媒体路径，排除 .cache 临时路径。"""
        path = self._original_media_path
        if path and not self._is_in_cache_dir(path):
            return path
        return None

    # ── 工作目录（默认保存/导出位置） ─────────────

    @staticmethod
    def _is_in_cache_dir(path: Optional[str]) -> bool:
        """判断路径是否位于 .cache 临时目录下。"""
        if not path:
            return False
        try:
            return Path(path).resolve().is_relative_to(_CACHE_DIR.resolve())
        except (ValueError, OSError):
            return False

    def is_temp_save_path(self, path: Optional[str] = None) -> bool:
        """判断给定路径（或当前 _save_path）是否为 .cache 临时位置。

        临时项目的 save_path 不应作为默认保存目录返回给用户。
        """
        target = path if path is not None else self._save_path
        return self._is_in_cache_dir(target)

    @property
    def working_dir(self) -> str:
        """派生：当前工作目录。

        优先级：
          1. 已正式保存的项目目录（排除 .cache 临时项目）
          2. 音频文件所在目录
          3. 最近加载/导入的歌词文件所在目录
          4. settings["export.last_export_dir"]
          5. ""（让 Qt 用系统默认）
        """
        if self._save_path and not self.is_temp_save_path(self._save_path):
            parent = str(Path(self._save_path).parent)
            if parent and Path(parent).is_dir():
                return parent
        if self._audio_path and not self._is_in_cache_dir(self._audio_path):
            parent = str(Path(self._audio_path).parent)
            if parent and Path(parent).is_dir():
                return parent
        if self._last_lyric_dir and Path(self._last_lyric_dir).is_dir():
            return self._last_lyric_dir
        try:
            from strange_uta_game.frontend.settings.app_settings import AppSettings
            last = AppSettings().get("export.last_export_dir", "") or ""
        except Exception:
            last = ""
        if last and Path(last).is_dir():
            return last
        return ""

    def suggested_save_path(self, ext: str = ".sug") -> str:
        """根据 working_dir + 项目标题/音频名生成建议的保存全路径。

        若无可用目录则只返回建议文件名。
        """
        if not ext.startswith("."):
            ext = "." + ext
        # 选 base name
        base = ""
        if self._project and getattr(self._project, "metadata", None):
            title = getattr(self._project.metadata, "title", "") or ""
            if title.strip():
                base = title.strip()
        if not base and self._audio_path:
            base = Path(self._audio_path).stem
        if not base:
            base = "untitled"

        wd = self.working_dir
        if wd:
            return str(Path(wd) / f"{base}{ext}")
        return f"{base}{ext}"

    def set_working_dir(self, file_or_dir: str) -> None:
        """登记一个用户刚操作过的文件/目录，并持久化到 config。

        - 传入文件路径 → 取其 parent
        - 同时记录为最近歌词目录（用于歌词类型时的派生）
        - 写入 ``settings["export.last_export_dir"]`` 并立刻 save()
        """
        if not file_or_dir:
            return
        p = Path(file_or_dir)
        parent = str(p.parent) if p.suffix or p.is_file() else str(p)
        if not parent:
            return
        if not Path(parent).is_dir():
            return
        self._last_lyric_dir = parent
        self._persist_last_export_dir(parent)

    @staticmethod
    def _persist_last_export_dir(parent: str) -> None:
        """把目录写入 config.json 的 export.last_export_dir 并立即持久化。

        .cache 目录（含临时音频/临时项目）一律不写入，避免污染默认路径。
        """
        if not parent:
            return
        # 过滤 .cache 目录（临时提取的音频、临时项目都在这里）
        if ProjectStore._is_in_cache_dir(parent):
            return
        try:
            from strange_uta_game.frontend.settings.app_settings import AppSettings
            settings = AppSettings()
            current = settings.get("export.last_export_dir", "")
            if current == parent:
                return
            settings.set("export.last_export_dir", parent)
            settings.save()
        except Exception:
            pass  # 持久化失败不影响主流程

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
        # 清理旧项目的临时文件
        if self._project:
            self.cleanup_temp_files()
        
        self._project = project
        self._save_path = save_path
        if audio_path is not None:
            self._audio_path = audio_path
        self._original_media_path = None
        self._dirty = False
        self._start_periodic_save()
        self.data_changed.emit("project")

    def close_project(self) -> None:
        """关闭当前项目。"""
        self.cleanup_temp_files()
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
        # 设置和音频路径变更不算项目内容修改
        if change_type not in ("settings", "audio"):
            self._dirty = True
            self._schedule_auto_save()
        self.data_changed.emit(change_type)

    # ── 保存 ─────────────────────────────────────

    def _get_nicokara_tags_for_save(self) -> Optional[dict]:
        """读取当前 AppSettings 中的 nicokara_tags 用于持久化。"""
        try:
            from strange_uta_game.frontend.settings.app_settings import AppSettings
            tags = AppSettings().get("nicokara_tags")
            if tags:
                return dict(tags)
        except Exception:
            pass
        return None

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

        old_path = self._save_path
        try:
            SugProjectParser.save(
                self._project,
                target,
                nicokara_tags=self._get_nicokara_tags_for_save(),
                media_path=self.get_saveable_media_path(),
            )
            self._save_path = target
            self._dirty = False
            if old_path and old_path != target:
                self._cleanup_temp_for_path(old_path)
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
            SugProjectParser.save(
                self._project,
                autosave_path,
                nicokara_tags=self._get_nicokara_tags_for_save(),
                media_path=self.get_saveable_media_path(),
            )
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

        所有临时文件统一存放在程序目录的 .cache 文件夹下：
        - 已保存项目 → ``.cache/.项目名.sug.temp``
        - 未保存项目 → ``.cache/.untitled.sug.temp``
        """
        if not self._project:
            return

        temp_path = self.get_temp_path()
        try:
            _CACHE_DIR.mkdir(exist_ok=True)
            SugProjectParser.save(
                self._project,
                str(temp_path),
                nicokara_tags=self._get_nicokara_tags_for_save(),
                media_path=self.get_saveable_media_path(),
            )
        except Exception:
            pass  # 定时保存静默失败

    def get_temp_path(self) -> Path:
        """返回当前项目的临时保存路径（存放在 .cache 目录下）。"""
        if self._save_path:
            p = Path(self._save_path)
            # 使用项目文件名作为临时文件名，存放在 .cache 目录
            temp_filename = "." + p.name + ".temp"
            return _CACHE_DIR / temp_filename
        return _UNTITLED_TEMP

    def _cleanup_temp_for_path(self, save_path: str) -> None:
        """删除指定保存路径关联的临时文件（.cache/.xxx.sug.temp 与 autosave）。"""
        sp = Path(save_path)
        temp_name = "." + sp.name + ".temp"
        temp_path = _CACHE_DIR / temp_name
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass

        for name in (
            str(sp.parent / ("." + sp.name + ".autosave")),
            save_path + ".autosave",
            save_path + ".autosave.sug",
        ):
            try:
                fp = Path(name)
                if fp.exists():
                    fp.unlink()
            except Exception:
                pass

    def cleanup_temp_files(self) -> None:
        """删除当前项目关联的临时文件（含 .temp 与 .autosave，兼容旧命名）。"""
        temp = self.get_temp_path()
        try:
            if temp.exists():
                temp.unlink()
        except Exception:
            pass

        # 删除 autosave 文件（仅已保存项目才有）；兼容旧命名
        if self._save_path:
            p = Path(self._save_path)
            for name in (
                str(p.parent / ("." + p.name + ".autosave")),
                self._save_path + ".autosave",
                self._save_path + ".autosave.sug",
            ):
                try:
                    fp = Path(name)
                    if fp.exists():
                        fp.unlink()
                except Exception:
                    pass

    @staticmethod
    def has_crash_recovery() -> bool:
        """检查是否有闪退恢复文件（检查 .cache 目录下的所有 .sug.temp 文件）。"""
        return _UNTITLED_TEMP.exists() or any(_CACHE_DIR.glob(".*.sug.temp"))

    @staticmethod
    def load_crash_recovery() -> Optional[Project]:
        """加载闪退恢复文件（优先加载未命名项目的恢复文件）。"""
        # 优先检查未命名项目的恢复文件
        if _UNTITLED_TEMP.exists():
            try:
                return SugProjectParser.load(str(_UNTITLED_TEMP))
            except Exception:
                pass
        
        # 检查其他项目的恢复文件
        for temp_file in _CACHE_DIR.glob(".*.sug.temp"):
            try:
                return SugProjectParser.load(str(temp_file))
            except Exception:
                continue
        return None

    @staticmethod
    def delete_crash_recovery() -> None:
        """删除闪退恢复文件（删除 .cache 目录下的所有 .sug.temp 文件）。"""
        try:
            if _UNTITLED_TEMP.exists():
                _UNTITLED_TEMP.unlink()
        except Exception:
            pass
        
        # 删除其他项目的恢复文件
        for temp_file in _CACHE_DIR.glob(".*.sug.temp"):
            try:
                temp_file.unlink()
            except Exception:
                pass
