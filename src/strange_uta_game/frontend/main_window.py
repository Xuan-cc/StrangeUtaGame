"""主窗口。

应用主容器，使用 PyQt-Fluent-Widgets 的 MSFluentWindow。
参考 March7thAssistant 的 UI 架构。
"""

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QIcon, QAction, QKeySequence, QShortcut
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
from qfluentwidgets import InfoBar, InfoBarPosition

from typing import Optional

from strange_uta_game.backend.application import CommandManager, TimingService
from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.audio import SoundDeviceEngine
from strange_uta_game.frontend.project_store import ProjectStore


class MainWindow(MSFluentWindow):
    """主窗口 - MSFluentWindow 侧边栏导航架构"""

    def __init__(self):
        super().__init__()

        self._audio_engine = SoundDeviceEngine()
        self._command_manager = CommandManager()
        self._timing_service = TimingService(self._audio_engine, self._command_manager)
        self._store = ProjectStore(self)

        # 跟踪当前界面（用于 switchTo 自动应用修改，必须在 _init_navigation 之前）
        self._current_interface = None

        self._init_window()
        self._init_interfaces()
        self._init_navigation()

        # 中央响应：store 的 project 变更 → 同步 timing_service 等
        self._store.data_changed.connect(self._on_data_changed)

        # 全局 Ctrl+S 保存快捷键
        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self._on_global_save)

        # 初始化自动保存配置
        self._apply_auto_save_settings()

        # 延迟检查闪退恢复（等 UI 显示完毕后再弹窗）
        QTimer.singleShot(500, self._check_crash_recovery)

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
        setTheme(Theme.LIGHT, lazy=True)

        self.setWindowTitle("StrangeUtaGame - 歌词打轴工具")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        # 设置窗口图标（左上角）
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

    def _init_interfaces(self):
        """初始化所有子界面"""
        from .home.home_interface import HomeInterface
        from .editor.timing_interface import EditorInterface
        from .export.export_interface import ExportInterface
        from .settings.singer_interface import SingerManagerInterface
        from .editor.fulltext_interface import RubyInterface
        from .settings.settings_interface import SettingsInterface
        from .editor.line_interface import EditInterface
        from .online.online_interface import OnlineQueryInterface

        self.homeInterface = HomeInterface(self)
        self.homeInterface.setObjectName("homeInterface")
        self.homeInterface.project_created.connect(self._on_project_created)
        self.homeInterface.project_opened.connect(self._on_project_opened)
        self.homeInterface.project_save_requested.connect(self._on_save_project)

        self.editorInterface = EditorInterface(self)
        self.editorInterface.setObjectName("editorInterface")
        self.editorInterface.set_timing_service(self._timing_service)

        self.exportInterface = ExportInterface(self)
        self.exportInterface.setObjectName("exportInterface")

        self.singerInterface = SingerManagerInterface(self)
        self.singerInterface.setObjectName("singerInterface")

        self.rubyInterface = RubyInterface(self)
        self.rubyInterface.setObjectName("rubyInterface")

        self.settingInterface = SettingsInterface(self)
        self.settingInterface.setObjectName("settingInterface")

        self.editViewInterface = EditInterface(self)
        self.editViewInterface.setObjectName("editViewInterface")

        self.onlineInterface = OnlineQueryInterface(self)
        self.onlineInterface.setObjectName("onlineInterface")

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
        self.addSubInterface(self.homeInterface, FIF.HOME, "主页")
        self.addSubInterface(self.editorInterface, FIF.PLAY, "打轴")
        self.addSubInterface(self.editViewInterface, FIF.EDIT, "行编辑")
        self.addSubInterface(self.exportInterface, FIF.SHARE, "导出")
        self.addSubInterface(self.singerInterface, FIF.PEOPLE, "演唱者")
        self.addSubInterface(self.rubyInterface, FIF.FONT, "全文本编辑")
        self.addSubInterface(self.onlineInterface, FIF.GLOBE, "在线查询")

        # 底部
        self.addSubInterface(
            self.settingInterface,
            FIF.SETTING,
            "设置",
            position=NavigationItemPosition.BOTTOM,
        )

        # 默认主页
        self.switchTo(self.homeInterface)

    # ==================== 标签页切换 ====================

    def switchTo(self, interface):
        """切换标签页，离开全文本编辑界面时自动应用修改"""
        if (
            hasattr(self, "rubyInterface")
            and self._current_interface is self.rubyInterface
            and interface is not self.rubyInterface
            and self.rubyInterface.is_dirty()
        ):
            self.rubyInterface._on_apply_changes()
        # 切换到设置界面时从磁盘重新加载配置（用户可能通过其他途径修改了配置）
        if hasattr(self, "settingInterface") and interface is self.settingInterface:
            self.settingInterface.reload_from_disk()
        # 从打轴界面切换到行编辑界面时，自动跳转到当前行
        if (
            hasattr(self, "editorInterface")
            and hasattr(self, "editViewInterface")
            and self._current_interface is self.editorInterface
            and interface is self.editViewInterface
        ):
            line_idx = self.editorInterface._current_line_idx
            self.editViewInterface.scroll_to_line(line_idx)
        # #1：从打轴界面切换到全文本编辑界面时，将输入光标跳转到对应字符
        if (
            hasattr(self, "editorInterface")
            and hasattr(self, "rubyInterface")
            and self._current_interface is self.editorInterface
            and interface is self.rubyInterface
        ):
            line_idx = self.editorInterface._current_line_idx
            char_idx = 0
            preview = getattr(self.editorInterface, "preview", None)
            if preview is not None:
                char_idx = max(0, int(getattr(preview, "_current_char_idx", 0) or 0))
            # 注意：rubyInterface 在切换"进入"时会调用 _refresh_display 重置文本，
            # 因此需要在 super().switchTo 之后再定位光标。
            self._pending_ruby_jump = (line_idx, char_idx)
        else:
            self._pending_ruby_jump = None
        self._current_interface = interface
        super().switchTo(interface)
        # #1：切到 rubyInterface 之后再定位光标（保证 QPlainTextEdit 已显示）
        pending = getattr(self, "_pending_ruby_jump", None)
        if pending is not None and interface is getattr(self, "rubyInterface", None):
            line_idx, char_idx = pending
            try:
                self.rubyInterface.scroll_to_line(line_idx, char_idx)
            except Exception:
                pass
            self._pending_ruby_jump = None

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

        InfoBar.success(
            title="项目打开成功",
            content=f"共 {len(project.sentences)} 行歌词",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _on_data_changed(self, change_type: str):
        """响应 store 的数据变更 — 同步非 UI 组件。"""
        if change_type == "project":
            project = self._store.project
            self._command_manager.clear()
            if project:
                self._timing_service.set_project(project)
            if project and project.metadata and project.metadata.title:
                self.setWindowTitle(f"StrangeUtaGame - {project.metadata.title}")
            else:
                self.setWindowTitle("StrangeUtaGame - 歌词打轴工具")
        elif change_type == "settings":
            # 同步打轴偏移到 TimingService
            settings = self.settingInterface.get_settings()
            offset_ms = settings.get("timing.tag_offset_ms", 0)
            self._timing_service.set_timing_offset(offset_ms)
            # 同步自动保存配置到 ProjectStore
            self._apply_auto_save_settings()
        elif change_type in ("lyrics", "checkpoints"):
            # 歌词/轴点变更后重建全局 checkpoint 列表
            self._timing_service._rebuild_global_checkpoints()
        elif change_type == "audio":
            # 音频路径变更 → 全局加载到 editor
            # 幂等守卫：editor 已加载相同路径时跳过，避免
            # Editor.load_audio → store.set_audio_path → emit("audio")
            # → MainWindow → Editor.load_audio 的重入回环导致 UI 卡死。
            audio_path = self._store.audio_path
            if audio_path and getattr(self.editorInterface, "_audio_file_path", None) != audio_path:
                self.editorInterface.load_audio(audio_path)

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

        reply = QMessageBox.question(
            self,
            "恢复未保存的项目",
            "检测到上次异常退出时的未保存项目数据。\n是否加载恢复？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.Yes:
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

    # ==================== 窗口事件 ====================

    def _on_save_project(self):
        """从任意页面触发保存"""
        self._on_global_save()

    def _on_global_save(self):
        """全局 Ctrl+S 保存"""
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

        if self._store.save_path:
            success = self._store.save()
            if success:
                # 手动保存成功 → 清理临时文件
                self._store.cleanup_temp_files()
                InfoBar.success(
                    title="保存成功",
                    content=self._store.save_path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=2000,
                    parent=self,
                )
            else:
                InfoBar.error(
                    title="保存失败",
                    content="无法保存到 " + (self._store.save_path or ""),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
        else:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "保存项目",
                "",
                "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)",
            )
            if not path:
                return
            if not path.endswith(".sug"):
                path += ".sug"

            # 另存为前先清理旧的 untitled 临时文件
            old_temp = self._store.get_temp_path()
            if self._store.save(path):
                # 保存成功 → 清理旧临时文件
                try:
                    if old_temp.exists():
                        old_temp.unlink()
                except Exception:
                    pass
                InfoBar.success(
                    title="保存成功",
                    content=path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=2000,
                    parent=self,
                )
            else:
                InfoBar.error(
                    title="保存失败",
                    content="无法保存到 " + path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )

    def closeEvent(self, e):
        """关闭窗口时检查未保存变更并退出"""
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
        QApplication.quit()
        e.accept()
