"""主窗口。

应用主容器，使用 PyQt-Fluent-Widgets 的 MSFluentWindow。
参考 March7thAssistant 的 UI 架构。
"""

from PyQt6.QtCore import Qt, QThread, QTimer, QEvent
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from qfluentwidgets import (
    NavigationItemPosition,
    MSFluentWindow,
    setThemeColor,
    NavigationBarPushButton,
    setTheme,
    Theme,
    FluentIcon as FIF,
)
from qfluentwidgets import InfoBar, InfoBarPosition, StateToolTip

from typing import Optional

from strange_uta_game.backend.application import CommandManager, TimingService
from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.audio import BassEngine, BassTsmEngine
from strange_uta_game.frontend.project_store import ProjectStore
from strange_uta_game.frontend.theme import theme


class MainWindow(MSFluentWindow):
    """主窗口 - MSFluentWindow 侧边栏导航架构"""

    def __init__(self):
        super().__init__()

        # 引擎按"启用高质量音频变速"设置选择：
        #   开（默认）→ BassTsmEngine：离线 TSM 预渲染，变速不变调、无爆音；
        #   关        → BassEngine：原版 BASS 实时变速，零缓存但可能爆音。
        self._audio_engine = self._make_audio_engine()
        self._command_manager = CommandManager()
        self._command_manager.set_on_state_changed(self._on_command_state_changed)
        self._timing_service = TimingService(self._audio_engine, self._command_manager)
        self._store = ProjectStore(self)

        # 异步保存相关
        self._save_thread: Optional[QThread] = None
        self._save_worker = None

        # 跟踪当前界面（用于 switchTo 自动应用修改，必须在 _init_navigation 之前）
        self._current_interface = None

        # 窗口大小/最大化记忆：使用独立的 AppSettings 实例，与设置页的编辑
        # 生命周期解耦；保存前会 reload，避免覆盖设置页刚写入的其它字段。
        from strange_uta_game.frontend.settings.app_settings import AppSettings
        self._win_settings = AppSettings()
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(400)
        self._geometry_save_timer.timeout.connect(self._save_window_geometry)

        self._init_window()
        self._init_interfaces()
        self._init_navigation()

        # 中央响应：store 的 project 变更 → 同步 timing_service 等
        self._store.data_changed.connect(self._on_data_changed)

        # 监听主题变化，更新 Win10 兜底背景色
        theme.changed.connect(self._on_theme_changed)

        # 全局 Ctrl+S 保存快捷键
        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self._on_global_save)

        # 初始化自动保存配置
        self._apply_auto_save_settings()


        # 延迟检查闪退恢复（等 UI 显示完毕后再弹窗）
        QTimer.singleShot(500, self._check_crash_recovery)

        # 延迟检查应用自动更新（在闪退恢复之后，避免抢占用户注意力）。
        # 失败/无网时静默跳过，绝不阻塞主流程。
        QTimer.singleShot(2500, self._check_for_app_update)

        # 启动期网络词典自动更新（独立于应用版本检查；HTTP 在后台线程跑，不阻塞 UI）
        QTimer.singleShot(3000, self._schedule_network_dict_auto_update)

        # 由 updater 流程主动设置；closeEvent 检测到此标志即 bypass dirty 弹窗，
        # 走"兜底保存 + 直接退出"路径。
        self._force_quitting = False

    @staticmethod
    def _find_icon_path() -> Optional[str]:
        """查找应用图标路径（兼容开发环境和 PyInstaller 打包环境）。"""
        import sys
        from pathlib import Path

        candidates = []
        # PyInstaller 打包后
        base = getattr(sys, "_MEIPASS", None)
        if base:
            candidates.append(Path(base) / "strange_uta_game" / "resource" / "icon.ico")
        # 开发环境：相对于本文件
        candidates.append(
            Path(__file__).resolve().parent.parent / "resource" / "icon.ico"
        )
        # 项目根目录
        candidates.append(Path(sys.argv[0]).resolve().parent / "icon.ico")

        for p in candidates:
            if p.exists():
                return str(p)
        return None

    def _init_window(self):
        """初始化窗口属性"""
        setThemeColor("#FF6B6B", lazy=True)
        # 使用主题管理器的设置，而不是硬编码
        if theme.is_dark:
            setTheme(Theme.DARK, lazy=True)
        else:
            setTheme(Theme.LIGHT, lazy=True)

        # Win10 兜底：Mica 材质不可用时，强制设置纯深色背景
        # 避免白底白字的灾难性渲染
        self._apply_win10_fallback_bg()

        self.setWindowTitle("StrangeUtaGame - 歌词打轴工具 Bilibili@不会说话的呆轩cc")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        # 设置窗口图标（左上角 + 任务栏）
        from PyQt6.QtGui import QIcon

        icon_path = self._find_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        # 居中
        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.move(
                (geometry.width() - self.width()) // 2,
                (geometry.height() - self.height()) // 2,
            )

        # 应用用户上次的窗口大小/最大化习惯（覆盖上面的默认尺寸）
        self._restore_window_geometry()

    def _restore_window_geometry(self):
        """启动时从 config.json 恢复窗口大小与最大化状态（仅读取一次）。

        读取 ``ui.window_size`` / ``ui.window_maximized``；字段缺失时维持
        :meth:`_init_window` 设定的默认尺寸（即不做任何改动）。
        """
        try:
            size = self._win_settings.get("ui.window_size", None)
            if isinstance(size, (list, tuple)) and len(size) == 2:
                self.resize(int(size[0]), int(size[1]))
                # 改变尺寸后重新居中
                screen = QApplication.primaryScreen()
                if screen is not None:
                    geo = screen.availableGeometry()
                    self.move(
                        (geo.width() - self.width()) // 2,
                        (geo.height() - self.height()) // 2,
                    )
            if self._win_settings.get("ui.window_maximized", False):
                self.showMaximized()
        except Exception:
            import logging
            logging.getLogger(__name__).warning("恢复窗口几何状态失败", exc_info=True)

    def _save_window_geometry(self):
        """把当前窗口大小与最大化状态实时写入 config.json。

        先 ``reload`` 取得磁盘上最新的完整配置（避免覆盖设置页刚保存的其它
        字段），再仅更新窗口两项后落盘。最大化/全屏时不覆盖 ``window_size``，
        以便退出最大化后仍能恢复用户习惯的普通窗口尺寸。
        """
        win_settings = getattr(self, "_win_settings", None)
        if win_settings is None:
            return
        try:
            maximized = self.isMaximized() or self.isFullScreen()
            win_settings.reload()
            win_settings.set("ui.window_maximized", maximized)
            if not maximized:
                win_settings.set("ui.window_size", [self.width(), self.height()])
            win_settings.save()
        except Exception:
            import logging
            logging.getLogger(__name__).warning("保存窗口几何状态失败", exc_info=True)

    def _schedule_geometry_save(self):
        """触发防抖保存（拖拽缩放过程中合并多次事件，停止后才写盘）。"""
        timer = getattr(self, "_geometry_save_timer", None)
        if timer is not None:
            timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_geometry_save()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._schedule_geometry_save()

    def _on_theme_changed(self):
        """主题变化时更新 Win10 兜底背景色"""
        self._apply_win10_fallback_bg()

    def _apply_win10_fallback_bg(self):
        """强制覆盖窗口背景色，确保强制主题模式下 Win11 Mica 不漏色。

        Win11 的 Mica 材质跟随 OS 系统主题，与应用内强制的浅/深色无关。
        若不显式覆盖背景，「强制浅色 + 系统深色」时 Mica 透出深色背景，
        导致整个窗口看起来仍是深色。深色模式同理。

        解决方案：无论深浅色均设置纯色背景，完全覆盖 Mica，保证
        强制模式和跟随系统模式下视觉一致。
        """
        if theme.is_dark:
            bg = theme.bg_primary.name()
            self.setStyleSheet(f"MSFluentWindow {{ background-color: {bg}; }}")
        else:
            # 浅色背景：用 #F9F9F9 而非空字符串，确保覆盖掉任何残留的
            # 深色 Mica 材质（强制浅色 + 系统深色的场景）。
            self.setStyleSheet("MSFluentWindow { background-color: #F9F9F9; }")

    def _init_interfaces(self):
        """初始化所有子界面"""
        from .home.home_interface import HomeInterface
        from .editor.timing_interface import EditorInterface
        from .export.export_interface import ExportInterface
        from .singer.singer_interface import SingerManagerInterface
        from .editor.fulltext_interface import RubyInterface
        from .settings.settings_interface import SettingsInterface
        from .editor.line_interface import EditInterface
        from .online.online_interface import OnlineQueryInterface

        self.homeInterface = HomeInterface(self)
        self.homeInterface.setObjectName("homeInterface")
        self.homeInterface.project_created.connect(self._on_project_created)
        self.homeInterface.project_opened.connect(self._on_project_opened)
        self.homeInterface.project_save_requested.connect(self._on_save_project)
        self.homeInterface.hide()  # 已废弃，仅保留信号连接

        self.editorInterface = EditorInterface(self)
        self.editorInterface.setObjectName("editorInterface")
        self.editorInterface.set_timing_service(self._timing_service)
        self.editorInterface.project_saved.connect(self._update_title)

        self.exportInterface = ExportInterface(self)
        self.exportInterface.setObjectName("exportInterface")

        self.singerInterface = SingerManagerInterface(self)
        self.singerInterface.setObjectName("singerInterface")

        self.rubyInterface = RubyInterface(self)
        self.rubyInterface.setObjectName("rubyInterface")
        self.rubyInterface.hide()  # 已废弃，仅保留与 Timing 共用的功能

        self.settingInterface = SettingsInterface(self)
        self.settingInterface.setObjectName("settingInterface")

        self.editViewInterface = EditInterface(self)
        self.editViewInterface.setObjectName("editViewInterface")
        self.editViewInterface.hide()  # 已废弃，仅保留与 Timing 共用的功能

        self.onlineInterface = OnlineQueryInterface(self)
        self.onlineInterface.setObjectName("onlineInterface")
        self.onlineInterface.hide()  # 已废弃，占位界面

        # 将 store 传递给所有子界面
        self.homeInterface.set_store(self._store)
        self.editorInterface.set_store(self._store)
        self.editViewInterface.set_store(self._store)
        self.exportInterface.set_store(self._store)
        self.singerInterface.set_store(self._store)
        self.rubyInterface.set_store(self._store)
        self.settingInterface.set_store(self._store)

        # 初始广播设置，确保 EditorInterface 等在启动时就能读取设置值
        self._store.notify("settings")

    def _init_navigation(self):
        """初始化侧边栏导航"""
        # 以下 interface 已废弃，仅保留初始化（部分功能被 Timing 界面复用），不注册到侧边栏
        self.addSubInterface(self.editorInterface, FIF.PLAY, "打轴")
        self.addSubInterface(self.exportInterface, FIF.SHARE, "导出")
        self.addSubInterface(self.singerInterface, FIF.PEOPLE, "演唱者")

        # 底部
        self.addSubInterface(
            self.settingInterface,
            FIF.SETTING,
            "设置",
            position=NavigationItemPosition.BOTTOM,
        )

        # 默认打轴页面
        self.switchTo(self.editorInterface)

    # ==================== 标签页切换 ====================

    def switchTo(self, interface):
        """切换标签页"""
        # 重置目标页面的 y 坐标，防止动画被打断时 widget 残留偏移，导致下次动画越来越快
        interface.move(interface.x(), 0)
        # 切换到打轴界面时暂停位置拉取，避免 60fps 刷新与切换动画竞争导致控件抖动
        if hasattr(self, "editorInterface") and interface is self.editorInterface:
            self.editorInterface.pause_poll_for_page_animation()
        # 切换到设置界面时从磁盘重新加载配置
        if hasattr(self, "settingInterface") and interface is self.settingInterface:
            self.settingInterface.reload_from_disk()
        # 切换到导出界面时同步默认格式
        if hasattr(self, "exportInterface") and interface is self.exportInterface:
            self.exportInterface._sync_default_format()
        self._current_interface = interface
        super().switchTo(interface)

    # ==================== 项目操作 ====================

    def _on_project_created(self, project: Project, audio_path: str = ""):
        """项目创建完成"""
        self._store.load_project(project, audio_path=audio_path if audio_path else None)

        # 自动加载主页选择的音频
        if audio_path:
            self.editorInterface.load_audio(audio_path)

        self.switchTo(self.editorInterface)

        InfoBar.success(
            title="项目创建成功",
            content=f"共 {len(project.sentences)} 行歌词",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _on_project_opened(self, project: Project, file_path: str = ""):
        """项目打开完成"""
        self._store.load_project(project, save_path=file_path if file_path else None)
        self.switchTo(self.editorInterface)

        if file_path:
            self._apply_project_extras(file_path)

        InfoBar.success(
            title="项目打开成功",
            content=f"共 {len(project.sentences)} 行歌词",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _apply_project_extras(self, file_path: str) -> None:
        """读取并应用 .sug 的附加字段（nicokara_tags、media_path）。"""
        from pathlib import Path

        from strange_uta_game.backend.infrastructure.audio.video_converter import (
            is_video_file,
        )
        from strange_uta_game.backend.infrastructure.persistence.sug_io import (
            SugProjectParser,
        )
        from strange_uta_game.frontend.settings.app_settings import AppSettings

        extras = SugProjectParser.load_extras(file_path)
        if not extras:
            return

        nicokara_tags = extras.get("nicokara_tags")
        if nicokara_tags:
            try:
                settings = AppSettings()
                settings.set("nicokara_tags", nicokara_tags)
                settings.save()
            except Exception:
                pass

        media_path = extras.get("media_path", "")
        if not media_path:
            return

        if not Path(media_path).exists():
            InfoBar.warning(
                title="媒体文件未找到",
                content=f"上次关联的媒体文件不存在：{Path(media_path).name}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return

        self._store.restore_media_path(media_path)

        if is_video_file(media_path):
            self.editorInterface._file_loader._load_video_as_audio(media_path)
        else:
            self.editorInterface.load_audio(media_path)

    def _update_title(self):
        """刷新窗口标题栏：拼接存档文件名与保存状态。"""
        from pathlib import Path
        project = self._store.project if self._store else None
        if not project:
            self.setWindowTitle("StrangeUtaGame - 歌词打轴工具 Bilibili@不会说话的呆轩cc")
            return
        save_path = self._store.save_path if self._store else None
        if save_path and not self._store.is_temp_save_path(save_path):
            name = Path(save_path).name  # 如 "mysong.sug"
        else:
            name = "未命名"
        dirty_mark = "[未保存]" if (self._store and self._store.dirty) else ""
        self.setWindowTitle(f"StrangeUtaGame - {name}{dirty_mark} //Bilibili@不会说话的呆轩cc")

    def _on_data_changed(self, change_type: str):
        """响应 store 的数据变更 — 同步非 UI 组件。"""
        if change_type == "project":
            project = self._store.project
            self._command_manager.clear()
            if project:
                self._timing_service.set_project(project)
        elif change_type == "settings":
            # 同步打轴偏移到 TimingService
            settings = self.settingInterface.get_settings()
            offset_ms = settings.get("timing.tag_offset_ms", -230)
            self._timing_service.set_timing_offset(offset_ms)
            # 同步自动保存配置到 ProjectStore
            self._apply_auto_save_settings()
            # 按"高质量音频变速"开关切换引擎（仅在实际变化时重建+重载）
            self._apply_audio_engine_setting()
        elif change_type in ("lyrics", "checkpoints"):
            # 歌词/轴点变更后重建全局 checkpoint 列表
            self._timing_service.rebuild_global_checkpoints()
        elif change_type == "audio":
            # 音频路径变更 → 全局加载到 editor
            # 幂等守卫：editor 已加载相同路径时跳过，避免
            # Editor.load_audio → store.set_audio_path → emit("audio")
            # → MainWindow → Editor.load_audio 的重入回环导致 UI 卡死。
            audio_path = self._store.audio_path
            if audio_path and getattr(self.editorInterface, "_audio_file_path", None) != audio_path:
                self.editorInterface.load_audio(audio_path)
        self._update_title()

    def _on_command_state_changed(self) -> None:
        if self._store:
            self._store.mark_dirty()

    # ==================== 音频引擎选择 ====================

    def _hq_speed_enabled(self) -> bool:
        """读取"启用高质量音频变速"设置（默认开启）。

        优先用 settingInterface 的实时值（反映用户刚改的设置）；启动期
        settingInterface 尚未创建时回退到磁盘 AppSettings。
        """
        try:
            setting_iface = getattr(self, "settingInterface", None)
            if setting_iface is not None:
                return bool(setting_iface.get_settings().get("audio.hq_speed_change", True))
            from strange_uta_game.frontend.settings.app_settings import AppSettings

            return bool(AppSettings().get("audio.hq_speed_change", True))
        except Exception:
            return True

    def _make_audio_engine(self):
        """按设置创建音频引擎。"""
        return BassTsmEngine() if self._hq_speed_enabled() else BassEngine()

    def _apply_audio_engine_setting(self):
        """设置变更时按"高质量音频变速"开关切换引擎。

        仅在引擎类型实际改变时重建：释放旧引擎、接入新引擎，并重载当前曲目
        （位置重置为 0）。切换是用户在设置里的低频操作，可接受短暂中断。
        """
        desired = BassTsmEngine if self._hq_speed_enabled() else BassEngine
        if isinstance(self._audio_engine, desired):
            return

        editor = getattr(self, "editorInterface", None)
        audio_path = getattr(editor, "_audio_file_path", None) if editor else None

        new_engine = desired()
        self._timing_service.swap_audio_engine(new_engine)
        self._audio_engine = new_engine
        # 仅换引擎实例（swap_audio_engine）只迁移了位置/渲染回调，编辑器侧的其它
        # 接线（服务回调、焦点/居中信号、preview 引擎引用）不会自动跟到新引擎，
        # 表现为切换后回调丢失、跳转不准、数据未重新初始化。这里重走一遍完整接线
        # （set_timing_service 已做成幂等）并完整重载当前曲目，等价于一次干净的加载。
        if editor is not None:
            editor.set_timing_service(self._timing_service)
            # 重载当前曲目到新引擎（清空守卫与路径以放行重载）
            if audio_path:
                editor._audio_loading = False
                editor._audio_file_path = None
                editor.load_audio(audio_path)

        InfoBar.success(
            title="音频引擎已切换",
            content=("已启用高质量变速（离线预渲染）" if desired is BassTsmEngine
                     else "已关闭高质量变速（实时变速，可能爆音）"),
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=3000, parent=self,
        )

    # ==================== 自动保存配置 ====================

    def _apply_auto_save_settings(self):
        """从 AppSettings 读取自动保存配置并应用到 ProjectStore。"""
        settings = self.settingInterface.get_settings()
        enabled = settings.get("auto_save.enabled", True)
        interval = settings.get("auto_save.interval_minutes", 5)
        self._store.set_periodic_save_config(enabled, interval)

    # ==================== 闪退恢复 ====================

    def _check_crash_recovery(self):
        """启动时检查是否有未命名项目的闪退恢复文件。"""
        if not ProjectStore.has_crash_recovery():
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("恢复未保存的项目")
        msg.setText("检测到上次异常退出时的未保存项目数据。\n是否加载恢复？")
        btn_yes = msg.addButton("是", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("否", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_yes)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is btn_yes:
            project = ProjectStore.load_crash_recovery()
            if project:
                self._store.load_project(project)
                self.switchTo(self.editorInterface)
                InfoBar.success(
                    title="恢复成功",
                    content=f"已恢复 {len(project.sentences)} 行歌词",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
                # 恢复后删除临时文件
                ProjectStore.delete_crash_recovery()
            else:
                InfoBar.error(
                    title="恢复失败",
                    content="无法读取恢复文件，文件可能已损坏",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
                ProjectStore.delete_crash_recovery()
        else:
            # 用户拒绝恢复 → 删除临时文件
            ProjectStore.delete_crash_recovery()

    # ==================== 自动更新检查 ====================

    def _check_for_app_update(self) -> None:
        """启动时的轻量自动更新检查。

        实际逻辑全部委托给 ``strange_uta_game.updater``；任何异常均吞掉并仅写日志，
        以确保更新模块的故障不会影响主程序使用。
        """
        try:
            from strange_uta_game.updater.settings import UpdaterSettings
            from strange_uta_game.updater.worker import UpdateChecker

            settings = UpdaterSettings.load(self.settingInterface.get_settings())
            if not (settings.enabled and settings.check_on_startup):
                return

            # 启动期检查：受 ``min_check_interval_hours`` 防抖限制
            self._update_checker = UpdateChecker(settings, manual=False, parent=self)
            self._update_checker.finished.connect(self._on_startup_update_check)
            self._update_checker.start()
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "启动更新检查失败，已忽略", exc_info=True
            )

    def _on_startup_update_check(self, result_obj: object) -> None:
        """处理启动期 UpdateChecker 的回调。"""
        try:
            from strange_uta_game.__version__ import __version__
            from strange_uta_game.updater.settings import UpdaterSettings
            from strange_uta_game.updater.sources import SOURCE_LABELS
            from strange_uta_game.updater.ui.update_dialog import UpdateAvailableDialog
            from strange_uta_game.updater import installer as upd_installer

            result = result_obj  # type: ignore[assignment]
            if not getattr(result, "ok", False) or not getattr(result, "has_update", False):
                return
            release = getattr(result, "release", None)
            if release is None:
                return

            # 用户曾经点击「跳过此版本」 → 静默忽略
            settings = UpdaterSettings.load(self.settingInterface.get_settings())
            if settings.skipped_version and settings.skipped_version == release.version:
                return

            # 记录最近一次发现的远端版本（仅用于以后扩展，例如"侧栏红点"）
            settings.last_seen_version = release.version
            try:
                settings.save(self.settingInterface.get_settings())
            except Exception:
                pass

            primary_source = getattr(result, "primary_source", "")
            source_label = SOURCE_LABELS.get(primary_source, "") if primary_source else ""

            dlg = UpdateAvailableDialog(
                release,
                local_version=__version__,
                primary_source_label=source_label,
                all_releases=getattr(result, "all_releases", None) or [],
                parent=self,
            )
            accepted = dlg.exec()
            choice = dlg.user_choice

            if choice == "skip":
                settings.skipped_version = release.version
                try:
                    settings.save(self.settingInterface.get_settings())
                except Exception:
                    pass
                return
            if not accepted or choice == "later":
                return

            # 用户确认更新 → 启动 Updater.exe 并退出
            if not upd_installer.is_updater_available():
                InfoBar.warning(
                    title="更新器未就绪",
                    content="未找到 Updater.exe。请到 GitHub 手动下载完整安装包。",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                    parent=self,
                )
                return

            from strange_uta_game.updater.proxy import resolve_proxy
            info, _ = resolve_proxy(settings.proxy_mode, settings.proxy_manual_url)
            proxy_url = info.url if info and info.is_valid else ""

            plan = upd_installer.LaunchPlan(
                app_dir=upd_installer.find_app_dir(),
                app_exe_name=upd_installer.find_app_exe_name(),
                target_version=release.version,
                target_tag=release.tag,
                asset_name=result.primary_asset_name,
                download_urls=list(result.download_candidates),
                proxy_url=proxy_url,
            )

            # launch_updater 内部会调用 _update_updater_from_remote 发起 HTTP 请求，
            # 同步调用会冻结 UI；改为在后台线程中执行（与 update_card.py 保持一致）。
            from strange_uta_game.updater.ui.update_card import _LaunchUpdaterWorker

            InfoBar.info(
                title="正在准备更新",
                content="正在获取最新更新器，请稍候…",
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                position=InfoBarPosition.TOP,
                duration=30000,
                parent=self,
            )

            worker = _LaunchUpdaterWorker(plan, parent=None)
            self._startup_update_launch_worker = worker  # 防 GC

            def _on_launch_done(launch_result: object) -> None:
                from strange_uta_game.updater import installer as _inst
                lr: _inst.LaunchResult = launch_result  # type: ignore[assignment]
                if not lr.launched:
                    InfoBar.error(
                        title="启动 Updater 失败",
                        content=lr.reason or "未知错误",
                        orient=Qt.Orientation.Horizontal,
                        isClosable=True,
                        position=InfoBarPosition.TOP,
                        duration=6000,
                        parent=self,
                    )
                    return

                InfoBar.success(
                    title="更新已启动",
                    content="即将退出应用，由 Updater 完成替换并自动重启…",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3500,
                    parent=self,
                )
                # 用强制退出而非 QApplication.quit()
                QTimer.singleShot(1200, self.request_force_quit)

            worker.done.connect(_on_launch_done, Qt.ConnectionType.QueuedConnection)
            worker.start()
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "处理启动更新回调时异常，已忽略", exc_info=True
            )

    def _schedule_network_dict_auto_update(self) -> None:
        """启动期网络词典自动更新调度（独立于应用版本检查）。

        由 ``__init__`` 中 ``QTimer.singleShot(3000, ...)`` 触发；HTTP 拉取
        放在 daemon 线程，绝不阻塞 UI。是否真的拉取由
        ``AppSettings.maybe_auto_update_network_dictionary`` 内部根据
        ``network_dictionary.auto_update.{enabled, interval_value, interval_unit}``
        与 ``last_auto_update_at`` 判断。
        """
        import threading

        def _worker() -> None:
            try:
                from strange_uta_game.frontend.settings.app_settings import AppSettings
                AppSettings().maybe_auto_update_network_dictionary()
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "网络词典自动更新失败，已忽略", exc_info=True
                )

        try:
            threading.Thread(
                target=_worker, daemon=True, name="net-dict-auto-update"
            ).start()
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "网络词典自动更新调度失败，已忽略", exc_info=True
            )

    # ==================== 启动时打开项目 ====================

    def open_initial_project(self, file_path: str) -> None:
        """启动时通过命令行参数打开 .sug 项目文件（异步）。

        委托给 editorInterface._file_loader.load_project，与其他加载路径使用同一函数。
        若 file_loader 尚未就绪（timing_service 未初始化），延迟 100ms 重试。
        """
        from pathlib import Path

        if not Path(file_path).is_file():
            InfoBar.error(
                title="无法打开文件",
                content=f"文件不存在: {file_path}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return

        file_loader = getattr(self.editorInterface, "_file_loader", None)
        if file_loader is None or file_loader._timing_service is None:
            QTimer.singleShot(100, lambda: self.open_initial_project(file_path))
            return

        def _on_success(project: Project, loaded_path: str) -> None:
            self.switchTo(self.editorInterface)
            InfoBar.success(
                title="项目打开成功",
                content=f"共 {len(project.sentences)} 行歌词",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

        file_loader.load_project(file_path, check_unsaved=False, on_success=_on_success)

    # ==================== 窗口事件 ====================

    def _on_save_project(self):
        """从任意页面触发保存"""
        self._on_global_save()

    def _on_global_save(self):
        """全局 Ctrl+S 保存（异步）"""
        if not self._store.project:
            InfoBar.warning(
                title="无项目",
                content="请先创建或打开项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        if self._store.save_path and not self._store.is_temp_save_path():
            self._async_save(self._store.save_path)
        else:
            suggested = self._store.suggested_save_path(".sug")
            path, _ = QFileDialog.getSaveFileName(
                self,
                "保存项目",
                suggested,
                "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)",
            )
            if not path:
                return
            if not path.endswith(".sug"):
                path += ".sug"

            # 另存为前先清理旧的 untitled 临时文件
            old_temp = self._store.get_temp_path()
            try:
                if old_temp.exists():
                    old_temp.unlink()
            except Exception:
                pass

            # 登记新的工作目录到 config
            self._store.set_working_dir(path)

            self._async_save(path)

    def _async_save(self, file_path: str):
        """异步保存项目"""
        from copy import deepcopy
        from strange_uta_game.frontend.workers import ProjectSaveWorker

        # 在主线程创建深拷贝，避免保存过程中 UI 修改 project
        project_copy = deepcopy(self._store.project)
        # extras 在主线程读取，保证线程安全
        nicokara_tags = self._store._get_nicokara_tags_for_save()
        media_path = self._store.get_saveable_media_path()

        self._save_thread = QThread(self)
        self._save_worker = ProjectSaveWorker(
            project_copy, file_path,
            nicokara_tags=nicokara_tags,
            media_path=media_path,
        )
        self._save_worker.moveToThread(self._save_thread)

        # 连接信号
        self._save_thread.started.connect(self._save_worker.run)
        self._save_worker.finished.connect(lambda path: self._on_save_success(path))
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.finished.connect(self._cleanup_save_thread)
        self._save_worker.error.connect(self._cleanup_save_thread)

        # 启动线程
        self._save_thread.start()

    def _on_save_success(self, file_path: str) -> None:
        """保存成功回调"""
        old_path = self._store._save_path
        self._store._save_path = file_path
        self._store._dirty = False

        if old_path and old_path != file_path:
            self._store._cleanup_temp_for_path(old_path)
        self._store.cleanup_temp_files()

        InfoBar.success(
            title="保存成功",
            content=file_path,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )
        self._update_title()

    def _on_save_error(self, error_msg: str) -> None:
        """保存失败回调"""
        InfoBar.error(
            title="保存失败",
            content=error_msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _cleanup_save_thread(self) -> None:
        """清理保存线程"""
        if self._save_thread:
            self._save_thread.quit()
            self._save_thread.wait()
            self._save_thread = None
        if self._save_worker:
            self._save_worker.deleteLater()
            self._save_worker = None

    def closeEvent(self, e):
        """关闭窗口时检查未保存变更并退出。

        ``self._force_quitting=True`` 时由 :meth:`request_force_quit` 设置，
        表示当前流程是 updater 触发的硬退出 —— 不弹"未保存"对话框，改为
        把脏数据兜底写到临时文件（next 启动会触发"闪退恢复"机制），然后立刻退出。
        """
        # 退出前确保窗口大小/最大化状态已落盘（防抖定时器可能来不及触发）
        self._save_window_geometry()

        if self._force_quitting:
            # 兜底保存（脏数据写到 .cache/.untitled.sug.temp 或 .项目名.sug.temp）
            try:
                if self._store.dirty:
                    self._store._do_periodic_save()
            except Exception:
                pass
            # 释放编辑器资源（音频引擎等）
            if hasattr(self, "editorInterface"):
                try:
                    self.editorInterface.release_resources()
                except Exception:
                    pass
            self._clear_llm_logs()
            QApplication.quit()
            e.accept()
            return

        if self._store.dirty:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Question)
            msg.setWindowTitle("未保存的更改")
            msg.setText("项目有未保存的更改，是否在退出前保存？")
            save_btn = msg.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
            discard_btn = msg.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(save_btn)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is save_btn:
                self._on_save_project()
                # 保存后清理临时文件
                self._store.cleanup_temp_files()
            elif clicked is discard_btn:
                # 用户主动放弃保存 → 删除临时文件
                self._store.cleanup_temp_files()
            else:
                # 取消或关闭对话框
                e.ignore()
                return
        else:
            # 无脏数据，正常退出 → 清理临时文件
            self._store.cleanup_temp_files()

        # 释放编辑器资源
        if hasattr(self, "editorInterface"):
            self.editorInterface.release_resources()
        self._clear_llm_logs()
        QApplication.quit()
        e.accept()

    @staticmethod
    def _clear_llm_logs() -> None:
        """退出时清除会话级 LLM 请求日志。"""
        try:
            from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
                clear_llm_logs,
            )
            clear_llm_logs()
        except Exception:
            pass

    def request_force_quit(self) -> None:
        """立即退出主程序（由 updater 流程调用）。

        步骤：

        1. 置位 ``_force_quitting`` 标志（影响 :meth:`closeEvent` 行为）；
        2. 调 :meth:`close` 触发 closeEvent —— 由于 ``_force_quitting=True``，会
           bypass "未保存更改"对话框，自动把脏数据兜底写到 .cache 下的临时文件
           （主程序下次启动会触发"闪退恢复"流程）；
        3. closeEvent 内部已经 `QApplication.quit()`；
        4. 兜底：调度一个 250ms 后的 ``os._exit(0)`` 硬退出，防止 Qt 事件循环
           因为残留 QThread / Modal 未处理事件而拒绝退出 —— 那会导致 Updater
           备份 ``_internal`` 时拿不到写权限。
        """
        import os as _os
        self._force_quitting = True
        try:
            self.close()
        except Exception:
            pass
        # 给 Qt 一点时间走完 close 流程；超时强制退出
        QTimer.singleShot(250, lambda: _os._exit(0))
